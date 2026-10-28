{{ config(dist='all') }}
select segment, count(*) as orders,
       sum(case when status in ('completed', 'shipped') then amount else 0 end) as net_revenue
from {{ ref('fct_orders') }}
group by segment
