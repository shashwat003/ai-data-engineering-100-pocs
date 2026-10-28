-- version precedence: the highest BATCH wins (batches are a monotonic sequence), load time only breaks ties.
-- This makes the result independent of the order in which files happen to arrive.
select
    order_id, customer_id, order_date, status, amount, _batch_id, _loaded_at,
    row_number() over (partition by order_id order by _batch_id desc, _loaded_at desc) as rn
from {{ source('raw', 'orders') }}
