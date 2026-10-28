# Results: AI risk register for a synthetic 7-system inventory

Summary: {'systems': 7, 'by_tier': {'prohibited': 1, 'high': 2, 'limited': 2, 'minimal': 2}, 'open_gaps': 5}. Full register: `results/register.md`.

| id | system | tier | missing evidence |
|---|---|---|---|
| SYS-01 | Credit scoring model | high | - |
| SYS-02 | CV screening assistant | high | risk_management, data_governance, human_oversight, accuracy_monitoring |
| SYS-03 | Support chatbot | limited | - |
| SYS-04 | Marketing copy generator | minimal | - |
| SYS-05 | Invoice anomaly detector | minimal | - |
| SYS-06 | Emotion tracking in call centre | prohibited | - |
| SYS-07 | Deepfake voice for ads | limited | content_labelling |

Tiers come from a deliberately small rule set (3 prohibited practices, 6 Annex III areas, transparency duties); a human must confirm every tier.
