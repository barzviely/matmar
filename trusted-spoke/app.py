#!/usr/bin/env python3
import os
import aws_cdk as cdk
from trusted_spoke.trusted_spoke_stack import TrustedSpokeStack

app = cdk.App()
TrustedSpokeStack(app, "TrustedSpokeStack",
        env={
        'account': os.environ.get('CDK_DEFAULT_ACCOUNT', '506294024506'),
        'region': os.environ.get('CDK_DEFAULT_REGION', 'il-central-1')
    }              
    )

app.synth()
