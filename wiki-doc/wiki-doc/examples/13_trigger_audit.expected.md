# 13 — триггер, CHECK constraint, GRANT/REVOKE

Вход: 13_trigger_audit.sql + context.sql.
Две отдельные страницы: `table+demo+audit_log` и `table+demo_src+orders`.

- CREATE TABLE demo.audit_log — определение цели, статус resolved.
- Колонки: id bigserial PRIMARY KEY, table_name text NOT NULL, action text NOT NULL,
  changed_at timestamp DEFAULT now(), old_data jsonb, new_data jsonb.
- ALTER TABLE ... ADD CONSTRAINT chk_action CHECK (action IN (...)) — ограничение
  целостности, добавленное упорядоченным DDL.
- CREATE TRIGGER trg_audit_orders AFTER INSERT OR UPDATE OR DELETE ON demo_src.orders
  FOR EACH ROW EXECUTE FUNCTION demo.fn_audit_trigger() — триггер, его функция и
  целевая таблица отражаются как отдельная операция на странице `demo_src.orders`.
  На странице `demo.audit_log` этой операции и прямой записи в orders нет.
- GRANT SELECT ON demo.audit_log TO auditor и REVOKE DELETE ON demo.audit_log FROM PUBLIC —
  права доступа; PUBLIC не превращается в конкретную роль.
- Таблица demo_src.orders имеет id bigint PRIMARY KEY и amount numeric(12,2).
- Функция demo.fn_audit_trigger является отдельным объявлением; на странице orders
  она присутствует как зависимость вызова с доступным определением.
