# Results (measured by `python -m fargate_bluegreen_api.main`; offline Terraform plan, no AWS; deployment SIMULATED)

## Linter corpus (findings per fixture; `good_*` must be 0, everything else must be > 0)

**trust**

| fixture | findings |
|---|---|
| bad_any_ref | 1 |
| bad_no_aud | 1 |
| bad_no_conditions | 2 |
| bad_no_sub | 1 |
| bad_other_account_provider | 1 |
| bad_principal_star | 1 |
| bad_pull_request | 1 |
| bad_star | 1 |
| bad_wildcard_owner | 1 |
| bad_wrong_action | 1 |
| bad_wrong_audience | 1 |
| good_branch | 0 |
| good_env | 0 |

**permissions**

| fixture | findings |
|---|---|
| bad_admin | 1 |
| bad_ecr_everywhere | 1 |
| bad_iam_write | 2 |
| bad_passrole_no_condition | 1 |
| bad_passrole_star | 2 |
| bad_service_wildcard | 1 |
| good | 0 |

**workflows**

| fixture | findings |
|---|---|
| bad_no_environment | 1 |
| bad_no_id_token | 1 |
| bad_no_timeout_no_concurrency | 2 |
| bad_pull_request_target | 1 |
| bad_script_injection | 1 |
| bad_static_keys | 3 |
| bad_tag_pins | 2 |
| bad_unneeded_id_token | 1 |
| bad_write_all | 1 |
| good | 0 |

## Blue/green simulation (20 seeds each)

| scenario | completed | rolled back | mean bad requests served by green |
|---|---|---|---|
| healthy release | 20 | 0 | 3.400 |
| green tasks never start (breaker) | 0 | 20 | 0.000 |
| flaky starts (30%), recovers | 15 | 5 | 2.500 |
| green returns 5xx (5%) | 0 | 20 | 17.800 |
| green mildly worse (0.8pp), inside tolerance | 20 | 0 | 25.100 |
| controller crash mid-rollout | 20 | 0 | 3.400 |

## Terraform plan policy checks

- status: planned offline (never applied, no AWS contact)
- **clean**: 0 finding(s)
- **unpinned image**: 1 finding(s): task container app: image not pinned by digest
- **trust any repo of the owner**: 1 finding(s): deploy role trust: statement 0: subject pattern 'repo:example-org/*' is too broad (wildcard beyond the ref/environment suffix)
- **trust has no environment**: 1 finding(s): deploy role trust: statement 0: subject pattern 'repo:example-org/inference-api:*' is too broad (wildcard beyond the ref/environment suffix)
