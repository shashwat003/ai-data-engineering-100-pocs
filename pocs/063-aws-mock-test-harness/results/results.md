# Results (measured by `python -m aws_mock_harness.main`; moto server, no Docker, no LocalStack)

- server startup: 0.57 s; reset+create+put per simulated test: 50.4 ms
- state cleared by reset: True; request to real AWS blocked by guard: True

## What the mock supports (one round-trip probe per service)

| probe | works | detail |
|---|---|---|
| s3 | yes |  |
| sqs | yes |  |
| sns | yes |  |
| dynamodb | yes |  |
| kinesis | yes |  |
| firehose | yes |  |
| events | yes |  |
| secretsmanager | yes |  |
| ssm | yes |  |
| kms | yes |  |
| stepfunctions | yes |  |
| lambda_create | yes |  |
| lambda_invoke | NO | AssertionError:  |
| athena | yes |  |
| glue | yes |  |
| organizations | yes |  |
