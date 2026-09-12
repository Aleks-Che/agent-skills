# 01 — отсутствующее определение цели

Вход: 01_no_target_ddl.sql + context.sql. Документируем demo.load_missing().

- Читает demo_src.events; пишет demo.missing_target.
- Определение missing_target отсутствует во всём наборе: not_found, а не external.
- Целевые типы id и loaded_at: unknown. Типы выражений: bigint и timestamptz.
- Сигнатура без параметров, RETURNS void; не требовать пяти замечаний или coef_up.
- Отсутствие DDL и его влияние явно описаны. Документация может быть ready при
  честном unknown; конкретный выдуманный тип цели должен дать blocking defect.
- Рабочее выполнение загрузки не проверяется: целевая таблица намеренно отсутствует.

