# 12 — materialized view, индексы и GRANT

Вход: 12_matview_index_grant.sql + context.sql.

- CREATE MATERIALIZED VIEW demo.monthly_sales — определение цели, статус resolved.
- Источник demo_src.orders; CTAS/матпредставление формирует demo.monthly_sales.
- Выход: month, total_amount, order_count; тип order_count — bigint.
- Создаются два индекса: idx_monthly_sales_month (UNIQUE, month) и
  idx_monthly_sales_amount (total_amount, частичный по total_amount > 1000).
- GRANT SELECT ON demo.monthly_sales TO reporting_role — право доступа, отдельное
  от чтения данных.
- Индексы и GRANT не выдумывают физических объектов-страниц: это операции DDL/DCL
  над demo.monthly_sales.
