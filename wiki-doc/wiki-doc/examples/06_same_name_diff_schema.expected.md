# 06 — схемы и перегрузки

Вход: 06_same_name_diff_schema.sql + context.sql.

- Три объекта: core.orders_summary(), archive.orders_summary(),
  core.orders_summary(bigint).
- Три различных канонических ключа и page_id; ни одна страница не перезаписывает другую.
- Хеш сигнатуры участвует в ключе routines всегда, включая ноль параметров.
- Переименование p_min_id при прежнем типе не меняет ключ; имя схемы и bigint меняют.
- Две функции читают demo.orders, одна — demo.order_history.
- Фильтр id >= p_min_id есть только в перегрузке с аргументом.
- RETURNS bigint и STABLE проверяются для всех трёх read-only функций.

