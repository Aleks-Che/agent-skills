# 05 — DEFAULT и специальный аргумент

Вход: 05_default_string.sql + context.sql.

- Оба входных varchar-параметра обязательны: DEFAULT в объявлении отсутствует.
- При p_date_start = 'default' обе границы берутся из queue WHERE status = 1;
  p_date_end в этой ветви не используется, но остаётся обязательным аргументом вызова.
- Иначе применяется формат dd.mm.yyyy к обоим аргументам.
- BETWEEN включает обе границы.
- Допустимые примеры: SELECT demo.fn_default_value('default', 'default');
  SELECT demo.fn_default_value('01.01.2025', '31.01.2025');
- SELECT demo.fn_default_value() нельзя представлять рабочим вызовом.
- В таблице раздельно представлены DEFAULT и поведение при спецзначении.

