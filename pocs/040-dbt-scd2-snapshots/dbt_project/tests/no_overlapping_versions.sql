-- Singular test: two versions of the same customer must never overlap in time.
select a.customer_id, a.dbt_valid_from, a.dbt_valid_to, b.dbt_valid_from as other_from
from {{ ref('customers_check') }} a
join {{ ref('customers_check') }} b
  on a.customer_id = b.customer_id and a.dbt_scd_id <> b.dbt_scd_id
 and a.dbt_valid_from < coalesce(b.dbt_valid_to, cast('9999-12-31' as timestamp))
 and b.dbt_valid_from < coalesce(a.dbt_valid_to, cast('9999-12-31' as timestamp))
