import os
import json
import boto3
import uuid
import time
import shutil
from datetime import datetime, timezone
from urllib.parse import unquote_plus
import requests
import paramiko
import io
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def get_current_path():
    """Get current UTC time folder path: yyyy/MM/DD/HH"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y/%m/%d/%H")

def get_onprem_credentials():
    """Get on-premises credentials from AWS Secrets Manager"""
    try:
        secret_client = boto3.client('secretsmanager')
        secret_response = secret_client.get_secret_value(
            SecretId=os.environ['ONPREM_SECRET_NAME']
        )
        return json.loads(secret_response['SecretString'])
    except Exception as e:
        logger.error(f"Error getting on-premises credentials: {str(e)}")
        raise

def send_metrics(cloudwatch, metrics_data):
    """Send custom metrics to CloudWatch"""
    try:
        cloudwatch.put_metric_data(
            Namespace='MOD/FileTransfer',
            MetricData=metrics_data
        )
    except Exception as e:
        logger.error(f"Error sending metrics: {str(e)}")

def transfer_file_to_onprem(file_path, file_content, credentials, location):
    """Transfer file to specific on-premises system using SFTP with network-based trust"""
    temp_file_path = f"/tmp/{uuid.uuid4()}"
    
    try:
        # Write content to temporary file
        with open(temp_file_path, 'wb') as f:
            f.write(file_content)
        
        # Get location-specific credentials
        host = credentials[f'{location}_host']
        port = int(credentials[f'{location}_port'])
        
        # Create SSH client
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # Connect using network-based trust (no username/password or keys)
        client.connect(
            hostname=host,
            port=port,
            # No username, password, or private key needed
            # The connection is authenticated based on network trust
            timeout=30
        )
        
        # Create SFTP client from SSH connection
        sftp = client.open_sftp()
        
        # Ensure remote directory exists
        remote_dir = os.path.dirname(file_path)
        create_remote_directory(sftp, remote_dir)
        
        # Upload file
        sftp.put(temp_file_path, file_path)
        
        logger.info(f"Successfully transferred file to on-premises {location}: {file_path}")
        sftp.close()
        client.close()
        
        return True
    except Exception as e:
        logger.error(f"Error transferring file to on-premises {location}: {str(e)}")
        return False
    finally:
        # Clean up temporary files
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

def create_remote_directory(sftp, remote_dir):
    """Create remote directory structure if it doesn't exist"""
    if remote_dir == '/':
        return
    
    try:
        sftp.stat(remote_dir)
    except IOError:
        parent_dir = os.path.dirname(remote_dir)
        create_remote_directory(sftp, parent_dir)
        sftp.mkdir(remote_dir)

def process_s3_event(record, s3_client, cloudwatch, credentials):
    """Process a single S3 event record"""
    start_time = time.time()
    bucket = record['s3']['bucket']['name']
    key = unquote_plus(record['s3']['object']['key'])
    
    logger.info(f"Processing file {key} from bucket {bucket}")
    
    try:
        # Get the object from S3
        response = s3_client.get_object(Bucket=bucket, Key=key)
        file_content = response['Body'].read()
        file_size = response['ContentLength']
        
        # Determine remote file path
        file_name = os.path.basename(key)
        remote_path = f"/From_AWS/{get_current_path()}/{file_name}"
        
        # Track locations status
        locations_status = {}
        
        # Transfer file to both on-premises locations
        # First location: op1 (Metzuda) - first priority
        locations_status['op1'] = transfer_file_to_onprem(remote_path, file_content, credentials, 'op1')
        
        # Second location: op2 (Marganit) - second priority
        locations_status['op2'] = transfer_file_to_onprem(remote_path, file_content, credentials, 'op2')
        
        # Transfer is successful only if both transfers are successful
        overall_success = all(locations_status.values())
        
        # Track metrics
        duration = time.time() - start_time
        metrics = [
            {
                'MetricName': 'ExecutionTime',
                'Value': duration,
                'Unit': 'Seconds',
                'Dimensions': [{'Name': 'FileName', 'Value': file_name}]
            },
            {
                'MetricName': 'FileSize',
                'Value': file_size,
                'Unit': 'Bytes',
                'Dimensions': [{'Name': 'FileName', 'Value': file_name}]
            },
            {
                'MetricName': 'TransferSuccess',
                'Value': 1 if overall_success else 0,
                'Unit': 'Count',
                'Dimensions': [{'Name': 'FileName', 'Value': file_name}]
            }
        ]
        
        send_metrics(cloudwatch, metrics)
        
        logger.info(f"File: {file_name}, Size: {file_size} bytes, Duration: {duration:.2f}s, Success: {overall_success}")
        logger.info(f"Location status - op1: {locations_status['op1']}, op2: {locations_status['op2']}")
        
        if overall_success:
            logger.info(f"Successfully processed file {key} to both destinations")
            return True
        else:
            failed_destinations = [loc for loc, status in locations_status.items() if not status]
            logger.error(f"Failed to process file {key} to destinations: {', '.join(failed_destinations)}")
            return False
            
    except Exception as e:
        logger.error(f"Error processing file {key}: {str(e)}")
        return False

