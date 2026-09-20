"""P2-03: Model extensions - triggers, indexes, constraints, access control."""
import hashlib
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from sql_extract import extract_inventory
from sql_ast import analyze
from check_policy import load_policy
from validation_plan import generate_plan

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


class TriggerExtractionTests(unittest.TestCase):
    """Test trigger extraction from SQL."""

    def inventory(self, text, path='input.sql'):
        return extract_inventory(text, path, hashlib.sha256(text.encode()).hexdigest(), version='15')

    def test_create_trigger_before_insert(self):
        sql = '''CREATE FUNCTION demo.audit() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
CREATE TRIGGER trg_audit BEFORE INSERT ON demo.accounts FOR EACH ROW EXECUTE FUNCTION demo.audit();'''
        result = self.inventory(sql)
        triggers = [i for i in result['items'] if i['kind'] == 'TRIGGER']
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]['details']['trigger_name'], 'trg_audit')
        self.assertEqual(triggers[0]['details']['timing'], 'BEFORE')
        self.assertIn('INSERT', triggers[0]['details']['events'])

    def test_create_trigger_after_update(self):
        sql = '''CREATE FUNCTION demo.audit() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
CREATE TRIGGER trg_update AFTER UPDATE ON demo.accounts FOR EACH ROW EXECUTE FUNCTION demo.audit();'''
        result = self.inventory(sql)
        triggers = [i for i in result['items'] if i['kind'] == 'TRIGGER']
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]['details']['timing'], 'AFTER')
        self.assertIn('UPDATE', triggers[0]['details']['events'])

    def test_create_trigger_multiple_events(self):
        sql = '''CREATE FUNCTION demo.audit() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
CREATE TRIGGER trg_multi AFTER INSERT OR UPDATE OR DELETE ON demo.accounts FOR EACH ROW EXECUTE FUNCTION demo.audit();'''
        result = self.inventory(sql)
        triggers = [i for i in result['items'] if i['kind'] == 'TRIGGER']
        self.assertEqual(len(triggers), 1)
        self.assertEqual(len(triggers[0]['details']['events']), 3)
        self.assertIn('INSERT', triggers[0]['details']['events'])
        self.assertIn('UPDATE', triggers[0]['details']['events'])
        self.assertIn('DELETE', triggers[0]['details']['events'])

    def test_create_trigger_instead_of(self):
        sql = '''CREATE FUNCTION demo.handle_view() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
CREATE TRIGGER trg_view INSTEAD OF INSERT ON demo.my_view FOR EACH ROW EXECUTE FUNCTION demo.handle_view();'''
        result = self.inventory(sql)
        triggers = [i for i in result['items'] if i['kind'] == 'TRIGGER']
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]['details']['timing'], 'INSTEAD OF')

    def test_trigger_calls_function(self):
        sql = '''CREATE FUNCTION demo.audit() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
CREATE TRIGGER trg_audit BEFORE INSERT ON demo.accounts FOR EACH ROW EXECUTE FUNCTION demo.audit();'''
        result = self.inventory(sql)
        triggers = [i for i in result['items'] if i['kind'] == 'TRIGGER']
        self.assertIn('demo.audit', triggers[0].get('calls', []))

    def test_trigger_writes_to_table(self):
        sql = '''CREATE FUNCTION demo.audit() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
CREATE TRIGGER trg_audit BEFORE INSERT ON demo.accounts FOR EACH ROW EXECUTE FUNCTION demo.audit();'''
        result = self.inventory(sql)
        triggers = [i for i in result['items'] if i['kind'] == 'TRIGGER']
        self.assertIn('demo.accounts', triggers[0].get('writes', []))


