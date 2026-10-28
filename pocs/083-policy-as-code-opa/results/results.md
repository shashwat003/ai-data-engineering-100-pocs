# Results: RBAC+ABAC data access policy evaluated by real OPA 1.2.0

16/16 named scenarios match expectations. Differential test: 1500 seeded random requests (33 allowed), Rego vs independent Python reference: 0 mismatches. Missing-attribute check (each attribute removed from an otherwise-allowed request): attributes that still allowed access = none.

| scenario | decision | reasons | vs expected |
|---|---|---|---|
| analyst reads internal data | allow | - | ok |
| analyst cannot write | deny | no_role_grants_action | ok |
| engineer writes own dept | allow | - | ok |
| engineer writes other dept | deny | write_outside_own_department | ok |
| steward grants own dept | allow | - | ok |
| PII without approved purpose | deny | pii_requires_purpose | ok |
| PII with approved purpose | allow | - | ok |
| restricted data above clearance | deny | clearance_too_low | ok |
| confidential without MFA | deny | mfa_required | ok |
| cross-region read denied | deny | region_mismatch | ok |
| cross-region read with approval | allow | - | ok |
| inactive (departed) user | deny | user_inactive | ok |
| auditor reads | allow | - | ok |
| auditor + engineer role cannot write (auditor wins) | deny | auditors_are_read_only | ok |
| unknown classification | deny | clearance_too_low, unknown_classification | ok |
| no roles at all | deny | no_role_grants_action | ok |
