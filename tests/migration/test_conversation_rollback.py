from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from courswatch.conversation.brain import TokenBudget, usage_totals
from courswatch.storage import schema
from ops.migration.prepare_budget_v1_copy import prepare_copy


class ConversationRollbackTests(unittest.TestCase):
    def test_legacy_copy_keeps_current_charges_holds_notices_and_future_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary)/'current.db', Path(temporary)/'legacy-copy.db'
            ledger=TokenBudget(source, limit=10000)
            first=ledger.reserve(500, 'direct'); ledger.settle(first,100,50)
            ledger.reserve(600,'direct')  # Remote outcome still unknown: preserve hold.
            ledger.first_notice()
            with closing(sqlite3.connect(source)) as db:
                db.execute("INSERT INTO blocked_days VALUES('2026-01-01')")
                db.commit()
                original={table:db.execute('SELECT * FROM '+table).fetchall()
                          for table in ('calls','notices','blocked_days','call_profiles')}
            report=prepare_copy(source,target)
            self.assertEqual(report['calls'],2)
            with closing(sqlite3.connect(target)) as db:
                # Exercise the exact old migration rule, which rejects future versions.
                with patch.dict(schema.MIGRATIONS, {'budget':(schema._budget_base,)}):
                    schema.migrate(db,'budget')
                for table,rows in original.items():
                    self.assertEqual(db.execute('SELECT * FROM '+table).fetchall(),rows)
                self.assertEqual(usage_totals(db,ledger.day())['budget_used_tokens'],750)
            with closing(sqlite3.connect(source)) as db:
                self.assertEqual({row[0] for row in db.execute(
                    "SELECT version FROM schema_migrations WHERE component='budget'")},{1,2})
            with self.assertRaises(FileExistsError):
                prepare_copy(source,target)

    def test_future_schema_copy_is_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            source,target=Path(temporary)/'current.db',Path(temporary)/'output.db'
            TokenBudget(source)
            with closing(sqlite3.connect(source)) as db:
                db.execute("INSERT INTO schema_migrations VALUES('budget',99,'synthetic')")
                db.commit()
            with self.assertRaises(ValueError):
                prepare_copy(source,target)
            self.assertFalse(target.exists())
