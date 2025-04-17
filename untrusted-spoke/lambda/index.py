import os
import json
import boto3
import uuid
import time
import shutil
from datetime import datetime, timezone
from google.cloud import storage
import io
import zipfile
import csv
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List
from tenacity import retry, stop_after_attempt, wait_exponential

def get_current_path():
    """Get current UTC time folder path: yyyy/MM/DD/HH"""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y/%m/%d/%H")

def get_storage_client():
    """Create a Google Cloud Storage client using credentials from AWS Secrets Manager"""
    try:
        # Get credentials from AWS Secrets Manager
        secret_client = boto3.client('secretsmanager')
        secret_response = secret_client.get_secret_value(
            SecretId=os.environ['GOOGLE_CREDS_SECRET_NAME']
        )
        credentials_dict = json.loads(secret_response['SecretString'])
        
        # Create storage client directly from service account info
        return storage.Client.from_service_account_info(credentials_dict)
    except Exception as e:
        print(f"Error setting up Google Storage client: {str(e)}")
        raise

def validate_csv_content(csv_content: str) -> Dict[str, Any]:
    """Validate CSV content using regex patterns"""
    validation_result = {
        "is_valid": False,
        "errors": []
    }

    # Define patterns for meteorological data
    HEADER_PATTERN = r"^(time,)?lat,lon(,(air_pressure|air_temperature|rel_humidity|wind_direction|wind_speed)_[0-9]{0,10}){0,200}(,(cloud_cover|cloud_base|visibility|precipitation)){0,4}$"
    DATA_PATTERN = r"^([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})?,?(-?[0-9]{1,5}\.[0-9]{0,20}),(-?[0-9]{1,5}\.[0-9]{0,20})((,-?[0-9]{1,5}\.[0-9]{0,20})*)$"

    try:
        # Split content into lines
        lines = [line.strip() for line in csv_content.split('\n') if line.strip()]
        if not lines:
            validation_result["errors"].append("EmptyFile: CSV is empty")
            return validation_result

        # Validate header
        header = lines[0]
        if not re.match(HEADER_PATTERN, header):
            validation_result["errors"].append("InvalidHeader: Header format does not match expected pattern")
            return validation_result

        # Validate first and last row
        rows_to_check = [lines[1], lines[-1]] if len(lines) > 2 else lines[1:]
        for i, row in enumerate(rows_to_check):
            if not re.match(DATA_PATTERN, row):
                validation_result["errors"].append(f"InvalidData: Row format does not match expected pattern")
                return validation_result

        validation_result["is_valid"] = True
        return validation_result

    except Exception as e:
        validation_result["errors"].append(f"ValidationError: {str(e)}")
        return validation_result

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    reraise=True
)
def download_from_gcs_with_retry(blob):
    """Download from Google Cloud Storage with retry mechanism"""
    try:
        return blob.download_as_bytes()
    except Exception as e:
        print(f"Download attempt failed: {str(e)}")
        raise

def put_metrics(cloudwatch, namespace: str, metrics_data: List[Dict]):
    """Helper function to send metrics to CloudWatch"""
    try:
        cloudwatch.put_metric_data(Namespace=namespace, MetricData=metrics_data)
    except Exception as e:
        print(f"Error sending metrics: {str(e)}")

def track_file_processing(cloudwatch, file_name: str, file_size: int, duration: float, is_valid: bool, errors: List[str]):
    """Track metrics for single file processing"""
    metrics = [
        {
            'MetricName': 'ProcessingDuration',
            'Value': duration,
            'Unit': 'Seconds',
            'Dimensions': [{'Name': 'FileName', 'Value': file_name}]
        },
        {
            'MetricName': 'FileSize',
            'Value': file_size,
            'Unit': 'Bytes',
            'Dimensions': [{'Name': 'FileName', 'Value': file_name}]
        }
    ]

    if not is_valid:
        metrics.append({
            'MetricName': 'ValidationErrors',
            'Value': len(errors),
            'Unit': 'Count',
            'Dimensions': [{'Name': 'FileName', 'Value': file_name}]
        })

    put_metrics(cloudwatch, 'MOD/FileProcessing', metrics)

def track_batch_processing(cloudwatch, total_files: int, successful_files: int, total_size: int, duration: float):
    """Track metrics for batch processing"""
    metrics = [
        {
            'MetricName': 'ProcessedFiles',
            'Value': total_files,
            'Unit': 'Count'
        },
        {
            'MetricName': 'SuccessfulFiles',
            'Value': successful_files,
            'Unit': 'Count'
        },
        {
            'MetricName': 'TotalSize',
            'Value': total_size,
            'Unit': 'Bytes'
        },
        {
            'MetricName': 'BatchDuration',
            'Value': duration,
            'Unit': 'Seconds'
        }
    ]

    if total_files > 0:
        metrics.append({
            'MetricName': 'SuccessRate',
            'Value': (successful_files / total_files) * 100,
            'Unit': 'Percent'
        })

    put_metrics(cloudwatch, 'MOD/BatchProcessing', metrics)

