# 03 — CTE и временная таблица

Вход: 03_cte_temp.sql + context.sql. Документируем demo.fn_with_cte(date).

- Внешние источники: demo_src.users и demo_src.events.
- active_users и recent_events — локальные CTE, не внешние физические таблицы.
- tmp_filtered — временная цель; ON COMMIT DROP, колонки bigint/text.
- Зафиксированы is_active = true, включённая граница 30 дней, max(event_date),
  GROUP BY user_id и JOIN r.user_id = u.id.
- p_date обязателен; RETURNS void.
- CREATE TEMP и INSERT покрыты отдельно. При отсутствии рендерера не заявлять
  проверку Mermaid инструментом.

