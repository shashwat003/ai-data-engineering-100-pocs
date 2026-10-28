select order_id, status, amount,
       case when status not in ('completed', 'shipped', 'cancelled', 'returned') then 'status'
            when amount < 0 then 'amount'
            else 'customer' end as reason
from {{ ref('stg_orders_ranked') }}
where rn = 1
  and (status not in ('completed', 'shipped', 'cancelled', 'returned') or amount < 0 or customer_id is null)
