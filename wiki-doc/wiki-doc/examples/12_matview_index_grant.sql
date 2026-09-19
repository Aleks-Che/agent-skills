CREATE MATERIALIZED VIEW demo.monthly_sales AS
SELECT
    date_trunc('month', o.created_at) AS month,
    sum(o.amount) AS total_amount,
    count(*) AS order_count
FROM demo_src.orders AS o
WHERE o.status = 'completed'
GROUP BY date_trunc('month', o.created_at);

CREATE UNIQUE INDEX idx_monthly_sales_month ON demo.monthly_sales (month);

CREATE INDEX idx_monthly_sales_amount ON demo.monthly_sales (total_amount)
WHERE total_amount > 1000;

GRANT SELECT ON demo.monthly_sales TO reporting_role;
