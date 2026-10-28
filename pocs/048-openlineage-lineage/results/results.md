# Results (measured by `python -m openlineage_lineage.main`; official openlineage-python client -> local receiver, Marquez not used)

- **events_accepted**: 8
- **rejected**: 0
- **upstream_of_mart_revenue**: ['warehouse:fct_orders', 'warehouse:raw_customers', 'warehouse:raw_orders', 'warehouse:stg_customers', 'warehouse:stg_orders']
- **downstream_of_raw_customers**: ['warehouse:fct_orders', 'warehouse:mart_revenue', 'warehouse:stg_customers']
- **sources**: ['warehouse:raw_customers', 'warehouse:raw_orders']
- **column_upstream_mart_revenue.revenue**: ['warehouse:fct_orders.amount', 'warehouse:raw_orders.amount_cents', 'warehouse:stg_orders.amount']
- **column_upstream_mart_revenue.country**: ['warehouse:fct_orders.country', 'warehouse:raw_customers.country_code', 'warehouse:stg_customers.country']
- **stale_after_failure**: {'stg_orders': 'stg_orders', 'fct_orders': 'stg_orders', 'mart_revenue': 'stg_orders'}
- **stuck_runs**: ['stg_customers']