def process_single_file(blob, s3_client, cloudwatch):
    """Process a single zip file with cleanup and metrics"""
    start_time = time.time()
    temp_dir = os.path.join('/tmp', str(uuid.uuid4()))
    os.makedirs(temp_dir, exist_ok=True)
    
    try:
        # Download zip from Google Cloud Storage
        zip_content = download_from_gcs_with_retry(blob)
        
        # Upload to raw folder in untrusted S3
        raw_key = f"raw/{get_current_path()}/{blob.name}"
        s3_client.put_object(
            Bucket=os.environ['S3_BUCKET_NAME'],
            Key=raw_key,
            Body=zip_content
        )

        # Extract and validate CSV
        temp_zip_path = os.path.join(temp_dir, 'temp.zip')
        with open(temp_zip_path, 'wb') as f:
            f.write(zip_content)
        
        with zipfile.ZipFile(temp_zip_path) as zip_ref:
            csv_filename = zip_ref.namelist()[0]  # Should be only one CSV
            temp_csv_path = os.path.join(temp_dir, csv_filename)
            zip_ref.extract(csv_filename, temp_dir)
            
            with open(temp_csv_path, 'r') as f:
                csv_content = f.read()
            
            # Validate CSV content
            validation_result = validate_csv_content(csv_content)
            
            # Track file metrics
            process_duration = time.time() - start_time
            track_file_processing(
                cloudwatch,
                blob.name,
                blob.size,
                process_duration,
                validation_result["is_valid"],
                validation_result["errors"]
            )
            
            if validation_result["is_valid"]:
                # If valid, copy to trusted S3 bucket
                s3_client.copy_object(
                    Bucket=os.environ['TRUSTED_S3_BUCKET'],
                    Key=f"valid/{get_current_path()}/{blob.name}",
                    CopySource={'Bucket': os.environ['S3_BUCKET_NAME'], 'Key': raw_key},
                    Metadata={
                        'validation_status': 'valid',
                        'process_duration': str(process_duration)
                    },
                    MetadataDirective='REPLACE'
                )
                return True
            else:
                # Create error log
                s3_client.put_object(
                    Bucket=os.environ['S3_BUCKET_NAME'],
                    Key=f"invalid/{get_current_path()}/{blob.name}.errors.json",
                    Body=json.dumps({
                        'validation_result': validation_result,
                        'process_duration': process_duration
                    }, indent=2),
                    Metadata={'validation_status': 'invalid'}
                )
                return False

    except Exception as e:
        print(f"Error processing file {blob.name}: {str(e)}")
        return False
        
    finally:
        # Clean up temporary files
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

def lambda_handler(event, context):
    """Main Lambda handler"""
    batch_start_time = time.time()
    
    try:
        # Initialize clients
        s3_client = boto3.client('s3')
        cloudwatch = boto3.client('cloudwatch')
        
        # Get Google Storage client
        storage_client = get_storage_client()
        
        # Get current folder path
        folder_path = get_current_path()
        
        # Get all zip files from current hour
        gcs_bucket = storage_client.bucket(os.environ['GCS_BUCKET_NAME'])
        blobs = [blob for blob in gcs_bucket.list_blobs(prefix=folder_path) 
                if blob.name.endswith('.zip')]
        
        total_size = sum(blob.size for blob in blobs)
        
        # Process files concurrently
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(
                lambda blob: process_single_file(blob, s3_client, cloudwatch), 
                blobs
            ))
        
        successful_files = sum(1 for r in results if r)
        
        # Track batch metrics
        batch_duration = time.time() - batch_start_time
        track_batch_processing(
            cloudwatch,
            len(blobs),
            successful_files,
            total_size,
            batch_duration
        )
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Processing complete',
                'total_files': len(blobs),
                'valid_files': successful_files,
                'invalid_files': len(blobs) - successful_files,
                'folder_processed': folder_path,
                'total_size': total_size,
                'duration_seconds': batch_duration
            })
        }
        
    except Exception as e:
        print(f"Lambda execution failed: {str(e)}")
        # Track metrics even in case of failure
        if 'cloudwatch' in locals() and 'blobs' in locals():
            track_batch_processing(
                cloudwatch,
                len(blobs),
                0,  # No successful files in case of failure
                0,  # Don't report size in case of failure
                time.time() - batch_start_time
            )
        raise