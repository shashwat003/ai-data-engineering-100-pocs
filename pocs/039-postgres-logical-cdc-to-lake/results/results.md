# Results (POC 039: REAL PostgreSQL 16 logical decoding into a Parquet lake; no Debezium, no Kafka)
Every scenario starts its own throw-away server. Row counts are exact; timings are measured on one machine and noisy.

## 1. First load while the source is being written

A snapshot is taken after the slot exists, while 678 concurrent background transactions ran. Source vs lake after draining: orders: 231 src / 231 lake, 0 missing, 0 extra, 0 differing; accounts: 8 src / 8 lake, 0 missing, 0 extra, 0 differing.

## 2. Streaming with transactions and rollbacks

| check | result |
|---|---|
| committed / rolled-back transactions | 341 / 59 |
| polls interleaved with the workload | 23 |
| silver total balance != constant after any poll (atomicity violations) | 0 |
| rows from rolled-back transactions in silver / events in bronze | 0 / 0 |
| final reconcile | orders: 163 src / 163 lake, 0 missing, 0 extra, 0 differing; accounts: 8 src / 8 lake, 0 missing, 0 extra, 0 differing |

## 3. Crash after the lake write, before the slot advance

| check | result |
|---|---|
| crash injected / slot not advanced | True / True |
| bronze changelog events vs distinct events (restart used a different batch size) | 206 vs 133 |
| final reconcile | orders: 78 src / 78 lake, 0 missing, 0 extra, 0 differing; accounts: 8 src / 8 lake, 0 missing, 0 extra, 0 differing |

## 4. Schema evolution

| step | reconcile |
|---|---|
| ADD COLUMN channel (nullable), 15 rows updated | orders: 60 src / 60 lake, 0 missing, 0 extra, 0 differing |
| ADD COLUMN priority DEFAULT 'normal' (no WAL event per row) | orders: 66 src / 66 lake, 0 missing, 0 extra, 53 differing |
| after re-snapshot of the table (66 rows) | orders: 66 src / 66 lake, 0 missing, 0 extra, 0 differing |

## 5. Replication-slot lag

| moment | WAL bytes the slot is behind |
|---|---|
| idle | 0 |
| after writes, consumer stopped | 66192 |
| after draining | 0 |

Drained 265 transactions in 0.56 s (measured, noisy). An idle slot retains WAL on the server indefinitely, so lag must be alerted on; the demo server also sets `max_slot_wal_keep_size`.

## 6. Unsupported column type (jsonb)

Consumer stopped with `UnsupportedChange: unsupported column type 'jsonb' (at 0/15180B8); stopping before the slot is advanced`; slot lag after the failure 296 bytes (not advanced), bronze files written: 0.

## Debezium-shaped event produced for an UPDATE

```json
{
 "op": "u",
 "before": {
  "id": 7,
  "status": "new",
  "amount": "10.50",
  "note": null
 },
 "after": {
  "id": 7,
  "status": "paid",
  "amount": "10.50",
  "note": null
 },
 "source": {
  "db": "postgres",
  "table": "public.orders",
  "lsn": 22000000,
  "txId": 731,
  "ordinal": 0
 },
 "ts": "2026-01-01 00:00:00+00"
}
```