class IndexExtractionTests(unittest.TestCase):
    """Test index extraction from SQL."""

    def inventory(self, text, path='input.sql'):
        return extract_inventory(text, path, hashlib.sha256(text.encode()).hexdigest(), version='15')

    def test_create_index_btree(self):
        sql = 'CREATE INDEX idx_name ON demo.accounts (name);'
        result = self.inventory(sql)
        indexes = [i for i in result['items'] if i['kind'] == 'INDEX']
        self.assertEqual(len(indexes), 1)
        self.assertEqual(indexes[0]['details']['index_name'], 'idx_name')
        self.assertEqual(indexes[0]['details']['access_method'], 'btree')
        self.assertFalse(indexes[0]['details']['unique'])

    def test_create_unique_index(self):
        sql = 'CREATE UNIQUE INDEX idx_email ON demo.users (email);'
        result = self.inventory(sql)
        indexes = [i for i in result['items'] if i['kind'] == 'INDEX']
        self.assertEqual(len(indexes), 1)
        self.assertTrue(indexes[0]['details']['unique'])

    def test_create_index_gin(self):
        sql = 'CREATE INDEX idx_tags ON demo.posts USING gin (tags);'
        result = self.inventory(sql)
        indexes = [i for i in result['items'] if i['kind'] == 'INDEX']
        self.assertEqual(len(indexes), 1)
        self.assertEqual(indexes[0]['details']['access_method'], 'gin')

    def test_create_index_expression(self):
        sql = 'CREATE INDEX idx_lower_name ON demo.accounts (lower(name));'
        result = self.inventory(sql)
        indexes = [i for i in result['items'] if i['kind'] == 'INDEX']
        self.assertEqual(len(indexes), 1)

    def test_create_index_partial(self):
        sql = 'CREATE INDEX idx_active ON demo.accounts (status) WHERE active = true;'
        result = self.inventory(sql)
        indexes = [i for i in result['items'] if i['kind'] == 'INDEX']
        self.assertEqual(len(indexes), 1)

    def test_index_writes_to_table(self):
        sql = 'CREATE INDEX idx_name ON demo.accounts (name);'
        result = self.inventory(sql)
        indexes = [i for i in result['items'] if i['kind'] == 'INDEX']
        self.assertIn('demo.accounts', indexes[0].get('writes', []))


class ConstraintExtractionTests(unittest.TestCase):
    """Test constraint extraction from SQL."""

    def inventory(self, text, path='input.sql'):
        return extract_inventory(text, path, hashlib.sha256(text.encode()).hexdigest(), version='15')

    def test_foreign_key_constraint(self):
        sql = '''CREATE TABLE demo.orders (
    id SERIAL PRIMARY KEY,
    account_id INTEGER NOT NULL
);
ALTER TABLE demo.orders ADD CONSTRAINT fk_account FOREIGN KEY (account_id) REFERENCES demo.accounts(id);'''
        result = self.inventory(sql)
        alters = [i for i in result['items'] if i['kind'] == 'ALTER']
        self.assertTrue(len(alters) > 0)
        # Check that the ALTER statement is captured
        self.assertTrue(any('fk_account' in str(i.get('details', {})) for i in alters))

    def test_unique_constraint(self):
        sql = '''CREATE TABLE demo.users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL
);
ALTER TABLE demo.users ADD CONSTRAINT unique_email UNIQUE (email);'''
        result = self.inventory(sql)
        alters = [i for i in result['items'] if i['kind'] == 'ALTER']
        self.assertTrue(len(alters) > 0)

    def test_check_constraint(self):
        sql = '''CREATE TABLE demo.accounts (
    id SERIAL PRIMARY KEY,
    balance NUMERIC(15,2) NOT NULL
);
ALTER TABLE demo.accounts ADD CONSTRAINT positive_balance CHECK (balance >= 0);'''
        result = self.inventory(sql)
        alters = [i for i in result['items'] if i['kind'] == 'ALTER']
        self.assertTrue(len(alters) > 0)


