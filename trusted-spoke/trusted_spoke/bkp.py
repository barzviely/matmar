from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_iam as iam,
    aws_ec2 as ec2,
    aws_sqs as sqs,
    aws_logs as logs,
    Size,
    Tags,
    CustomResource,
    custom_resources as cr
)
from constructs import Construct

class TrustedSpokeStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, untrusted_account_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Use existing VPC
        vpc = ec2.Vpc.from_lookup(self, "TrustedVpc", 
            vpc_name="sky-vpc"
        )
        
        # Create a security group for the Lambda function
        lambda_sg = ec2.SecurityGroup(self, "TransferLambdaSG",
            vpc=vpc,
            description="Security group for Transfer Lambda",
            security_group_name="transfer-lambda-sg",
            allow_all_outbound=True
        )
        
        # Create S3 trusted bucket with lifecycle rules
        trusted_bucket = s3.Bucket(self, "S3TrustedBucket",
            bucket_name="s3-trusted-bucket-testsssss",
            removal_policy=RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.S3_MANAGED,
            event_bridge_enabled=True,
            versioned=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.DEEP_ARCHIVE,
                            transition_after=Duration.days(30)
                        )
                    ],
                    enabled=True
                )
            ]
        )
        
        # Add cross-account bucket policy to allow access from untrusted account
        trusted_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCrossAccountAccess",
                effect=iam.Effect.ALLOW,
                principals=[iam.AccountPrincipal(untrusted_account_id)],
                actions=[
                    "s3:PutObject", 
                    "s3:GetObject",
                    "s3:ListBucket"
                ],
                resources=[
                    trusted_bucket.bucket_arn,
                    f"{trusted_bucket.bucket_arn}/*"
                ]
            )
        )
        
        # Create SQS Queue for notifications
        notification_queue = sqs.Queue(self, "NotificationQueue",
            queue_name="transfer-notification-queue",
            visibility_timeout=Duration.seconds(300)
        )
        # Add S3 permission to SQS queue policy (IMPORTANT FIX)
        notification_queue.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowS3ToSendMessages",
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("s3.amazonaws.com")],
                actions=["sqs:SendMessage"],
                resources=[notification_queue.queue_arn],
                conditions={
                    "ArnLike": {
                        "aws:SourceArn": trusted_bucket.bucket_arn
                    }
                }
            )
        )        
        # REMOVED: This line was causing the error
        # trusted_bucket.add_event_notification(
        #    s3.EventType.OBJECT_CREATED,
        #    s3n.SqsDestination(notification_queue)
        # )
        
        # Create Lambda execution role
        lambda_role = iam.Role(self, "TransferLambdaRole",
            role_name="Transfer-Lambda-Role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
        )
        
        # Add CloudWatch Logs permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            resources=["arn:aws:logs:*:*:*"]
        ))
        
        # Add CloudWatch Metrics permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "cloudwatch:PutMetricData"
            ],
            resources=["*"]
        ))
        
        # Add S3 bucket access permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetObject",
                "s3:PutObject",
                "s3:ListBucket",
                "s3:DeleteObject"
            ],
            resources=[
                trusted_bucket.bucket_arn,
                f"{trusted_bucket.bucket_arn}/*"
            ]
        ))
        
        # Add VPC access permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:CreateNetworkInterface",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DeleteNetworkInterface"
            ],
            resources=["*"]
        ))
        
        # Add SQS permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "sqs:ReceiveMessage",
                "sqs:DeleteMessage",
                "sqs:GetQueueAttributes"
            ],
            resources=[notification_queue.queue_arn]
        ))
        
        # Create Secret access for on-prem credentials (fixing the name)
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:onprem-credentials*"]
        ))
        
        # Creating Lambda layer for dependencies
        dependencies_layer = lambda_.LayerVersion(self, "TransferDependenciesLayer",
            layer_version_name="transfer-dependencies",
            code=lambda_.Code.from_asset("./lambda/layers/transfer-dependencies/python/transfer-dependencies.zip"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
            description="Dependencies for Transfer Lambda: requests, paramiko"
        )
        
        # Create Lambda function
        transfer_lambda = lambda_.Function(self, "TransferFunction",
            function_name="s3-transfer",
            runtime=lambda_.Runtime.PYTHON_3_9,
            code=lambda_.Code.from_asset("./lambda"),
            handler="index.lambda_handler",
            timeout=Duration.minutes(5),
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(4096),
            environment={
                "TRUSTED_BUCKET": trusted_bucket.bucket_name,
                "ONPREM_SECRET_NAME": "onprem-credentials"
            },
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_NAT
            ),
            security_groups=[lambda_sg],
            role=lambda_role,
            layers=[dependencies_layer]
        )
        
        # Configure CloudWatch Logs retention
        logs.LogGroup(
            self, 
            "TransferLambdaLogGroup",
            log_group_name=f"/aws/lambda/{transfer_lambda.function_name}",
            retention=logs.RetentionDays.SIX_MONTHS
        )
        
        # Set up SQS as event source for Lambda
        transfer_lambda.add_event_source_mapping("SQSTrigger",
            event_source_arn=notification_queue.queue_arn,
            batch_size=10,
            max_batching_window=Duration.minutes(5)
        )
        
        # ADDED SECTION START - Custom Resource for S3 Bucket Notification
        # Create security group for notification handler
        notification_handler_sg = ec2.SecurityGroup(self, "NotificationHandlerSG",
            vpc=vpc,
            description="Security group for S3 notification handler Lambda",
            allow_all_outbound=True
        )
        
        # Create IAM role for notification handler
        notification_handler_role = iam.Role(self, "NotificationHandlerRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
        )
        
        # Add required permissions for the notification handler
        notification_handler_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents"
            ],
            resources=["arn:aws:logs:*:*:*"]
        ))
        
        # Add S3 bucket notification permissions
        notification_handler_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetBucketNotification",
                "s3:PutBucketNotification"
            ],
            resources=[trusted_bucket.bucket_arn]
        ))
        
        # Add VPC access permissions
        notification_handler_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "ec2:CreateNetworkInterface",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DeleteNetworkInterface"
            ],
            resources=["*"]
        ))
        
        # Create notification handler Lambda function
        notification_handler = lambda_.Function(self, "S3NotificationHandler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            code=lambda_.Code.from_inline("""
import json
import boto3
import cfnresponse
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

def handler(event, context):
    logger.info('Received event: %s', json.dumps(event))
    
    # Extract properties from the event
    properties = event['ResourceProperties']
    bucket_name = properties['BucketName']
    queue_url = properties['QueueUrl']
    queue_arn = properties['QueueArn']
    events = properties.get('Events', 's3:ObjectCreated:*').split(',')
    
    response_data = {}
    physical_id = f"{bucket_name}-notification-{context.aws_request_id[:8]}"
    
    try:
        s3 = boto3.client('s3')
        
        if event['RequestType'] in ['Create', 'Update']:
            # Get existing notification configuration
            existing_config = s3.get_bucket_notification_configuration(Bucket=bucket_name)
            
            # Remove service configurations we don't want to include
            if 'ResponseMetadata' in existing_config:
                existing_config.pop('ResponseMetadata')
            
            # Prepare the new queue configuration
            queue_config = {
                'QueueArn': queue_arn,
                'Events': events
            }
            
            # Add or update the queue configurations
            queue_configurations = existing_config.get('QueueConfigurations', [])
            
            # Check if a configuration for this queue already exists
            updated = False
            for i, config in enumerate(queue_configurations):
                if config.get('QueueArn') == queue_arn:
                    queue_configurations[i] = queue_config
                    updated = True
                    break
            
            if not updated:
                queue_configurations.append(queue_config)
                
            # Update the full notification configuration
            notification_config = existing_config
            notification_config['QueueConfigurations'] = queue_configurations
            
            # Put the updated notification configuration
            logger.info('Putting bucket notification: %s', json.dumps(notification_config))
            s3.put_bucket_notification_configuration(
                Bucket=bucket_name,
                NotificationConfiguration=notification_config
            )
            
            response_data['Message'] = f"S3 bucket notification configured for {bucket_name}"
            
        elif event['RequestType'] == 'Delete':
            # Get existing notification configuration
            existing_config = s3.get_bucket_notification_configuration(Bucket=bucket_name)
            
            # Remove service configurations we don't want to include
            if 'ResponseMetadata' in existing_config:
                existing_config.pop('ResponseMetadata')
            
            # Remove the queue configuration for this queue
            queue_configurations = existing_config.get('QueueConfigurations', [])
            queue_configurations = [
                config for config in queue_configurations 
                if config.get('QueueArn') != queue_arn
            ]
            
            # Update the full notification configuration
            notification_config = existing_config
            if queue_configurations:
                notification_config['QueueConfigurations'] = queue_configurations
            else:
                notification_config.pop('QueueConfigurations', None)
            
            # Put the updated notification configuration
            logger.info('Putting bucket notification: %s', json.dumps(notification_config))
            s3.put_bucket_notification_configuration(
                Bucket=bucket_name,
                NotificationConfiguration=notification_config
            )
            
            response_data['Message'] = f"S3 bucket notification removed for {bucket_name}"
        
        cfnresponse.send(event, context, cfnresponse.SUCCESS, response_data, physical_id)
    
    except Exception as e:
        logger.error('Error: %s', str(e))
        response_data['Error'] = str(e)
        cfnresponse.send(event, context, cfnresponse.FAILED, response_data, physical_id)
            """),
            handler="index.handler",
            timeout=Duration.seconds(300),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_NAT
            ),
            security_groups=[notification_handler_sg],
            role=notification_handler_role
        )
        
        # Create custom resource provider
        notification_provider = cr.Provider(self, "S3NotificationProvider",
            on_event_handler=notification_handler
        )
        
        # Create custom resource for S3 bucket notification
        s3_notification_custom_resource = CustomResource(self, "S3BucketNotificationCustomResource",
            service_token=notification_provider.service_token,
            properties={
                "BucketName": trusted_bucket.bucket_name,
                "QueueUrl": notification_queue.queue_url,
                "QueueArn": notification_queue.queue_arn,
                "Events": "s3:ObjectCreated:*"
            }
        )
        # ADDED SECTION END
        
        # Add tags to resources
        all_resources = [
            trusted_bucket,
            notification_queue,
            lambda_sg,
            lambda_role,
            transfer_lambda,
            dependencies_layer,
            notification_handler,
            notification_handler_sg,
            notification_handler_role
        ]
        
        for resource in all_resources:
            Tags.of(resource).add("Project", "TransferSystem")
            Tags.of(resource).add("Environment", "trusted")