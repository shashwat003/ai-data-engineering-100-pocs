# Results (POC 059: REAL dbt-core + DuckDB, local COPY semantics, Redshift-dialect syntax checks, offline Terraform plan; NOTHING run on Redshift or AWS)
Expected injected defects: {'copy_rejects': 3, 'sql_rejects': 5, 'late_updates': 30, 'late_new_orders': 6}. COPY loaded 1233 rows and rejected 3 unparsable ones (MAXERROR 5), logging each reject in `raw.load_errors`.

## COPY semantics

| check | result |
|---|---|
| MAXERROR 1 on a batch with 3 bad rows | CopyError: orders/b002: 3 rejected rows exceed MAXERROR 1; nothing loaded; orders rows after: 250 |
| replaying every file | 0 loaded, 8 skipped (ledger) |
| rejected cells | orders/b002 amount='12,50'; orders/b002 amount='12,50'; orders/b002 amount='12,50' |

## dbt build and reconciliation

dbt build: {'success': True, 'nodes': 28, 'by_status': {'success': 10, 'pass': 18}}. Independent recomputation from raw (DuckDB ASOF join, different SQL): valid orders 998 vs fct_orders 998; rejected 5 vs 5; orders with a wrong point-in-time segment: **0**; revenue by segment matches: True; revenue by current segment matches: True -> **consistent**.

## Business rule: segment AS OF THE ORDER DATE (point-in-time)

`dim_customer_history` keeps every segment/country change (SCD2): 150 customers, 162 history rows, 12 changes. `fct_orders.segment` is the segment valid on the order date; 70 orders belong to customers whose segment has since changed, so `revenue_by_segment` (as of order date) and `revenue_by_current_segment` (today's segmentation) deliberately differ. The earlier version of this POC carried the segment as of LOAD time, which neither rule asks for (42 of 992 rows disagreed with a full refresh).

### Incremental build vs one full refresh, by arrival order (all columns incl. segment and country)

| arrival order | dbt builds | interim builds stopped by data tests | final build passed | equals full refresh | wrong point-in-time segments |
|---|---|---|---|---|---|
| in order | 4 | 0 | True | True | 0 |
| customers one batch late | 5 | 1 | True | True | 0 |
| batches reversed | 4 | 0 | True | True | 0 |
| random interleaving | 4 | 0 | True | True | 0 |
A stopped interim build is the data-quality gate working: orders that arrive before their customers fail the not-null/relationships tests, and the next build repairs them.

## Redshift dialect checks (syntax only)

| compiled model | findings |
|---|---|
| daily_revenue | none |
| dim_customer | none |
| dim_customer_history | none |
| fct_orders | none |
| revenue_by_current_segment | none |
| revenue_by_segment | none |
| rejected_orders | none |
| stg_customer_snapshots | none |
| stg_orders | none |
| stg_orders_ranked | none |

Redshift dbt config findings (dist/sort/strategy): none. Negative examples: `select try_cast(a as int) from t` -> TRY_CAST does not exist in Redshift; `select a from` -> does not parse as Redshift SQL: Expected table name but got .

COPY statement the production loader would run: `COPY raw.orders FROM 's3://elt-landing-123456789012/b001/orders.csv' IAM_ROLE 'arn:aws:iam::123456789012:role/redshift-copy-role' FORMAT AS CSV IGNOREHEADER 1 MAXERROR 5 TIMEFORMAT 'auto' TRUNCATECOLUMNS`

## Terraform plan checks (offline, mock credentials, never applied)

| variant | findings |
|---|---|
| default | none |
| publicly accessible | workgroup is publicly accessible |
| oversized base capacity | base capacity 128 RPU exceeds 32 |

## Modelled monthly compute (ASSUMED $0.375/RPU-hour, 60 s minimum)

| workload | RPU-hours | compute USD |
|---|---|---|
| 8 RPU, 4 runs/day x 5 min | 80.000 | 30.000 |
| 32 RPU, 4 runs/day x 5 min | 320.000 | 120.000 |
