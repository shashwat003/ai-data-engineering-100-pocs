-- one row per (customer, snapshot effective date); if a snapshot is loaded twice the latest load wins
select customer_id, segment, country, _effective_date as effective_date, _loaded_at
from (
    select *, row_number() over (partition by customer_id, _effective_date order by _loaded_at desc, _batch_id desc) as rn
    from {{ source('raw', 'customers') }}
    where _effective_date is not null
) as ranked
where rn = 1
