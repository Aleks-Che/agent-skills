# 10 — определение через CTAS

Вход: 10_ctas.sql + context.sql.

- CREATE TABLE AS является определением цели demo.event_clock; статус resolved.
- Источник demo_src.events; CTAS создаёт и заполняет demo.event_clock.
- Результат id bigint, loaded_at timestamptz, cutoff date.
- Отдельного CREATE со списком типов не требуется для однозначно известных выражений.
- cutoff — константа в строке результата, не условие переключения бизнес-логики.

