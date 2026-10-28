# Results (measured by `python -m data_product_template.main`)

- dbt build of scaffolded product (clean seed): ok=True
- dbt build with defective seeds (must fail): {'negative_amount': False, 'bad_status': False}
- generated schema.yml out of date after a contract edit is detected: True

## SLO scenarios

| scenario | all SLOs met | violated | error budget left |
|---|---|---|---|
| healthy | True | - | 1.500 |
| stale (no load for 3 days) | False | freshness_hours | 1.400 |
| volume drop (-70%) | False | volume_deviation_pct | 1.500 |
| availability burn (4 failed loads) | False | availability | -2.500 |
| incomplete (20 null amounts) | False | completeness, validity | 1.500 |
| invalid (20 bad statuses) | False | validity | 1.500 |

## Contract change gate

| change | required bump | actual bump | verdict |
|---|---|---|---|
| drop column + patch bump | major | patch | REJECT |
| tighten amount max + minor bump | major | minor | REJECT |
| tighten amount max + major bump | major | major | accept |
| doc-only change + patch bump | patch | patch | accept |
