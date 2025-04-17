#!/usr/bin/env python3
import os
import aws_cdk as cdk
from trusted_spoke.trusted_spoke_stack import TrustedSpokeStack

app = cdk.App()
untrusted_account_id = "746669204818"  # Replace with your actual untrusted account ID

TrustedSpokeStack(app, "TrustedSpokeStack",untrusted_account_id=untrusted_account_id,
        env={
        'account': os.environ.get('CDK_DEFAULT_ACCOUNT', '746669204818'),
        'region': os.environ.get('CDK_DEFAULT_REGION', 'il-central-1')
    }              
    )

app.synth()

