# Results (measured by `python -m glue_athena_iceberg.main`; real PyIceberg 0.12 on a local SQLite catalog; no Athena/Spark engine)

- **small_appends**: {'snapshots': 30, 'data_files': 30, 'rows': 1200}
- **partition_pruning**: {'files_planned_for_one_day': 3, 'files_total': 30, 'rows_returned': 120}
- **compaction**: {'data_files_after': 10, 'same_checksum': True, 'old_snapshot_rows': 1200}
- **schema_evolution**: {'columns': ['order_id', 'customer_id', 'ts', 'total_amount', 'quantity', 'currency'], 'old_rows_currency_null': 1200, 'new_rows_currency_eur': 5, 'old_rows_total_amount_non_null': True}
- **partition_evolution**: {'specs': 2, 'default_spec_fields': ['ts_month']}
- **delete**: {'rows_for_customer_before': 60, 'rows_after': 0, 'still_visible_in_old_snapshot': 60}
- **expire_snapshots**: {'snapshots_left': 1, 'files_on_disk_before': 51, 'unreferenced_files_on_disk_after': 40, 'deleted_data_removed_physically': True}
- **schema_enforcement**: {'nullable column given nulls-allowed schema': 'ValueError', 'missing required column': 'ValueError'}
- **concurrent_writers**: both commits succeeded
- **final_rows**: 1151
- **glue_athena_on_mock**: {'status': 'RAN against local moto, not real AWS', 'planned_workgroup': {'enforced': True, 'scan_cutoff_bytes': 10737418240, 'result_encryption': 'SSE_S3', 'engine': 'Athena engine version 3'}, 'replan_exit_code': 0, 'glue_table_params': {'table_type': 'ICEBERG'}, 'glue_table_location': 's3://demo-iceberg-lake/warehouse/lake/orders', 'results_bucket_encrypted': True, 'lake_public_access_blocked': True}
