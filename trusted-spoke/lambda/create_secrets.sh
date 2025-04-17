#!/bin/bash
# This script creates an AWS Secrets Manager secret for dual on-prem destinations with certificate authentication

# Set your secret values here
OP1_HOST="matmar.op.sky320.internal"
OP1_PORT="443"

OP2_HOST="matmar2.op.sky320.internal"
OP2_PORT="443"

# Create the secret in JSON format with certificate-based authentication
SECRET_STRING="{\"op1_host\":\"$OP1_HOST\",\"op1_port\":\"$OP1_PORT\",\"op2_host\":\"$OP2_HOST\",\"op2_port\":\"$OP2_PORT\"}"

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