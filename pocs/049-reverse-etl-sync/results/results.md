# Results: reverse ETL from DuckDB to a mock CRM (simulated time, rate limits and outages)

| step | created | updated | archived | dead-lettered | API calls | retries | 429s | bytes sent |
|---|---|---|---|---|---|---|---|---|
| 1. initial load (1000 rows, 3 requests per 10 s window) | 1000 | 0 | 0 | 0 | 13 | 3 | 3 | 243762 |
| 2. nothing changed | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| 3. next day: 5% updated, 10 deleted, 20 new, 1 bad email | 20 | 50 | 10 | 1 | 2 | 0 | 0 | 8102 |
| 4. 4 consecutive 503s during sync | 0 | 50 | 0 | 0 | 5 | 4 | 0 | 21470 |
| 5. re-run after the outage | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| naive: push every row again | 1000 | - | - | - | 13 | 3 | 3 | 243788 |

After the steps: 1009 of 1010 source rows match the CRM exactly (the rest are the dead-lettered bad email and rows removed). CRM fields ever received: ['churn_risk', 'email', 'first_name', 'last_name', 'lifetime_value', 'segment'] (internal_note and ssn_last4 never leave the warehouse). Archived in CRM: 10. Emptying the source table is blocked by the mass-delete guard: True.
