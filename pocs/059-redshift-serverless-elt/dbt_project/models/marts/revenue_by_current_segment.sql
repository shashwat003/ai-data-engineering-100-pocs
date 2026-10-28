{{ config(dist='all') }}
-- today's segmentation applied to all history (a different question from revenue_by_segment)
select d.segment, count(*) as orders,
       sum(case when f.status in ('completed', 'shipped') then f.amount else 0 end) as net_revenue
from {{ ref('fct_orders') }} as f
join {{ ref('dim_customer') }} as d on d.customer_id = f.customer_id
group by d.segment
