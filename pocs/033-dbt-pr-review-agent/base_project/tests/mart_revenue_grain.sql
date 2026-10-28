-- fails if the mart has more than one row per (order_date, customer_segment)
select order_date, customer_segment, count(*) as n
from {{ ref('mart_revenue_daily') }}
group by 1, 2
having count(*) > 1
