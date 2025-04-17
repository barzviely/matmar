# Untrusted Spoke Inspection System

This project implements a secure file validation and inspection system that retrieves meteorological data files from Google Cloud Storage, validates them, and transfers valid files to a trusted S3 bucket for further processing. It's designed as the first stage of a dual-VPC security architecture, working in tandem with the Trusted Spoke Transfer System.

The system runs on AWS Lambda within an untrusted VPC and is triggered by scheduled CloudWatch Events. It establishes secure connections to Google Cloud Storage using credentials stored in AWS Secrets Manager, providing reliable file validation with comprehensive error handling, metrics tracking, and logging.

## Repository Structure
```
.
├── app.py                              # CDK app entry point
├── cdk.json                            # CDK configuration
├── requirements.txt                    # Python dependencies
├── README.md                           # This documentation
├── untrusted_spoke/                    # Main module for stacks
│   ├── __init__.py
│   └── untrusted_spoke_stack.py        # Untrusted Spoke Stack definition
└── lambda/
    ├── inspection/                     # Lambda code for inspection
    │   └── index.py                    # Handler for Google Cloud to S3 inspection
    └── layers/                         # Lambda layers
        └── gcs.zip/    # Dependencies for inspection Lambda

```

## Usage Instructions
### Prerequisites
- AWS CLI configured with appropriate credentials
- Python 3.9 or later
- AWS CDK CLI installed (`npm install -g aws-cdk`)
- Existing "Spoke-VPC-Untrusted" VPC in AWS untrusted account
- Access to the Google Cloud Storage bucket
- Google Cloud service account credentials stored in AWS Secrets Manager

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
mkdir -p lambda/layers/inspection-dependencies/python
cd lambda/layers/inspection-dependencies/python
pip install -t . google-cloud-storage tenacity
cd ../../../..
```

3. Deploy the infrastructure:
```bash
cdk deploy
```

### Quick Start

1. Configure the required secret in AWS Secrets Manager:
   - `google-cloud-credentials`: JSON containing Google Cloud service account credentials

2. Update environment variables in `untrusted_spoke_stack.py` if needed:
```python
environment={
    "S3_BUCKET_NAME": untrusted_bucket.bucket_name,
    "TRUSTED_S3_BUCKET": trusted_bucket.bucket_name,
    "GOOGLE_CREDS_SECRET_NAME": "google-cloud-credentials",
    "GCS_BUCKET_NAME": "your-google-cloud-bucket-name"
}
```

3. Deploy the stack:
```bash
cdk deploy
```

### Monitoring file inspection

Monitor the inspection process using CloudWatch Logs and Metrics:
```bash
# Check inspection status in CloudWatch Logs
aws logs get-log-events --log-group-name /aws/lambda/gcs-inspection

# View metrics
aws cloudwatch get-metric-statistics --namespace "MOD/FileProcessing" --metric-name "ProcessingDuration" --start-time 2023-01-01T00:00:00Z --end-time 2023-01-02T00:00:00Z --period 3600 --statistics Average
```

## Data Flow
The system processes files through a secure pipeline from Google Cloud Storage to AWS S3.

```ascii
                ┌───────────────┐
                │ CloudWatch    │
                │ Scheduled     │
                │ Event         │
                └───────┬───────┘
                        │
                        ▼
┌───────────────┐     ┌─────────────┐ 
│ Google Cloud  │     │ Lambda      │    
│ Storage       │────▶│ (Untrusted  │ 
└───────────────┘     │  VPC)       │           
                      └──────┬──────┘
                             │
                             ▼
                    ┌────────────────┐
         ┌─────────┤  Validation    ├─────────┐
         │         └────────────────┘         │
         ▼                                    ▼
┌─────────────────┐                 ┌──────────────────┐
│ Untrusted S3    │                 │ Invalid Files    │
│ Bucket (Valid)  │                 │ S3 Bucket        │
└────────┬────────┘                 └──────────────────┘
         │
         ▼
┌─────────────────┐
│ Trusted S3      │
│ Bucket          │
└─────────────────┘
```

Component Interactions:
1. CloudWatch scheduled event triggers the Lambda function hourly
2. Lambda retrieves Google Cloud credentials from Secrets Manager
3. Lambda connects to Google Cloud Storage and lists files in the current time-based folder
4. Lambda downloads each file and validates it against schema requirements
5. Valid files are stored in the trusted S3 bucket
6. Invalid files are stored in a separate invalid files bucket with error details
7. Metrics are published to CloudWatch for monitoring

## Infrastructure

Lambda Function:
- `gcs-inspection`: Main inspection and validation function
  - Runtime: Python 3.9
  - Memory: 512MB
  - Timeout: 5 minutes
  - VPC: Private subnet deployment in "Spoke-VPC-Untrusted"

S3 Buckets:
- `s3-untrusted-bucket`: Main untrusted storage bucket
  - S3-managed encryption enabled
  - Lifecycle rules to archive after 30 days
- `s3-invalid-files-bucket`: Storage for invalid files
  - Contains error logs and original files
  - Lifecycle rules to archive after 30 days

IAM Resources:
- `Inspection-Lambda-Role`: Lambda execution role with permissions for:
  - S3 access across multiple buckets
  - Secrets Manager access
  - VPC network interface management
  - CloudWatch logging and metrics

Security Group:
- `inspection-lambda-sg`: Lambda security group
  - Outbound: All traffic allowed (for Google Cloud Storage access)

EventBridge Rule:
- `HourlyInspectionRule`: Hourly trigger for Lambda function

Lambda Layer:
- `inspection-dependencies`: Contains google-cloud-storage and tenacity libraries

## Validation Logic

The inspection system validates meteorological data files with the following checks:
- Validates CSV header structure against meteorological data schema
- Validates data rows for proper format and data types
- Checks for presence of required geographical coordinates
- Validates measurements for expected ranges and formats

Files passing validation are moved to the trusted S3 bucket for further processing by the Trusted Spoke Transfer System.

## Security Considerations

This implementation includes several security best practices:
- Lambda runs in a private subnet within an untrusted VPC
- Google Cloud credentials are stored in AWS Secrets Manager, not hardcoded
- S3 buckets use server-side encryption
- Lambda has only the minimum permissions needed
- All resources are tagged for proper governance

## Metrics and Monitoring

The system publishes the following CloudWatch metrics:
- `ProcessingDuration`: Time taken to process individual files
- `FileSize`: Size of each processed file
- `ValidationErrors`: Count of errors for invalid files
- `ProcessedFiles`: Total number of files processed in each batch
- `SuccessfulFiles`: Number of files successfully validated
- `TotalSize`: Total data volume processed
- `BatchDuration`: Total time for each batch run
- `SuccessRate`: Percentage of successfully validated files

## Troubleshooting

Common issues and solutions:
- **Lambda times out**: Check file sizes or increase Lambda timeout
- **Permission errors**: Verify IAM roles and Google Cloud credentials
- **Missing files**: Ensure the Google Cloud Storage path is correct
- **Validation errors**: Check the error logs in the invalid files bucket
- **VPC connectivity issues**: Ensure outbound internet access is available for Google Cloud Storage connections