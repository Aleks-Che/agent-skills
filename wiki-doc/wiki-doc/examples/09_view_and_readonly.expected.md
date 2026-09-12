# 09 — формула во view и read-only функции

Вход: 09_view_and_readonly.sql + context.sql.

- Два объекта: demo.order_totals и demo.total_for(bigint).
- Оба читают demo.orders, не изменяют её строки.
- Формула price * quantity проверяется в обоих объектах, несмотря на отсутствие ETL.
- Фильтр status = 'paid' есть в обоих; id = p_id — только в функции.
- Подмена умножения сложением в документации даёт blocking defect.
- total_for: RETURNS numeric, STABLE, p_id обязателен.
- У view нет входных параметров функции и EXCEPTION; соответствующие проверки NA.

