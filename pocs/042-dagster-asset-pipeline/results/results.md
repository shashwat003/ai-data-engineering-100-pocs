# Results: Dagster partitioned assets with blocking asset checks (real Dagster, in-process)

| partition | run | assets materialized | checks |
|---|---|---|---|
| 2025-03-01 | ok | clean_events, daily_metrics, raw_events | clean_has_no_bad_rows=pass, quarantine_rate_is_low=pass, raw_has_rows=pass |
| 2025-03-02 | ok | clean_events, daily_metrics, raw_events | clean_has_no_bad_rows=pass, quarantine_rate_is_low=pass, raw_has_rows=pass |
| 2025-03-03 | ok | clean_events, daily_metrics, raw_events | clean_has_no_bad_rows=pass, quarantine_rate_is_low=pass, raw_has_rows=pass |
| 2025-03-04 | ok | clean_events, daily_metrics, raw_events | clean_has_no_bad_rows=pass, quarantine_rate_is_low=pass, raw_has_rows=pass |
| 2025-03-05 | BLOCKED | raw_events | raw_has_rows=FAIL |
| 2025-03-06 | ok | clean_events, daily_metrics, raw_events | clean_has_no_bad_rows=pass, quarantine_rate_is_low=pass, raw_has_rows=pass |
| 2025-03-07 | ok | clean_events, daily_metrics, raw_events | clean_has_no_bad_rows=pass, quarantine_rate_is_low=pass, raw_has_rows=pass |

Blocked partitions: ['2025-03-05']. Summary before the fix: {'days_with_metrics': 6, 'revenue': 24508.87}; table counts: {'raw_events': 575, 'clean_events': 518, 'quarantine': 29, 'daily_metrics': 6}.

After the source is repaired and only 2025-03-05 is re-materialised: summary {'days_with_metrics': 7, 'revenue': 28467.22}; counts {'raw_events': 668, 'clean_events': 601, 'quarantine': 34, 'daily_metrics': 7}.

Re-running all seven partitions again: all succeeded = True; counts {'raw_events': 668, 'clean_events': 601, 'quarantine': 34, 'daily_metrics': 7} (unchanged, so partition writes are idempotent); summary {'days_with_metrics': 7, 'revenue': 28467.22}.
