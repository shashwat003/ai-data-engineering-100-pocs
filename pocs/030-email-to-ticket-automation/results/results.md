# Results: 150 synthetic emails to tickets (rule-based classifier, no LLM)

| metric | value |
|---|---|
| category accuracy | 1.000 |
| urgent flag accuracy (incl. VIP escalation) | 1.000 |
| order ids extracted | 84/84 |
| HARD set (paraphrase, mixed signals, Spanish, quoted threads): category accuracy | 0.133 |
| HARD set: sent to a human instead of auto-routed | 14/15 |
| HARD set: accuracy among auto-routed | 1.000 |
| tickets created | 150 |
| tickets needing a human (low confidence or safety flag) | 14 |
| tickets with a risky attachment flagged | 14 |
| replay of the whole mailbox | {'duplicate': 150} |
| replay changed state | False |
| reply threaded onto the original ticket | appended |

Queues: {'finance-support': 30, 'logistics': 30, 'tier2-engineering': 30, 'account-services': 30, 'product-feedback': 30}. Categories come from keyword scoring on templated bodies, so accuracy here is optimistic for real customer mail.
