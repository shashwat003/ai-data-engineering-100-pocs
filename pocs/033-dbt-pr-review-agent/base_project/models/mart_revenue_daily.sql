select
    order_date,
    customer_segment,
    sum(amount) as revenue,
    count(*) as orders
from {{ ref('fct_orders') }}
group by order_date, customer_segment
