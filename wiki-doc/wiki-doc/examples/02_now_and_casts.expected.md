# 02 — типы назначения и выражения

Вход: 02_now_and_casts.sql + context.sql. Документируем demo.events_log и загрузку.

- Цель: demo.events_log; источник: demo_src.events.
- event_name: целевой varchar(64), выражение varchar(16).
- event_value: целевой numeric(18,4), выражение numeric(12,2).
- created_at: тип цели timestamp with time zone, выражение timestamptz.
- payload: jsonb в цели и выражении; id: bigint.
- DDL цели найден в том же файле; CREATE и INSERT — разные операции.
- Подмена целевого типа типом выражения является блокирующим дефектом.

