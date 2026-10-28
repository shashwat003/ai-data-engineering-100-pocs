# Results: multi-agent data-quality triage on synthetic orders (rule-based agents, no LLM)

| defect | injected | found | extra flags |
|---|---|---|---|
| null_customer_id | 30 | 30 | 0 |
| status_typo | 40 | 40 | 0 |
| negative_amount | 15 | 15 | 0 |
| future_date | 25 | 25 | 0 |
| orphan_customer | 20 | 20 | 0 |
| duplicate_order_id | 20 | 20 | 0 |

| issue | severity | category | owner | rows |
|---|---|---|---|---|
| not_null:customer_id | medium | needs_human | source-system-owner | 30 |
| unique:order_id | high | auto_fixable | ingestion-team | 20 |
| in_set:status | medium | auto_fixable | data-steward | 40 |
| range_min:amount | medium | needs_human | source-system-owner | 15 |
| not_future:order_date | medium | needs_human | source-system-owner | 25 |
| fk_exists:customer_id | high | needs_human | ingestion-team | 20 |

Auto-applied (verified, confidence >= 0.9, <= 5% rows): [('drop_duplicates', ('unique', 'order_id'), 20), ('map_values', ('in_set', 'status'), 40)]. Queued for a human: 4 proposals (null/orphan customers, negative amounts, future dates are quarantine proposals, never guessed). Status typos repaired correctly: 40/40. Remaining issues after the auto fixes: 4; a second run auto-applies 0 further fixes (idempotent).