class GrantRevokeExtractionTests(unittest.TestCase):
    """Test GRANT/REVOKE extraction from SQL."""

    def inventory(self, text, path='input.sql'):
        return extract_inventory(text, path, hashlib.sha256(text.encode()).hexdigest(), version='15')

    def test_grant_select(self):
        sql = 'GRANT SELECT ON demo.accounts TO reader;'
        result = self.inventory(sql)
        grants = [i for i in result['items'] if i['kind'] == 'GRANT']
        self.assertEqual(len(grants), 1)
        self.assertIn('SELECT', grants[0]['details']['privileges'])
        self.assertIn('reader', grants[0]['details']['grantees'])

    def test_grant_multiple_privileges(self):
        sql = 'GRANT INSERT, UPDATE ON demo.accounts TO writer;'
        result = self.inventory(sql)
        grants = [i for i in result['items'] if i['kind'] == 'GRANT']
        self.assertEqual(len(grants), 1)
        self.assertEqual(len(grants[0]['details']['privileges']), 2)
        self.assertIn('INSERT', grants[0]['details']['privileges'])
        self.assertIn('UPDATE', grants[0]['details']['privileges'])

    def test_grant_all_privileges(self):
        sql = 'GRANT ALL PRIVILEGES ON demo.accounts TO admin;'
        result = self.inventory(sql)
        grants = [i for i in result['items'] if i['kind'] == 'GRANT']
        self.assertEqual(len(grants), 1)
        # "ALL PRIVILEGES" is extracted as a single token
        self.assertTrue(any('ALL' in p for p in grants[0]['details']['privileges']))

    def test_revoke_select(self):
        sql = 'REVOKE SELECT ON demo.accounts FROM reader;'
        result = self.inventory(sql)
        revokes = [i for i in result['items'] if i['kind'] == 'REVOKE']
        self.assertEqual(len(revokes), 1)
        self.assertIn('SELECT', revokes[0]['details']['privileges'])

    def test_grant_writes_to_table(self):
        sql = 'GRANT SELECT ON demo.accounts TO reader;'
        result = self.inventory(sql)
        grants = [i for i in result['items'] if i['kind'] == 'GRANT']
        self.assertIn('demo.accounts', grants[0].get('writes', []))


class NativeASTExtractionTests(unittest.TestCase):
    """Test AST-based extraction for new constructs."""

    def inventory(self, text, path='input.sql'):
        return extract_inventory(text, path, hashlib.sha256(text.encode()).hexdigest(), version='15')

    def test_trigger_ast_extraction(self):
        sql = '''CREATE FUNCTION demo.audit() RETURNS TRIGGER LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$;
CREATE TRIGGER trg_audit BEFORE INSERT ON demo.accounts FOR EACH ROW EXECUTE FUNCTION demo.audit();'''
        result = self.inventory(sql)
        triggers = [i for i in result['items'] if i['kind'] == 'TRIGGER']
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0]['details']['trigger_name'], 'trg_audit')
        self.assertEqual(triggers[0]['details']['timing'], 'BEFORE')
        self.assertIn('INSERT', triggers[0]['details']['events'])

    def test_index_ast_extraction(self):
        sql = '''CREATE TABLE demo.accounts (id SERIAL PRIMARY KEY, name VARCHAR(100));
CREATE INDEX idx_name ON demo.accounts (name);'''
        result = self.inventory(sql)
        indexes = [i for i in result['items'] if i['kind'] == 'INDEX']
        # May find implicit index from PRIMARY KEY plus explicit index
        # Also may find duplicate from regex+AST extraction
        self.assertGreaterEqual(len(indexes), 1)
        # Find the explicit index
        explicit = [i for i in indexes if i['details'].get('index_name') == 'idx_name']
        self.assertGreaterEqual(len(explicit), 1)
        self.assertEqual(explicit[0]['details']['access_method'], 'btree')

    def test_grant_ast_extraction(self):
        sql = '''CREATE TABLE demo.accounts (id SERIAL PRIMARY KEY, name VARCHAR(100));
GRANT SELECT ON demo.accounts TO reader;'''
        result = self.inventory(sql)
        grants = [i for i in result['items'] if i['kind'] == 'GRANT']
        self.assertEqual(len(grants), 1)
        self.assertIn('select', grants[0]['details']['privileges'])

    def test_alter_table_add_constraint_ast(self):
        sql = '''CREATE TABLE demo.accounts (id SERIAL PRIMARY KEY, customer_id INTEGER);
ALTER TABLE demo.accounts ADD CONSTRAINT fk_customer FOREIGN KEY (customer_id) REFERENCES demo.customers(id);'''
        result = self.inventory(sql)
        alters = [i for i in result['items'] if i['kind'] == 'ALTER']
        self.assertTrue(len(alters) > 0)
        # Check that constraints are extracted
        constraints = alters[0]['details'].get('constraints', [])
        self.assertTrue(len(constraints) > 0)
        self.assertEqual(constraints[0]['type'], 'FOREIGN_KEY')
        self.assertEqual(constraints[0]['name'], 'fk_customer')


