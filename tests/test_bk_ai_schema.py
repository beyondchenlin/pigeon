"""Exercise additive receipt migration on old rows without rewriting history."""
import os
import unittest
import uuid
from sqlalchemy import create_engine, text, inspect
from server.bk_ai_store import ensure_schema


class ReceiptSchemaTests(unittest.TestCase):
    def check_migration(self, engine):
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE bk_ai_task (id VARCHAR(36) PRIMARY KEY, goal TEXT, snapshot JSON)'))
            connection.execute(text("INSERT INTO bk_ai_task VALUES ('old-task','old goal','{\"status\":\"running\"}')"))
        ensure_schema(engine)
        ensure_schema(engine)
        with engine.connect() as connection:
            row = connection.execute(text('SELECT id, goal, snapshot, execution, sync_meta FROM bk_ai_task')).one()
            self.assertEqual(row.id, 'old-task')
            self.assertEqual(row.goal, 'old goal')
            self.assertIsNone(row.execution)
            self.assertIsNone(row.sync_meta)
            self.assertTrue({'execution','sync_meta'}.issubset({c['name'] for c in inspect(connection).get_columns('bk_ai_task')}))

    def test_existing_sqlite_receipts_preserved_and_upgrade_is_idempotent(self):
        engine = create_engine('sqlite://')
        try:
            self.check_migration(engine)
        finally:
            engine.dispose()

    @unittest.skipUnless(os.environ.get('BK_TEST_POSTGRES_URL'), 'isolated PostgreSQL URL required')
    def test_existing_postgres_receipts_preserved_and_upgrade_is_idempotent(self):
        url = os.environ['BK_TEST_POSTGRES_URL']
        schema = 'bk_contract_' + uuid.uuid4().hex
        admin = create_engine(url)
        with admin.begin() as connection:
            connection.execute(text('CREATE SCHEMA ' + schema))
        engine = create_engine(url, connect_args={'options':'-csearch_path=' + schema})
        try:
            self.check_migration(engine)
        finally:
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
            admin.dispose()


if __name__ == '__main__':
    unittest.main()
