CREATE TABLE demo.audit_log (
    id bigserial PRIMARY KEY,
    table_name text NOT NULL,
    action text NOT NULL,
    changed_at timestamp DEFAULT now(),
    old_data jsonb,
    new_data jsonb
);

CREATE TABLE demo_src.orders (
    id bigint PRIMARY KEY,
    amount numeric(12,2)
);

CREATE OR REPLACE FUNCTION demo.fn_audit_trigger()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO demo.audit_log (table_name, action, new_data)
        VALUES (TG_TABLE_NAME, TG_OP, to_jsonb(NEW));
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO demo.audit_log (table_name, action, old_data, new_data)
        VALUES (TG_TABLE_NAME, TG_OP, to_jsonb(OLD), to_jsonb(NEW));
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO demo.audit_log (table_name, action, old_data)
        VALUES (TG_TABLE_NAME, TG_OP, to_jsonb(OLD));
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_audit_orders
AFTER INSERT OR UPDATE OR DELETE ON demo_src.orders
FOR EACH ROW EXECUTE FUNCTION demo.fn_audit_trigger();

ALTER TABLE demo.audit_log ADD CONSTRAINT chk_action
CHECK (action IN ('INSERT', 'UPDATE', 'DELETE'));

GRANT SELECT ON demo.audit_log TO auditor;
REVOKE DELETE ON demo.audit_log FROM PUBLIC;