class ExampleFileTests(unittest.TestCase):
    """Test that example files parse without gaps."""

    def test_example_12_triggers_indexes(self):
        example_path = EXAMPLES / '12_triggers_indexes.sql'
        text = example_path.read_text(encoding='utf-8-sig')
        result = extract_inventory(text, example_path.name,
                                  hashlib.sha256(text.encode()).hexdigest(), version='15')
        # Should have triggers, indexes, grants
        kinds = {i['kind'] for i in result['items']}
        self.assertIn('TRIGGER', kinds)
        self.assertIn('INDEX', kinds)
        self.assertIn('GRANT', kinds)
        self.assertIn('REVOKE', kinds)
        self.assertFalse(result['coverage_notes'], result['coverage_notes'])


class PlanIntegrationTests(unittest.TestCase):
    """New constructs must produce concrete obligations, not just inventory items."""

    def plan(self, sql, subject, path='input.sql'):
        inv = extract_inventory(sql, path, hashlib.sha256(sql.encode()).hexdigest(),
                                version='15', documented_subjects=[subject])
        return inv, generate_plan(inv, load_policy(), page_id=subject)

    def test_example_12_produces_all_extension_obligations(self):
        example = EXAMPLES / '12_triggers_indexes.sql'
        text = example.read_text(encoding='utf-8-sig')
        inv, plan = self.plan(text, 'table+demo+accounts', example.name)
        rules = {c['rule_id'] for c in plan['required_checks']}
        for rule in ('trigger', 'index', 'constraint', 'access_rule'):
            self.assertIn(rule, rules)

    def test_constraint_types_from_alter_table(self):
        sql = ('CREATE TABLE demo.accounts (id integer, balance numeric);\n'
               'ALTER TABLE demo.accounts ADD CONSTRAINT positive_balance CHECK (balance >= 0);\n'
               'ALTER TABLE demo.accounts ADD CONSTRAINT fk_owner FOREIGN KEY (id) REFERENCES demo.owners(id);')
        inv, plan = self.plan(sql, 'table+demo+accounts')
        constraints = [i for i in inv['items'] if i['kind'] == 'ALTER'
                       and i['details'].get('constraints')]
        types = {c['type'] for item in constraints for c in item['details']['constraints']}
        self.assertIn('CHECK', types)
        self.assertIn('FOREIGN_KEY', types)
        self.assertEqual(sum(c['rule_id'] == 'constraint' for c in plan['required_checks']), 2)

    def test_standalone_constructs_do_not_leak_into_function_scope(self):
        text = (EXAMPLES / '12_triggers_indexes.sql').read_text(encoding='utf-8-sig')
        inv = extract_inventory(text, 'input.sql', hashlib.sha256(text.encode()).hexdigest(), version='15')
        function_scope = 'function+demo+audit_account+()'
        leaked = [i['kind'] for i in inv['items']
                  if i['anchor']['object_or_scope'] == function_scope
                  and i['kind'] in ('INDEX', 'TRIGGER', 'GRANT', 'REVOKE', 'ALTER')]
        self.assertEqual(leaked, [])


if __name__ == '__main__':
    unittest.main()
