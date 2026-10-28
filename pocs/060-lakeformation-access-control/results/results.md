# Results (measured by `python -m lakeformation_access_control.main`; Lake Formation semantics SIMULATED on DuckDB, no AWS service)

## Visible columns per role

| role | table | columns |
|---|---|---|
| analyst_eu | sales.customers | customer_id, name, region, segment |
| analyst_eu | sales.orders | amount, customer_id, order_id, region |
| data_scientist | sales.customers | customer_id, region, segment |
| data_scientist | sales.orders | amount, customer_id, order_id, region |
| compliance | sales.customers | customer_id, email, name, region, segment |
| compliance | sales.orders | amount, card_last4, customer_id, order_id, region |
| support | sales.customers | customer_id, name, region |
| support | sales.orders | - |
| stranger | sales.customers | - |
| stranger | sales.orders | - |

## Rows visible per role (customers / orders have 90 / 300 rows)

- analyst_eu: {'sales.customers': 30, 'sales.orders': 100}
- data_scientist: {'sales.customers': 90, 'sales.orders': 300}
- compliance: {'sales.customers': 90, 'sales.orders': 300}
- support: {'sales.customers': 90}

## Attack attempts

| attempt | outcome |
|---|---|
| select forbidden column | OK: denied (column email is not accessible) |
| forbidden column in WHERE (inference oracle) | OK: denied (column email is not accessible) |
| forbidden column via subquery | OK: denied (column email is not accessible) |
| forbidden column via CTE | OK: denied (column email is not accessible) |
| UNION tries to widen rows (non-EU orders) | OK: returned 30 rows (limit 30) |
| SELECT * returns only permitted columns | OK: returned 90 rows (limit 90) |
| table with no grant | OK: denied (table sales.orders does not exist or access is denied) |
| unknown role | OK: denied (table sales.customers does not exist or access is denied) |
| multi-statement | OK: denied (exactly one SELECT statement is allowed) |
| write attempt | OK: denied (exactly one SELECT statement is allowed) |
| table function reads a file | OK: denied (table functions are not allowed) |

## Lint findings

- analyst_eu can read PII column sales.customers.name without clearance
- support can read PII column sales.customers.name without clearance
