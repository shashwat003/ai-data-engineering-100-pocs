# Results: layered output safety filter on synthetic outputs

Classifier (TF-IDF word+char n-grams, logistic regression, trained on synthetic text; test templates and insult words never seen in training): accuracy 0.742 on 128 texts; per class {'benign': {'recall': 0.875, 'precision': 0.691}, 'self_harm': {'recall': 0.667, 'precision': 1.0}, 'toxic': {'recall': 0.625, 'precision': 0.652}, 'weapons': {'recall': 0.5, 'precision': 1.0}}.

Share of unsafe outputs caught (decision other than allow), clean wording:

| category | caught |
|---|---|
| toxic | 0.444 |
| self_harm | 0.667 |
| weapons | 0.500 |
| dangerous_command | 1.000 |
| prompt_leak | 1.000 |
| bad_link | 1.000 |
| pii | 1.000 |
| ungrounded_numbers | 1.000 |

Benign look-alikes (security discussion, 'kill the process', allowed links and commands) blocked, escalated or altered: 0.071 (['If you feel unsafe, contact local emergency services.']).

Evasion attempts on toxic, self-harm, weapon, command and prompt-leak texts:

| transformation | caught with normaliser | caught without |
|---|---|---|
| leetspeak | 0.579 | 0.211 |
| look-alike letters | 0.632 | 0.158 |
| spaced letters | 0.579 | 0.526 |
| zero-width chars | 0.632 | 0.000 |

Ablation (share of all unsafe outputs still caught with one layer removed; all layers: 0.72):

| layer removed | caught |
|---|---|
| pii_secrets | 0.600 |
| links | 0.600 |
| commands | 0.640 |
| prompt_leak | 0.640 |
| grounding | 0.720 |
| classifier | 0.440 |

Latency per output: p50 2.31 ms, p95 2.53 ms on one CPU core.
