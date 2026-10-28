{{ config(dist='all', sort=['customer_id', 'valid_from']) }}
-- BUSINESS RULE (point-in-time): a customer's segment/country applies from the effective date of the snapshot that
-- first shows it until the next change. Consecutive identical snapshots collapse into one row. The earliest known
-- state extends backwards (valid_from = 1900-01-01), so orders dated before the first snapshot still get an attribute.
-- Snapshots may arrive in any order: validity is derived from effective dates, never from load order.
with changes as (
    select customer_id, segment, country, effective_date, _loaded_at,
           lag(segment) over (partition by customer_id order by effective_date) as prev_segment,
           lag(country) over (partition by customer_id order by effective_date) as prev_country
    from {{ ref('stg_customer_snapshots') }}
),
kept as (
    select * from changes
    where prev_segment is null or prev_segment <> segment or prev_country <> country
)
select customer_id, segment, country,
       case when lag(effective_date) over (partition by customer_id order by effective_date) is null
            then cast('1900-01-01' as date) else effective_date end as valid_from,
       coalesce(lead(effective_date) over (partition by customer_id order by effective_date), cast('9999-12-31' as date)) as valid_to,
       lead(effective_date) over (partition by customer_id order by effective_date) is null as is_current,
       _loaded_at
from kept
