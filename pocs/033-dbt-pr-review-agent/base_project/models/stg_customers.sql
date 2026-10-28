select
    customer_id,
    name,
    segment as customer_segment,
    country
from {{ ref('raw_customers') }}