def lambda_handler(event, context):
    """Main Lambda handler"""
    start_time = time.time()
    logger.info(f"Processing event: {json.dumps(event)}")
    
    # Initialize clients
    s3_client = boto3.client('s3')
    cloudwatch = boto3.client('cloudwatch')
    
    # Get on-premises credentials
    credentials = get_onprem_credentials()
    
    successful_files = 0
    total_files = 0
    
    # Process S3 events from SQS
    if 'Records' in event:
        for record in event['Records']:
            # Check if this is an SQS message containing S3 event
            if 'body' in record:
                s3_event = json.loads(record['body'])
                
                if 'Records' in s3_event:
                    for s3_record in s3_event['Records']:
                        if s3_record.get('eventSource') == 'aws:s3' and s3_record.get('eventName', '').startswith('ObjectCreated'):
                            total_files += 1
                            if process_s3_event(s3_record, s3_client, cloudwatch, credentials):
                                successful_files += 1
    
    # Process scheduled event (hourly)
    else:
        # Get list of files from S3 bucket to process
        bucket_name = os.environ['TRUSTED_BUCKET']
        current_path = get_current_path()
        
        try:
            response = s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=current_path
            )
            
            if 'Contents' in response:
                for obj in response['Contents']:
                    total_files += 1
                    record = {
                        's3': {
                            'bucket': {'name': bucket_name},
                            'object': {'key': obj['Key']}
                        }
                    }
                    
                    if process_s3_event(record, s3_client, cloudwatch, credentials):
                        successful_files += 1
        except Exception as e:
            logger.error(f"Error listing objects: {str(e)}")
    
    # Track overall execution time
    end_time = time.time()
    duration = end_time - start_time
    
    # Send batch metrics
    batch_metrics = [
        {
            'MetricName': 'BatchExecutionTime',
            'Value': duration,
            'Unit': 'Seconds'
        },
        {
            'MetricName': 'BatchFilesProcessed',
            'Value': total_files,
            'Unit': 'Count'
        },
        {
            'MetricName': 'BatchFilesSuccessful',
            'Value': successful_files,
            'Unit': 'Count'
        }
    ]
    
    if total_files > 0:
        batch_metrics.append({
            'MetricName': 'BatchSuccessRate',
            'Value': (successful_files / total_files) * 100,
            'Unit': 'Percent'
        })
    
    send_metrics(cloudwatch, batch_metrics)
    
    logger.info(f"Processing complete. Total files: {total_files}, Successful: {successful_files}, Duration: {duration:.2f}s")
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'message': 'Processing complete',
            'total_files': total_files,
            'successful_files': successful_files,
            'processing_time_seconds': duration
        })
    }