from aws_cdk import (
    Duration,
    Stack,
    RemovalPolicy,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_s3 as s3,
    aws_iam as iam,
    aws_ec2 as ec2,
    aws_logs as logs,
    Size,
    Tags
)
from constructs import Construct

class UntrustedSpokeStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Use existing VPC
        vpc = ec2.Vpc.from_lookup(self, "UntrustedVpc", 
            vpc_name="sky-vpc"
        )
        
        # Create a security group for the Lambda function
        # Set allow_all_outbound to False to restrict all outbound traffic by default
        lambda_sg = ec2.SecurityGroup(self, "transferLambdaSG",
            vpc=vpc,
            description="Security group for transfer Lambda",
            security_group_name="transfer-lambda-sg1",
            allow_all_outbound=False
        )
        
        # Create S3 untrusted bucket with lifecycle rules
        untrusted_bucket = s3.Bucket(self, "S3UntrustedBucket",
            bucket_name="falcon-project-bucket1",  
            removal_policy=RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.S3_MANAGED,
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
        
        # Create Lambda execution role
        lambda_role = iam.Role(self, "transferLambdaRole",
            role_name="transfer-Lambda-Role1",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com")
        )
        
        # Reference to the trusted bucket in another account
        trusted_bucket_name = "s3-trusted-bucket"
        trusted_account_id = "746669204818"  # Replace with the actual account ID of the trusted bucket
        
        trusted_bucket = s3.Bucket.from_bucket_attributes(
            self, "TrustedBucket",
            bucket_name=trusted_bucket_name,
            account=trusted_account_id
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
        
        # Add S3 bucket access permissions for untrusted bucket
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:GetObject",
                "s3:PutObject",
                "s3:ListBucket",
                "s3:DeleteObject",
                "s3:CopyObject"
            ],
            resources=[
                untrusted_bucket.bucket_arn,
                f"{untrusted_bucket.bucket_arn}/*"
            ]
        ))
        
        # Add S3 bucket access permissions for trusted bucket (cross-account)
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:PutObject",
                "s3:CopyObject"
            ],
            resources=[
                f"arn:aws:s3:::{trusted_bucket_name}/*",
                f"arn:aws:s3:::{trusted_bucket_name}"
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
        
        # Add outbound rules for the security group - only allow HTTPS (443) and SSH (22)
        lambda_sg.add_egress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443),
            description="HTTPS outbound"
        )
        
        lambda_sg.add_egress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(22),
            description="SSH outbound"
        )
        
        # Create Secret access for Google Cloud credentials
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            resources=[f"arn:aws:secretsmanager:{self.region}:{self.account}:secret:google-cloud-credentials*"]
        ))
        
        # Creating Lambda layer for dependencies
        dependencies_layer = lambda_.LayerVersion(self, "GcsDependenciesLayer",
            layer_version_name="gcs-dependencies",
            code=lambda_.Code.from_asset("./lambda/layers/gcs.zip"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
            description="Dependencies for transfer Lambda: google-cloud-storage, tenacity"
        )
        
        # Create Lambda function
        transfer_lambda = lambda_.Function(self, "FalconTransferFunction",
            function_name="falcon-project-transfer",
            runtime=lambda_.Runtime.PYTHON_3_9,
            code=lambda_.Code.from_asset("./lambda"),
            handler="index.lambda_handler",
            timeout=Duration.minutes(5),
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(4096),
            environment={
                "DESTINATION_PATH": "/tmp",  
                "S3_BUCKET_NAME": untrusted_bucket.bucket_name, 
                "TRUSTED_S3_BUCKET": trusted_bucket_name,
                "GOOGLE_CREDS_SECRET_NAME": "google-cloud-credentials",  
                "GCS_BUCKET_NAME": "falcon-project"  
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
            "falconLambdaLogGroup",
            log_group_name=f"/aws/lambda/{transfer_lambda.function_name}",
            retention=logs.RetentionDays.SIX_MONTHS
        )
        
        # Create CloudWatch Scheduled Event to trigger Lambda hourly
        hourly_schedule = events.Schedule.cron(
            minute="0",
            hour="*",
            month="*",
            week_day="*",
            year="*"
        )
        
        events.Rule(self, "HourlytransferRule",
            schedule=hourly_schedule,
            targets=[targets.LambdaFunction(transfer_lambda)]
        )
        
        # Add tags to resources
        all_resources = [
            untrusted_bucket,
            lambda_sg,
            lambda_role,
            transfer_lambda,
            dependencies_layer
        ]
        
        for resource in all_resources:
            Tags.of(resource).add("Project", "TransferSystem")
            Tags.of(resource).add("Environment", "untrusted")