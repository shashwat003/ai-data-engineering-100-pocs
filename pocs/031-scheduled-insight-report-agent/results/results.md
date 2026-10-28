# Results: scheduled, idempotent, number-verified reports (synthetic metrics; writer is SIMULATED, no LLM)

Report for the incident week:

```
Weekly revenue report for 2025-04-07 to 2025-04-13.
Revenue was $148,619, down 4.7% from $155,883 the week before, and 3.8% below the four-week average of $154,507.
Orders were 1,635 (8.9% down), with an average order value of $90.9.
The largest segment mover was consumer, down $10,248.
Unusual days (z-score of the ratio to the same weekday in the prior four weeks, against the usual spread): 2025-04-12 (-3.9), 2025-04-13 (-4.1).
```

| writer behaviour | text used | passed verification | ungrounded numbers | links |
|---|---|---|---|---|
| faithful | writer | True | - | 0 |
| hallucinating | template_fallback | False | ['11.7%', '14%', '61'] | 0 |
| linking | template_fallback | False | - | 3 |

Schedule `0 8 * * 1`, four Mondays missed since the last tick, first sink call fails: [('2025-04-06', 'send_failed'), ('2025-04-06', 'delivered'), ('2025-04-13', 'delivered'), ('2025-04-20', 'delivered'), ('2025-04-27', 'delivered')]. Restart over the same window: [('2025-04-06', 'already_delivered'), ('2025-04-13', 'already_delivered'), ('2025-04-20', 'already_delivered'), ('2025-04-27', 'already_delivered')]. Files in outbox: 4 (distinct idempotency keys 4).
