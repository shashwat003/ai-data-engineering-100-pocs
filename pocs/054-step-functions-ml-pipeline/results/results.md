# Results (measured by `python -m step_functions_ml_pipeline.main`; own ASL interpreter running real scikit-learn tasks)

- definition validation errors: none

- **happy path**: SUCCEEDED; path: `Prepare > QualityGate > Tune > TrainOne > SelectBest > EvaluateGate > Register`; retries: 0 (slept 0s, simulated); registered: {'model': 'churn-lr', 'c': 1.0, 'auc': 0.8414}; notifications: 0
- **transient storage errors (2 failures) are retried with backoff**: SUCCEEDED; path: `Prepare > QualityGate > Tune > TrainOne > SelectBest > EvaluateGate > Register`; retries: 2 (slept 6s, simulated); registered: {'model': 'churn-lr', 'c': 1.0, 'auc': 0.8414}; notifications: 0
- **storage keeps failing -> Catch -> notify**: FAILED (PipelineFailed); path: `Prepare > NotifyFailure > Failed`; retries: 3 (slept 14s, simulated); registered: None; notifications: 1
- **one candidate times out once -> Map item retried**: SUCCEEDED; path: `Prepare > QualityGate > Tune > TrainOne > SelectBest > EvaluateGate > Register`; retries: 1 (slept 1s, simulated); registered: {'model': 'churn-lr', 'c': 1.0, 'auc': 0.8414}; notifications: 0
- **too few positives -> quality gate fails**: FAILED (DataQualityError); path: `Prepare > QualityGate > DataQualityFail`; retries: 0 (slept 0s, simulated); registered: None; notifications: 0
- **weak model (noisy labels) -> evaluation gate -> notify**: FAILED (PipelineFailed); path: `Prepare > QualityGate > Tune > TrainOne > SelectBest > EvaluateGate > NotifyFailure > Failed`; retries: 0 (slept 0s, simulated); registered: None; notifications: 1
- **Deploy to local moto**: {'status': 'deployed to local moto (boto3), not real AWS', 'stored_definition_matches': True, 'type': 'STANDARD', 'logging_level': 'ERROR', 'listed': ['ml-training-pipeline'], 'update_roundtrip': True}
