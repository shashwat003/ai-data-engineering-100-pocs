select
    customer_id,
    name,
    customer_segment,
    country
from {{ ref('stg_customers') }}
