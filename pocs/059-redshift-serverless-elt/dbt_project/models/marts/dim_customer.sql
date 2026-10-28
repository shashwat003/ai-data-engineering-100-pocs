{{ config(dist='all', sort='customer_id') }}
-- CURRENT state of each customer (the open history row) plus order facts; use fct_orders.segment for point-in-time reporting
select h.customer_id, h.segment, h.country, min(o.order_date) as first_order_date, count(o.order_id) as lifetime_orders
from {{ ref('dim_customer_history') }} as h
left join {{ ref('stg_orders') }} as o on o.customer_id = h.customer_id
where h.is_current
group by h.customer_id, h.segment, h.country
