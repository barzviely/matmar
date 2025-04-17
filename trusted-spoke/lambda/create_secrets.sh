#!/bin/bash
# This script creates an AWS Secrets Manager secret for dual on-prem destinations with certificate authentication

# Set your secret values here
OP1_HOST="metzuda-hostname.example.com"
OP1_PORT="22"
OP1_USERNAME="metzuda-user"
# Load the private key content from a file or paste it directly here as a base64-encoded string
OP1_PRIVATE_KEY=$(cat /path/to/metzuda_private_key | base64 -w 0)

OP2_HOST="marganit-hostname.example.com"
OP2_PORT="22"
OP2_USERNAME="marganit-user"
# Load the private key content from a file or paste it directly here as a base64-encoded string
OP2_PRIVATE_KEY=$(cat /path/to/marganit_private_key | base64 -w 0)

# Create the secret in JSON format with certificate-based authentication
SECRET_STRING="{\"op1_host\":\"$OP1_HOST\",\"op1_port\":\"$OP1_PORT\",\"op1_username\":\"$OP1_USERNAME\",\"op1_private_key\":$(echo "$OP1_PRIVATE_KEY" | base64 -d | jq -s -R .),\"op2_host\":\"$OP2_HOST\",\"op2_port\":\"$OP2_PORT\",\"op2_username\":\"$OP2_USERNAME\",\"op2_private_key\":$(echo "$OP2_PRIVATE_KEY" | base64 -d | jq -s -R .)}"

# Create the secret in AWS Secrets Manager
aws secretsmanager create-secret \
    --name onprem-credentials \
    --description "Credentials for on-premises SFTP destinations" \
    --secret-string "$SECRET_STRING"

echo "Secret created successfully in AWS Secrets Manager"

# To update an existing secret, uncomment and use the following command:
# aws secretsmanager update-secret \
#     --secret-id onprem-credentials \
#     --secret-string "$SECRET_STRING"