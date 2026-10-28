-- every fact row's attribute must come from the history row valid on its order date
select f.order_id
from {{ ref('fct_orders') }} as f
left join {{ ref('dim_customer_history') }} as h
       on h.customer_id = f.customer_id and f.order_date >= h.valid_from and f.order_date < h.valid_to
where coalesce(h.segment, '~') <> coalesce(f.segment, '~') or coalesce(h.country, '~') <> coalesce(f.country, '~')
