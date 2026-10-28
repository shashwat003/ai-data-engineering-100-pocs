select customer_id, count(*) as current_rows
from {{ ref('customers_check') }}
where dbt_valid_to = cast('9999-12-31' as timestamp)
group by customer_id
having count(*) > 1
