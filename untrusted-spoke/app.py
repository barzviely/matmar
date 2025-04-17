#!/usr/bin/env python3
import os

import aws_cdk as cdk

from untrusted_spoke.untrusted_spoke_stack import UntrustedSpokeStack


app = cdk.App()
UntrustedSpokeStack(app, "UntrustedSpokeStack",
        env={
        'account': os.environ.get('CDK_DEFAULT_ACCOUNT', '506294024506'),
        'region': os.environ.get('CDK_DEFAULT_REGION', 'il-central-1')
    }   
    )

app.synth()
