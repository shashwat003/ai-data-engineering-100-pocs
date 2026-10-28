# Results (measured by `python -m landing_zone_guardrails.main`; SCP semantics SIMULATED, Terraform applied to a LOCAL moto organisation)

- lint problems: 0 []
- regression matrix: 28/28 expectations hold

## Which cases each guardrail protects (cases that flip when the policy is removed)

| policy | cases that flip | n |
|---|---|---|
| deny-leave-org | prod-leave-organisation | 1 |
| deny-root-user | member-root-user | 1 |
| protect-security-services | prod-stop-cloudtrail | 1 |
| region-lock | action-case-insensitive, prod-read-wrong-region, regulated-glue-us-east | 3 |
| regulated-allowlist | regulated-ec2-not-allowlisted, regulated-lambda-not-allowlisted | 2 |
| require-s3-encryption | prod-disable-public-access-block, prod-put-without-sse, regulated-put-without-sse | 3 |
| sandbox-cost-guard | sandbox-instance-type-missing-fails-closed, sandbox-large-instance, sandbox-rds-cluster, sandbox-sagemaker-endpoint | 4 |

## Terraform against local moto

- status: RAN against local moto, not real AWS
- policies_created: ['deny-leave-org', 'deny-root-user', 'protect-security-services', 'region-lock', 'regulated-allowlist', 'require-s3-encryption', 'sandbox-cost-guard']
- content_matches_files: True
- attachments_match_org_json_after_apply: False
- matrix_on_actual_state_after_apply: 26/28
- allowlist_gap_cases_after_apply: ['regulated-ec2-not-allowlisted', 'regulated-lambda-not-allowlisted']
- reconciled_ous: ['regulated']
- attachments_match_org_json_after_reconcile: True
- matrix_on_actual_state_after_reconcile: 28/28
- clean_replan_exit_code: 0
- drift_plan_exit_code_after_console_edit: 2
- plan_exit_code_after_reapply: 0
- config_rules_declared: ['CLOUD_TRAIL_ENABLED', 'ENCRYPTED_VOLUMES', 'S3_BUCKET_PUBLIC_READ_PROHIBITED', 'S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED']
- required_config_rules_present: True
