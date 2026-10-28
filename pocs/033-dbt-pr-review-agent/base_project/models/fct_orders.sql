select
    o.order_id,
    o.customer_id,
    o.order_date,
    o.status,
    o.amount,
    c.customer_segment
from {{ ref('stg_orders') }} as o
inner join {{ ref('dim_customers') }} as c
    on o.customer_id = c.customer_id
where o.status <> 'cancelled'
