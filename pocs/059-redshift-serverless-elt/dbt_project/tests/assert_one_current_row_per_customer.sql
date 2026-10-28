select customer_id, count(*) as current_rows
from {{ ref('dim_customer_history') }}
where is_current
group by customer_id
having count(*) <> 1
