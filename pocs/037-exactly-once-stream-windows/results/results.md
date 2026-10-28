# Results (measured by `python -m exactly_once_windows.main`; SIMULATED in-process broker, no Kafka/Redpanda; 4 random crashes per run)

600 events in 24 batches of 25; 60 s tumbling windows, 30 s allowed lateness, ~5% late events. 30 runs per mode.

| delivery mode | runs exactly equal to reference | duplicate window records | windows with wrong values | windows missing | restarts |
|---|---|---|---|---|---|
| exactly_once | 30/30 | 0 | 0 | 0 | 113 |
| at_least_once | 0/30 | 15 | 206 | 67 | 84 |
| at_most_once | 6/30 | 0 | 91 | 69 | 37 |

No-crash exactly-once run vs the independent sequential reference: {'windows_expected': 48, 'output_records': 48, 'duplicate_records': 0, 'windows_with_wrong_values': 0, 'windows_missing': 0, 'late_events': 19, 'late_expected': 19, 'exact': True}
