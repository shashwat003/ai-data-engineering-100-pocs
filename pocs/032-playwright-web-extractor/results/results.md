# Results (measured by `python -m playwright_web_extractor.main`; local hostile test site, real Chromium)

Ground truth: 36 valid records of 40 rows (3 invalid + 1 duplicate id).

| extractor | records | recall | false records | quarantined | pages ok | retries | selector fallbacks | seconds |
|---|---|---|---|---|---|---|---|---|
| naive | 9 | 0.250 | 0 | 1 | 1 | 0 | 0 | 4.510 |
| resilient | 36 | 1.000 | 0 | 4 | 4 | 2 | 42 | 3.370 |

- resilient quarantine reasons: ['Value error, empty name', 'Value error, negative price', "could not convert string to float: 'N/A'", 'duplicate id']
