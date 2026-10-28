{{ config(dist='even', sort='order_date') }}
select order_date, count(*) as orders,
       sum(case when status in ('completed', 'shipped') then amount else 0 end) as net_revenue
from {{ ref('fct_orders') }}
group by order_date
