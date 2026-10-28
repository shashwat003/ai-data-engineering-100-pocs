{{ config(materialized='incremental', unique_key='order_id', incremental_strategy='delete+insert', dist='customer_id', sort='order_date') }}
-- segment/country are AS OF THE ORDER DATE (see dim_customer_history). Incremental runs re-process
--   (a) orders that are new or were re-loaded, and
--   (b) every order of a customer whose history changed since the last build (late or changed snapshots),
-- so the table equals a full refresh no matter in which order orders and snapshots arrive.
select o.order_id, o.customer_id, o.order_date, o.status, o.amount, h.segment, h.country, o._loaded_at, h._loaded_at as _dim_loaded_at
from {{ ref('stg_orders') }} as o
left join {{ ref('dim_customer_history') }} as h
       on h.customer_id = o.customer_id and o.order_date >= h.valid_from and o.order_date < h.valid_to
{% if is_incremental() %}
where o._loaded_at > (select coalesce(max(_loaded_at), cast('1900-01-01' as timestamp)) from {{ this }})
   or o.customer_id in (
        select customer_id from {{ ref('dim_customer_history') }}
        where _loaded_at > (select coalesce(max(_dim_loaded_at), cast('1900-01-01' as timestamp)) from {{ this }})
   )
   or o.customer_id in (select customer_id from {{ this }} where segment is null)
{% endif %}
