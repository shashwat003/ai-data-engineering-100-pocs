-- each row must start exactly where the previous one ended
select customer_id, valid_from, prev_valid_to
from (
    select customer_id, valid_from,
           lag(valid_to) over (partition by customer_id order by valid_from) as prev_valid_to
    from {{ ref('dim_customer_history') }}
) as t
where prev_valid_to is not null and prev_valid_to <> valid_from
