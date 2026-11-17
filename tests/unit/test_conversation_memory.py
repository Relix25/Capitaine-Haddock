from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from courswatch.conversation.contracts import ConversationSnapshot, MediaDescriptor, MessageEnvelope, Scope
from courswatch.memory import Journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.moment = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        self.scope = Scope('guild', 'allowed')
        self.journal = Journal(self.root / 'conversation.db', now=lambda: self.moment)

    def message(self, identifier='one', author='alice', text='AAIT : proposition vendredi', **changes):
        value = MessageEnvelope(self.scope, identifier, author, 'Même pseudo', text,
                                self.moment.isoformat(), source_url='https://discord.com/channels/guild/allowed/' + identifier)
        return replace(value, **changes)

    def test_same_timestamp_rest_update_invalidates_old_media_but_replay_cannot_revert(self):
        old_media = MediaDescriptor('embed:0:image', 'image', 'https://cdn.discordapp.com/a.png')
        new_media = replace(old_media, url='https://cdn.discordapp.com/b.png')
        original = self.message(media=(old_media,))
        self.journal.ingest(original)
        self.assertTrue(self.journal.save_observation(self.scope, 'one', 1, old_media.media_id,
                                                      {'status':'ready', 'description':'old image'}))
        refreshed = replace(original, media=(new_media,), event_kind='rest')
        self.assertEqual(self.journal.ingest(refreshed, backfill=True).revision, 2)
        self.assertIsNone(self.journal.ingest(original))
        self.assertFalse(self.journal.observations(self.scope, 'one'))

    def test_live_create_after_backfill_is_admitted_once_without_new_revision(self):
        from courswatch.conversation.coordinator import Coordinator

        coordinator = Coordinator()
        request = self.message(text='Haddock, bonjour !')
        historical = self.journal.ingest(replace(request, event_kind='rest'), backfill=True)
        self.assertTrue(coordinator.observe(historical))
        live = self.journal.ingest(request)
        self.assertEqual(live.revision, historical.revision)
        self.assertTrue(coordinator.observe(live))
        turn = coordinator.enqueue(live, direct=True)
        self.assertEqual(turn.reply_target_id, request.message_id)
        reopened = Journal(self.journal.path, now=lambda: self.moment)
        self.assertIsNone(reopened.ingest(request))
        edited = replace(request, text='Question modifiée', event_kind='rest')
        self.assertEqual(reopened.ingest(edited, backfill=True).revision, 2)
        self.assertIsNone(reopened.ingest(replace(edited, event_kind='create')))

    def test_page_reconciles_only_snowflakes_it_covered_at_the_shared_timestamp(self):
        for identity in ('10', '11', '12'):
            self.journal.ingest(self.message(identity))
        instant = self.moment.isoformat()
        self.assertEqual(self.journal.reconcile_window(self.scope, instant, instant, ('11',),
                         after_message_id=9, through_message_id=11, observed_before=instant), 1)
        self.assertIsNone(self.journal.active(self.scope, '10'))
        self.assertIsNotNone(self.journal.active(self.scope, '11'))
        self.assertIsNotNone(self.journal.active(self.scope, '12'))

    def test_recall_collective_discussion_after_seven_days_and_restart(self):
        first = self.message()
        second = self.message('two', 'bob', 'AAIT : proposition acceptée', reply_to_message_id='one')
        self.journal.ingest(first)
        self.journal.ingest(second)
        self.moment += timedelta(days=7)
        restarted = Journal(self.root / 'conversation.db', now=lambda: self.moment)
        rows = restarted.search(self.scope, 'AAIT')
        self.assertEqual({row.author_id for row in rows}, {'alice', 'bob'})
        self.assertEqual(restarted.active(self.scope, 'two').reply_to_message_id, 'one')
        self.assertEqual(restarted.active(self.scope, 'one').created_at, first.created_at)
        self.assertEqual(len(restarted.recent(self.scope)), 2)

    def test_duplicate_and_stale_backfill_cannot_override_edit(self):
        original = self.message()
        self.assertEqual(self.journal.ingest(original).revision, 1)
        self.assertIsNone(self.journal.ingest(original))
        edited = replace(original, text='AAIT : proposition lundi',
                         edited_at=(self.moment + timedelta(minutes=1)).isoformat(), event_kind='edit')
        self.assertEqual(self.journal.ingest(edited).revision, 2)
        self.assertIsNone(self.journal.ingest(original, backfill=True))
        self.assertEqual(self.journal.active(self.scope, 'one').text, edited.text)
        self.assertFalse(self.journal.search(self.scope, 'vendredi'))
        self.assertTrue(self.journal.search(self.scope, 'lundi'))
        with closing(sqlite3.connect(self.journal.path)) as db, db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM message_versions').fetchone()[0], 2)

    def test_delete_wipes_versions_and_observations_then_blocks_resurrection(self):
        media = MediaDescriptor('media-a', 'image', 'https://cdn.discordapp.com/attachments/a/b/a.png')
        message = self.journal.ingest(self.message(media=(media,)))
        self.assertTrue(self.journal.save_observation(self.scope, 'one', message.revision, 'media-a',
                                                    {'status': 'ready', 'description': 'Un voilier jaune', 'ocr': [{'text': 'AAIT'}]}))
        self.assertTrue(self.journal.search(self.scope, 'voilier'))
        self.assertTrue(self.journal.delete(self.scope, 'one'))
        self.assertFalse(self.journal.delete(self.scope, 'one'))
        self.assertIsNone(self.journal.active(self.scope, 'one'))
        self.assertFalse(self.journal.observations(self.scope))
        self.assertFalse(self.journal.search(self.scope, 'voilier'))
        self.assertFalse(self.journal.save_observation(self.scope, 'one', 1, 'media-a', {'description': 'late'}))
        self.assertIsNone(self.journal.ingest(message, backfill=True))
        with closing(sqlite3.connect(self.journal.path)) as db, db:
            self.assertFalse(db.execute('SELECT payload FROM message_versions').fetchall())
            self.assertNotIn('proposition', db.execute('SELECT payload FROM messages').fetchone()[0])

    def test_unknown_delete_precedes_create_and_scope_isolation(self):
        self.journal.delete(self.scope, 'one')
        self.assertIsNone(self.journal.ingest(self.message()))
        other = Scope('guild', 'private')
        self.journal.ingest(self.message(scope=other))
        self.assertFalse(self.journal.search(self.scope, 'AAIT'))
        self.assertEqual(len(self.journal.search(other, 'AAIT')), 1)
        self.assertFalse(self.journal.recent(Scope('other-guild', 'private')))

    def test_same_media_description_has_distinct_author_occurrences(self):
        asset = MediaDescriptor('emoji:12', 'emoji', 'https://cdn.discordapp.com/emojis/12.png')
        for identifier, author in [('one', 'alice'), ('two', 'bob')]:
            active = self.journal.ingest(self.message(identifier, author, '', media=(asset,)))
            self.assertTrue(self.journal.save_observation(self.scope, identifier, active.revision, asset.media_id,
                                                        {'status': 'ready', 'description': 'Le même dessin', 'ocr': [{'text': 'fantastique'}]}))
        self.assertEqual(len(self.journal.search(self.scope, 'fantastique')), 2)
        rows = self.journal.observations(self.scope, media_id=asset.media_id)
        self.assertEqual({row['author_id'] for row in rows}, {'alice', 'bob'})
        self.assertFalse(self.journal.observations(Scope('guild', 'public')))
        self.journal.ingest(replace(self.message(), edited_at=(self.moment + timedelta(seconds=1)).isoformat()))
        self.assertEqual(len(self.journal.observations(self.scope)), 1)

    def test_observation_requires_exact_revision_and_actual_attachment(self):
        active = self.journal.ingest(self.message())
        self.assertFalse(self.journal.save_observation(self.scope, 'one', active.revision, 'foreign', {'description': 'secret'}))
        self.assertFalse(self.journal.save_observation(self.scope, 'one', 99, 'foreign', {'description': 'secret'}))
        self.assertFalse(self.journal.search(self.scope, 'secret'))

    def test_query_filters_time_author_and_fts_syntax_is_data(self):
        self.journal.ingest(self.message('one', 'alice'))
        self.journal.ingest(self.message('two', 'bob', 'AAIT : proposition lundi',
                                        created_at=(self.moment - timedelta(days=2)).isoformat()))
        self.assertEqual([m.message_id for m in self.journal.search(self.scope, 'AAIT', author_id='alice')], ['one'])
        self.assertEqual([m.message_id for m in self.journal.search(self.scope, 'AAIT', before=self.moment.isoformat())], ['two'])
        self.assertFalse(self.journal.search(self.scope, '" : NEAR ( ) ***'))

    def test_backfill_is_silent_and_coverage_records_holes(self):
        self.journal.ingest(self.message(), backfill=True)
        coverage = self.journal.record_coverage(self.scope, self.moment - timedelta(days=7), self.moment,
                                                complete=False, reason='page_limit')
        self.assertFalse(coverage['complete'])
        self.assertEqual(coverage['gaps'][0]['reason'], 'page_limit')
        self.assertFalse(coverage['offline_mutations_complete'])
        with closing(sqlite3.connect(self.journal.path)) as db, db:
            self.assertFalse(db.execute('SELECT * FROM outbound_responses').fetchall())

    def test_complete_scan_reconciles_missing_messages_but_protects_live_race(self):
        self.journal.ingest(self.message('old', created_at=(self.moment - timedelta(days=1)).isoformat()))
        start, end = self.moment - timedelta(days=7), self.moment
        self.moment += timedelta(seconds=1)
        self.journal.ingest(self.message('racing', created_at=(end - timedelta(hours=1)).isoformat()))
        self.assertEqual(self.journal.reconcile_window(self.scope, start, end, []), 1)
        self.assertIsNone(self.journal.active(self.scope, 'old'))
        self.assertIsNotNone(self.journal.active(self.scope, 'racing'))

    def test_retention_purges_text_and_does_not_touch_other_ledgers(self):
        for name in ('state.db', 'luna-usage.db'):
            (self.root / name).write_bytes(b'unchanged-private-fixture')
        self.journal.ingest(self.message())
        self.moment += timedelta(days=91)
        self.assertEqual(self.journal.purge(), 1)
        self.assertFalse(self.journal.recent(self.scope))
        for name in ('state.db', 'luna-usage.db'):
            self.assertEqual((self.root / name).read_bytes(), b'unchanged-private-fixture')
        with self.assertRaises(ValueError):
            Journal(self.root / 'short.db', retention_days=6)

    def test_backup_migration_copy_future_schema_rejected_and_rollback_usable(self):
        self.journal.ingest(self.message())
        backup = self.journal.backup_to(self.root / 'rollback-copy.db')
        Journal(backup, now=lambda: self.moment)
        with closing(sqlite3.connect(backup)) as db, db:
            db.execute("INSERT INTO schema_migrations VALUES('conversation',999,'future')")
        with self.assertRaises(RuntimeError):
            Journal(backup, now=lambda: self.moment)
        self.assertIsNotNone(self.journal.active(self.scope, 'one'))
        clean = self.journal.backup_to(self.root / 'second-copy.db')
        self.assertEqual(Journal(clean, now=lambda: self.moment).active(self.scope, 'one').text,
                         self.message().text)

    def test_outbound_commit_is_durable_and_uncertain_never_reprepared(self):
        message = self.journal.ingest(self.message())
        snapshot = ConversationSnapshot('turn', 'conv', 1, self.scope, (message,), ('one',),
                                        ('alice',), 'one', 'AAIT', (('one', 1),))
        self.assertTrue(self.journal.prepare_outbound(snapshot, 'dedupe'))
        self.assertFalse(self.journal.prepare_outbound(snapshot, 'dedupe'))
        self.assertTrue(self.journal.commit_outbound('dedupe'))
        self.assertFalse(self.journal.commit_outbound('dedupe'))
        restarted = Journal(self.journal.path, now=lambda: self.moment)
        self.assertEqual(len(restarted.uncertain(self.scope)), 1)
        restarted.mark_outbound('dedupe', 'uncertain')
        self.assertFalse(restarted.prepare_outbound(snapshot, 'dedupe'))
        restarted.mark_outbound('dedupe', 'sent', 'receipt')
        self.assertEqual(restarted.outbound('dedupe')['message_id'], 'receipt')
        self.assertFalse(restarted.uncertain(self.scope))

    def test_disjoint_coverage_keeps_exact_gap_and_later_scan_fills_it(self):
        def day(number):
            return self.moment.replace(day=number, hour=0)
        self.journal.record_coverage(self.scope, day(1), day(2), complete=True)
        result = self.journal.record_coverage(self.scope, day(10), day(17), complete=True)
        self.assertFalse(result['complete'])
        self.assertEqual(len(result['intervals']), 2)
        self.assertEqual([(gap['start'], gap['end']) for gap in result['gaps']],
                         [(day(2).isoformat(), day(10).isoformat())])
        self.assertFalse(self.journal.is_covered(self.scope, day(5), day(6)))
        restarted = Journal(self.journal.path, now=lambda: self.moment)
        self.assertFalse(restarted.is_covered(self.scope, day(5), day(6)))
        result = restarted.record_coverage(self.scope, day(2), day(10), complete=True)
        self.assertTrue(result['complete'])
        self.assertFalse(result['gaps'])
        self.assertEqual(len(result['intervals']), 1)
        self.assertTrue(restarted.is_covered(self.scope, day(5), day(6)))

    def test_partial_request_and_confirmed_prefix_preserve_unread_suffix(self):
        start = self.moment - timedelta(days=7)
        prefix_end = self.moment - timedelta(days=5)
        self.journal.record_coverage(self.scope, start, self.moment, complete=False, reason='limit')
        partial = self.journal.record_coverage(self.scope, start, prefix_end, complete=True)
        self.assertFalse(partial['complete'])
        self.assertEqual([(gap['start'], gap['end'], gap['reason']) for gap in partial['gaps']],
                         [(prefix_end.isoformat(), self.moment.isoformat(), 'limit')])
        self.journal.checkpoint(self.scope, cursor='last-confirmed-id', first_at=start, last_at=prefix_end)
        self.assertEqual(self.journal.coverage(self.scope)['cursor'], 'last-confirmed-id')
        self.assertFalse(self.journal.coverage(self.scope)['complete'])
        complete = self.journal.record_coverage(self.scope, prefix_end, self.moment, complete=True)
        self.assertTrue(complete['complete'])
        self.assertFalse(complete['gaps'])

    def test_legacy_min_max_coverage_is_conservatively_migrated_on_copy(self):
        self.journal.ingest(self.message())
        self.journal.checkpoint(self.scope, 'legacy', first_at=self.moment - timedelta(days=7), last_at=self.moment)
        copy = self.journal.backup_to(self.root / 'legacy-copy.db')
        with closing(sqlite3.connect(copy)) as db, db:
            db.execute('DROP TABLE coverage_windows')
            db.execute("DELETE FROM schema_migrations WHERE component='conversation' AND version=2")
            db.execute('UPDATE ingestion_cursors SET complete=1')
        upgraded = Journal(copy, now=lambda: self.moment)
        self.assertIsNotNone(upgraded.active(self.scope, 'one'))
        self.assertFalse(upgraded.coverage(self.scope)['complete'])
        self.assertEqual(upgraded.coverage(self.scope)['gaps'][0]['reason'], 'legacy_coverage_unverified')
        self.assertEqual(upgraded.coverage(self.scope)['cursor'], 'legacy')
        self.assertFalse(self.journal.coverage(self.scope)['complete'])
