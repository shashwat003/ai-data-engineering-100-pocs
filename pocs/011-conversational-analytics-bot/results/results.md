# Results: governed conversational analytics on the synthetic retail warehouse

The natural-language planner is SIMULATED (keyword rules emitting tool-call JSON); no LLM was run and Streamlit was not used. Everything after the planner is real: schema validation, governed metric SQL, parameter binding, row-level security, small-group suppression and the audit log.

| question | user | result | rows | equals independent SQL |
|---|---|---|---|---|
| What is total revenue? | ana | ok | 1 | True |
| revenue by country | ana | ok | 8 | - |
| orders per segment | ana | ok | 3 | - |
| revenue by category in 2024 | ana | ok | 5 | - |
| units by month for Electronics | ana | ok | 24 | - |
| how many customers by country | ana | ok | 8 | - |
| top 3 revenue by country | ana | ok | 3 | - |
| revenue for Japan in 2023 | ana | ok | 1 | True |
| orders in March 2024 | ana | ok | 1 | True |
| revenue by segment for Germany | ana | ok | 2 | - |
| revenue by country | mia | ok | 1 | - |
| total revenue | mia | ok | 1 | True |

Independent-SQL checks: 4/4 match. Adversarial prompts refused: 8/8. Hostile tool calls refused or bounded: 7/7. Manager (Germany) sees countries ['Germany']; asking for Japan returns 0 rows. Tables intact after all attacks: True. Audit entries: 29.
