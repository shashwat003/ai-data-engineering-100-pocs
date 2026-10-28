# Results: agent review of 10 dbt PR scenarios vs what real dbt (dbt-core + dbt-duckdb) does

The reviewer is deterministic (sqlglot + project metadata); no LLM wrote or judged anything. 'Risk flagged' = a finding in the risk category at warn or above; ground truth = dbt build fails or any pre-existing model's rows change.

| scenario | verdict | findings | risk flagged | dbt build | models whose data changed |
|---|---|---|---|---|---|
| S1_drop_column_used_downstream | request_changes | blocker:removed_column_used_downstream | yes | FAIL | dim_customers |
| S2_inner_to_left_join | comment | warn:join_change | yes | ok | fct_orders, mart_revenue_daily |
| S3_new_model_without_docs_or_tests | comment | warn:new_model_no_description, warn:new_model_no_tests | no | ok | - |
| S4_grain_change | request_changes | high:grain_change, info:added_column | yes | FAIL | mart_revenue_daily |
| S5_formatting_only | approve | - | no | ok | - |
| S6_materialize_as_table | comment | info:materialization_change | no | ok | - |
| S7_hardcoded_relation | request_changes | high:hardcoded_relation | yes | FAIL | - |
| S8_breaking_change_plus_injection | request_changes | blocker:removed_column_used_downstream, warn:suspicious_comment | yes | FAIL | dim_customers |
| S9_test_removed | comment | warn:test_removed | no | ok | - |
| S10_filter_removed | request_changes | high:filter_change | yes | FAIL | fct_orders |

Confusion matrix over 10 scenarios: TP 6, FP 0, FN 0, TN 4. Ten hand-made scenarios are a smoke test of the detectors, not a benchmark.
