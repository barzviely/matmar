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
    Tags
)
from constructs import Construct

class UntrustedSpokeStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Use existing VPC
        vpc = ec2.Vpc.from_lookup(self, "UntrustedVpc", 
            vpc_name="Spoke-VPC-Untrusted"
        )
        
        # Create a security group for the Lambda function
        lambda_sg = ec2.SecurityGroup(self, "InspectionLambdaSG",
            vpc=vpc,
            description="Security group for Inspection Lambda",
            security_group_name="inspection-lambda-sg",
            allow_all_outbound=True
        )
        
        # Create S3 untrusted bucket with lifecycle rules
        untrusted_bucket = s3.Bucket(self, "S3UntrustedBucket",
            bucket_name="falcon-project-bucket",  # From the screenshot
            removal_policy=RemovalPolicy.RETAIN,
            encryption=s3.BucketEncryption.S3_MANAGED,
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
        
        # Create S3 invalid files bucket (using prefix on main bucket)
        # Note: We're using the same bucket with different prefixes as implied by the code
        
        # Reference to the trusted bucket (created in another stack)
        trusted_bucket = s3.Bucket.from_bucket_name(
            self, "TrustedBucket", "s3-trusted-bucket"
        )
        
        # Create Lambda execution role
        lambda_role = iam.Role(self, "InspectionLambdaRole",
            role_name="Inspection-Lambda-Role",
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
        
        # Add S3 bucket access permissions for trusted bucket
        lambda_role.add_to_policy(iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=[
                "s3:PutObject",
                "s3:CopyObject"
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
        
        # Add outbound rules for the security group (as shown in the screenshot)
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
        dependencies_layer = lambda_.LayerVersion(self, "InspectionDependenciesLayer",
            layer_version_name="inspection-dependencies",
            code=lambda_.Code.from_asset("./lambda/layers/inspection-dependencies/python/inspection-dependencies.zip"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_9],
            description="Dependencies for Inspection Lambda: google-cloud-storage, tenacity"
        )
        
        # Create Lambda function
        inspection_lambda = lambda_.Function(self, "InspectionFunction",
            function_name="gcs-inspection",
            runtime=lambda_.Runtime.PYTHON_3_9,
            code=lambda_.Code.from_asset("./lambda/inspection"),
            handler="index.lambda_handler",
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "DESTINATION_PATH": "/tmp",  # From the screenshot
                "S3_BUCKET_NAME": "falcon-project-bucket",  # From the screenshot
                "TRUSTED_S3_BUCKET": trusted_bucket.bucket_name,
                "GOOGLE_CREDS_SECRET_NAME": "google-cloud-credentials",  # From the screenshot
                "GCS_BUCKET_NAME": "falcon-project"  # From the screenshot
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
            "InspectionLambdaLogGroup",
            log_group_name=f"/aws/lambda/{inspection_lambda.function_name}",
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
        
        events.Rule(self, "HourlyInspectionRule",
            schedule=hourly_schedule,
            targets=[targets.LambdaFunction(inspection_lambda)]
        )
        
        # Add tags to resources
        all_resources = [
            untrusted_bucket,
            lambda_sg,
            lambda_role,
            inspection_lambda,
            dependencies_layer
        ]
        
        for resource in all_resources:
            Tags.of(resource).add("Project", "FalconTransferSystem")
            Tags.of(resource).add("Environment", "untrusted")