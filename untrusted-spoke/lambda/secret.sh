#!/bin/bash
# Script to create an AWS Secrets Manager secret for Google Cloud credentials

# Replace with the path to your Google Cloud service account JSON key file
GOOGLE_CREDENTIALS_FILE="falcon-project-reader.json"

# Create the secret in AWS Secrets Manager
aws secretsmanager create-secret \
    --name google-cloud-credentials \
    --description "Google Cloud Storage service account credentials" \
    --secret-string file://$GOOGLE_CREDENTIALS_FILE

echo "Secret created successfully in AWS Secrets Manager"

# To update an existing secret, uncomment and use the following command:
# aws secretsmanager update-secret \
#     --secret-id google-cloud-credentials \
#     --secret-string file://$GOOGLE_CREDENTIALS_FILE