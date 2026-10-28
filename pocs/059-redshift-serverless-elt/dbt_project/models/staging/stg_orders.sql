select order_id, customer_id, order_date, status, amount, _loaded_at
from {{ ref('stg_orders_ranked') }}
where rn = 1
  and status in ('completed', 'shipped', 'cancelled', 'returned')
  and amount >= 0
  and customer_id is not null
