# Results: Structured Streaming sessionisation (517 events, 12 micro-batches, 30-minute gap)

The oracle is a plain-Python batch sessionisation of all events. Events arrive out of order: about 6% 10-50 minutes late and 1% 2-3 hours late. 'Dropped events' are events inside oracle sessions that Spark discarded because they arrived behind the watermark.

| watermark | sessions emitted | oracle sessions | sessions identical to oracle | events dropped by Spark | drops predicted by Python model | median lag (micro-batches) from last event arrival to emission | share emitted only by the final flush |
|---|---|---|---|---|---|---|---|
| 5 minutes | 94 | 94 | 0.957 | 4 | 8 | 2 | 0.202 |
| 30 minutes | 94 | 94 | 0.957 | 4 | 4 | 3 | 0.287 |
| 3 hours | 94 | 94 | 1.000 | 0 | 0 | 7 | 0.691 |
