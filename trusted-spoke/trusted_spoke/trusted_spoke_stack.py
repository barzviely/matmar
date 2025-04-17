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
    Tags
)
from constructs import Construct

class TrustedSpokeStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Use existing VPC
        vpc = ec2.Vpc.from_lookup(self, "TrustedVpc", 
            vpc_name="Spoke-VPC-Trusted"
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
            bucket_name="s3-trusted-bucket",
            removal_policy=RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.S3_MANAGED,
            event_bridge_enabled=True,
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
        
        # Create SQS Queue for notifications
        notification_queue = sqs.Queue(self, "NotificationQueue",
            queue_name="transfer-notification-queue",
            visibility_timeout=Duration.seconds(300)
        )
        
        # Set up S3 event notifications to SQS
        trusted_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.SqsDestination(notification_queue)
        )
        
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
        
        # Create Secret access for on-prem credentials
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
            code=lambda_.Code.from_asset("./lambda/transfer"),
            handler="index.lambda_handler",
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "TRUSTED_BUCKET": trusted_bucket.bucket_name,
                "ONPREM_SECRET_NAME": "onprem-credentials"
            },
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
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
        
        # Add tags to resources
        all_resources = [
            trusted_bucket,
            notification_queue,
            lambda_sg,
            lambda_role,
            transfer_lambda,
            dependencies_layer
        ]
        
        for resource in all_resources:
            Tags.of(resource).add("Project", "TransferSystem")
            Tags.of(resource).add("Environment", "trusted")