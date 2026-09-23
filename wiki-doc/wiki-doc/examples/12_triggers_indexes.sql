CREATE TABLE demo.accounts (
    id integer PRIMARY KEY,
    email text NOT NULL,
    balance numeric(15,2) NOT NULL,
    created_at timestamp without time zone DEFAULT now()
);
CREATE UNIQUE INDEX idx_accounts_email ON demo.accounts (lower(email));
CREATE INDEX idx_accounts_balance ON demo.accounts (balance) WHERE balance > 0;
CREATE FUNCTION demo.audit_account() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO demo.account_audit (account_id, action) VALUES (NEW.id, TG_OP);
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_accounts_audit AFTER INSERT OR UPDATE ON demo.accounts
    FOR EACH ROW EXECUTE FUNCTION demo.audit_account();
ALTER TABLE demo.accounts ADD CONSTRAINT positive_balance CHECK (balance >= 0);
ALTER TABLE demo.accounts ADD CONSTRAINT fk_account_owner FOREIGN KEY (id) REFERENCES demo.owners(id);
GRANT SELECT ON demo.accounts TO reader;
REVOKE INSERT ON demo.accounts FROM writer;
