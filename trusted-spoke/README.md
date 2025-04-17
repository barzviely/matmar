# Trusted Spoke Transfer System

This project implements a secure file transfer system that moves validated files from a trusted S3 bucket to on-premises systems. It's designed as the second stage of a dual-VPC security architecture, where files are first validated in an untrusted environment before being moved to the trusted bucket for secure on-premises delivery.

The system runs on AWS Lambda within a trusted VPC and is triggered both by S3 event notifications. It establishes secure SFTP connections to on-premises endpoints using credentials stored in AWS Secrets Manager, providing reliable and secure file transfer with comprehensive error handling and logging.

## Repository Structure
```
.
├── app.py                          # CDK app entry point
├── cdk.json                        # CDK configuration
├── requirements.txt                # Python dependencies
├── README.md                       # This documentation
├── trusted_spoke/                  # Main module for stacks
│   ├── __init__.py
│   └── trusted_spoke_stack.py      # Trusted Spoke Stack definition
└── lambda/
    ├── index.py                      # Handler for S3 to on-prem transfer
    └── layers/                     # Lambda layers
        └── transfer-dependencies/  # Dependencies for transfer Lambda
            └── python/             # Python packages
                ├── paramiko/       # SFTP client library
                └── requests/       # HTTP client library
```

## Usage Instructions
### Prerequisites
- AWS CLI configured with appropriate credentials
- Python 3.9 or later
- AWS CDK CLI installed (`npm install -g aws-cdk`)
- Existing "Spoke-VPC-Trusted" VPC in AWS trusted account
- Access to the target on-premises systems
- Credentials stored in AWS Secrets Manager

Required Python packages for development:
```
aws-cdk-lib>=2.0.0
constructs>=10.0.0
boto3>=1.20.0
```

### Installation

1. Clone the repository and set up the virtual environment:
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip3 install -r requirements.txt
```

2. Create the Lambda layer with required dependencies:
```bash
mkdir -p lambda/layers/transfer-dependencies/python
cd lambda/layers/transfer-dependencies/python
pip install -t . paramiko requests
cd ../../../..
```

3. Deploy the infrastructure:
```bash
cdk deploy
```

### Quick Start

1. Configure the required secret in AWS Secrets Manager:
   - `onprem-credentials`: JSON containing host, port, username, password for on-premises SFTP server

2. Update environment variables in `trusted_spoke_stack.py` if needed:
```python
environment={
    "TRUSTED_BUCKET": trusted_bucket.bucket_name,
    "ONPREM_ENDPOINT": "your-onprem-endpoint",
    "ONPREM_SECRET_NAME": "onprem-credentials"
}
```

3. Deploy the stack:
```bash
cdk deploy
```

### Monitoring file transfers

Monitor the transfer process using CloudWatch Logs:
```bash
# Check transfer status in CloudWatch Logs
aws logs get-log-events --log-group-name /aws/lambda/s3-to-onprem-transfer
```

## Data Flow
The system processes files through a secure pipeline from trusted S3 to on-premises systems.

```ascii
                ┌───────────────┐
                │ S3 Event      │
                │ Notification  │
                └───────┬───────┘
                        │
                        ▼
┌───────────────┐     ┌─────────────┐ 
│ Trusted S3    │     │ SQS Queue   │    
│ Bucket        │────▶│             │ 
        │                    │           
        │                    │
        │                    ▼
        │            ┌───────────────┐
        └───────────▶│ Lambda        │
                     │ (in Trusted   │
                     │  VPC)         │
                     └───────┬───────┘
                             │
                             ▼
                     ┌───────────────┐
                     │ On-Premises   │
                     │ Systems       │
                     └───────────────┘
```

Component Interactions:
1. New files appear in the trusted S3 bucket (validated by the untrusted process)
2. S3 event notifications are sent to an SQS queue
3. The SQS queue triggers the Lambda function in the trusted VPC
4. Lambda retrieves credentials from Secrets Manager
5. Lambda downloads the file from S3
6. Lambda establishes a secure SFTP connection to on-premises system
7. Lambda transfers the file to the on-premises location
8. A scheduled check runs hourly to catch any missed files

## Infrastructure

Lambda Function:
- `s3-to-onprem-transfer`: Main file transfer function
  - Runtime: Python 3.9
  - Memory: 512MB
  - Timeout: 5 minutes
  - VPC: Private subnet deployment in "Spoke-VPC-Trusted"

S3 Bucket:
- `s3-trusted-bucket`: Trusted storage bucket
  - S3-managed encryption enabled
  - Event notifications configured

SQS Queue:
- `transfer-notification-queue`: Receives S3 event notifications
  - Visibility timeout: 300 seconds

IAM Resources:
- `Transfer-Lambda-Role`: Lambda execution role with permissions for:
  - S3 access
  - SQS message processing
  - Secrets Manager access
  - VPC network interface management
  - CloudWatch logging

Security Group:
- `transfer-lambda-sg`: Lambda security group
  - Outbound: All traffic allowed (can be restricted to specific endpoints)

EventBridge Rule:
- `HourlyTransferRule`: Hourly trigger for Lambda function as backup mechanism

Lambda Layer:
- `transfer-dependencies`: Contains paramiko and requests libraries for SFTP and HTTP connectivity

## Security Considerations

This implementation includes several security best practices:
- Lambda runs in a private subnet within a trusted VPC
- Credentials are stored in AWS Secrets Manager, not hardcoded
- S3 bucket uses server-side encryption
- Lambda has only the minimum permissions needed
- All resources are tagged for proper governance

## Troubleshooting

Common issues and solutions:
- **Lambda times out**: Increase the Lambda timeout or check connectivity to on-premises systems
- **Permission errors**: Verify IAM roles and security group settings
- **Missing files**: Check both event-based and scheduled triggers are working
- **VPC connectivity issues**: Ensure the VPC has proper route tables and NAT gateways configured