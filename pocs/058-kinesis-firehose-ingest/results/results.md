# Results (measured by `python -m kinesis_firehose_ingest.main`; Kinesis, Firehose, S3, DynamoDB on a LOCAL moto account; failures injected by wrappers)

- **producer**: {'sent_records': 605, 'retried_records': 100, 'gave_up': 0}
- **crashed_once**: True
- **consumer_after_replay**: {'records': 505, 'batches': 6, 'poison_skipped': 0}
- **sink**: {'objects': 21, 'events': 605, 'unique_event_ids': 605, 'expected': 605, 'late_prefix_events': 50}
- **effectively_once**: True
- **firehose_objects_before_compaction**: 6
- **firehose_compaction**: {'objects_before': 6, 'objects_after': 3, 'events_in': 600, 'events_out': 500, 'duplicates_dropped': 100}
- **firehose_unique_after**: 500
- **poison_skipped**: 1
- **oversize_rejected**: ['big1']
- **missing_stream_error**: ResourceNotFoundException
