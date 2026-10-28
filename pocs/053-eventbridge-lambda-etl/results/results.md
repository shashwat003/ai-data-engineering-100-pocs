# Results (measured by `python -m eventbridge_lambda_etl.main`; local moto account; Lambda invocation and S3->EventBridge emission SIMULATED)

- **messages_processed**: 2
- **curated_keys**: ['curated/day1.parquet', 'curated/day2.parquet']
- **quarantine_keys**: ['quarantine/day2.csv']
- **day2_rows_curated**: 10
- **day2_schema**: {'order_id': 'int64', 'customer_id': 'int64', 'amount': 'double', 'currency': 'string', 'order_date': 'date32[day]'}
- **day2_quarantine_reasons**: ['negative amount', 'unknown currency', 'unparseable value']
- **duplicate_results**: ['duplicate']
- **transient_statuses**: ['retry', 'ok']
- **poison_attempts**: 6
- **dlq_messages**: 2
- **main_queue_empty_at_end**: True
- **curated_keys_final**: ['curated/day1.parquet', 'curated/day2.parquet', 'curated/flaky.parquet']
- **reconciliation**: {'in': 37, 'ok': 33, 'rejected': 4, 'balanced': True}
