# 04 — UPDATE, PERFORM и MERGE

Вход: 04_update_perform.sql + context.sql. Документируем demo.refresh_summary(bigint, integer).

- Вызов log_event — два отдельных оператора PERFORM, одна вызываемая функция.
- В данном context.sql тело log_event — NULL; запись в таблицы аудита не выдумывать.
- UPDATE пишет demo.queue, меняет status/end_date только WHERE id = p_id.
- MERGE читает demo_stg.summary_tmp и сопоставляет строки demo.summary по id.
- Обе ветви MERGE пишут demo.summary: UPDATE при совпадении, INSERT иначе.
- demo_stg.summary_tmp не является целью записи; ошибочный INSERT в неё блокирует приёмку.
- Диаграмма показывает UPDATE queue, MERGE summary и USING-источник.
- Нельзя потерять функцию только потому, что нет двух INSERT INTO staging.

