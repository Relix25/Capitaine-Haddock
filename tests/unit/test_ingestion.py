"""Synthetic Gateway events: no socket, service token or Discord message sent."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from courswatch.conversation.contracts import Scope
from courswatch.conversation.ingestion import envelope_from_message
from courswatch.discord_app.bot import CourseBot


class IngestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_uses_registered_commands_and_preserves_tone_preferences(self):
        retired=self.bot.conversation_command_help('Haddock, comment utiliser /modele ?')
        self.assertIn('retirée',retired)
        self.assertIn('/settings',retired)
        help_text=self.bot.conversation_command_help('Quelles commandes existent ?')
        for command in self.bot.tree.get_commands(guild=self.bot.scope):
            self.assertIn('/'+command.name+' : '+command.description,help_text)
        self.assertEqual(self.bot.conversation_command_help('Haddock, /jurons rares'), '')

    async def test_scan_starts_module_from_repository_and_keeps_override_directory(self):
        import os
        command = self.bot.tree.get_command('actualiser', guild=self.bot.scope)
        self.bot.authorize = AsyncMock(return_value=True)
        self.bot.finish_manual_scan = MagicMock()
        interaction = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()),
                                      followup=SimpleNamespace(send=AsyncMock()))
        configs = [({}, Path(__file__).resolve().parents[2]), ({'COURS_WATCH_COLLECTOR':'old.py'}, None)]
        for environment, cwd in configs:
            with self.subTest(cwd=cwd), patch.dict(os.environ, environment, clear=True), \
                 patch('courswatch.discord_app.bot.asyncio.create_subprocess_exec', side_effect=OSError) as spawn:
                await command.callback(interaction)
            self.assertEqual(spawn.await_args.kwargs.get('cwd'), cwd)
            self.assertEqual(spawn.await_args.args[1], '-m' if cwd else 'old.py')
        self.assertTrue(interaction.response.defer.await_args.kwargs['ephemeral'])

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = {'discord': {'guild_id':'1', 'channel_id':'2', 'application_id':'99',
            'role_ids':['3'], 'luna':{'enabled':False}, 'easter_egg':{'enabled':False}}}
        self.config_patch = patch('courswatch.discord_app.bot.configuration', return_value=self.cfg)
        self.config_patch.start()
        self.bot = CourseBot('synthetic')
        self.bot.write_status = MagicMock()

    async def asyncTearDown(self):
        await self.bot.close()
        self.config_patch.stop()
        self.temp.cleanup()

    def message(self, **changes):
        values = dict(id=10, guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=2),
            author=SimpleNamespace(id=4, bot=False, display_name='Alice', roles=[SimpleNamespace(id=3)]),
            content='Question synthétique', mentions=[], attachments=[], embeds=[], reference=None,
            webhook_id=None, created_at=datetime(2026, 11, 10, 10, tzinfo=timezone.utc), edited_at=None)
        values.update(changes)
        return SimpleNamespace(**values)

    def attach_chat(self, old=None):
        self.bot.luna = SimpleNamespace(journal=SimpleNamespace(active=MagicMock(return_value=old),
            coverage=MagicMock(return_value={}), checkpoint=MagicMock(),
            reconcile_window=MagicMock(), record_coverage=MagicMock(), purge=MagicMock()),
            observe=MagicMock(), edited=MagicMock(), deleted=MagicMock(), close=AsyncMock(),
            invalidate=MagicMock(), invalidation=MagicMock(return_value=None), revalidated=MagicMock())
        return self.bot.luna

    async def test_normalizes_media_without_text_and_preserves_identity_and_reply(self):
        message = self.message(content='', attachments=[SimpleNamespace(id=20, url='https://cdn.discordapp.com/attachments/1/2/tiny.png',
            filename='tiny.png', content_type='image/png', size=64)], reference=SimpleNamespace(message_id=8))
        result = envelope_from_message(message)
        self.assertEqual((result.author_id, result.reply_to_id, result.created_at), ('4', '8', '2026-11-10T10:00:00+00:00'))
        self.assertEqual(len(result.media), 1)
        self.assertIn('/1/2/10', result.source_url)
        self.assertEqual(result.text, '')

    async def test_raw_partial_update_fetches_complete_message_and_never_retargets(self):
        original = envelope_from_message(self.message())
        chat = self.attach_chat(original)
        message = self.message(embeds=[SimpleNamespace(image=SimpleNamespace(url='https://cdn.discordapp.com/x.png'))])
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        with patch.object(self.bot, 'get_channel', return_value=channel):
            await self.bot.on_raw_message_edit(SimpleNamespace(guild_id=1, channel_id=2, message_id=10, data={'embeds':[]}))
        updated = chat.edited.call_args.args[0]
        self.assertEqual(updated.text, original.text)
        self.assertEqual(updated.author_id, '4')
        chat.observe.assert_not_called()

    async def test_scope_and_current_authorization_block_raw_updates(self):
        chat = self.attach_chat(envelope_from_message(self.message()))
        fetch = AsyncMock(return_value=self.message(author=SimpleNamespace(id=4, bot=False, roles=[], display_name='Alice')))
        with patch.object(self.bot, 'get_channel', return_value=SimpleNamespace(fetch_message=fetch)):
            await self.bot.on_raw_message_edit(SimpleNamespace(guild_id=1, channel_id=9, message_id=10))
            fetch.assert_not_awaited()
            await self.bot.on_raw_message_edit(SimpleNamespace(guild_id=1, channel_id=2, message_id=10))
        chat.edited.assert_not_called()
        chat.invalidate.assert_called_with(Scope('1','2'), '10')
        chat.deleted.assert_not_called()

    async def test_temporary_member_failure_does_not_delete_message_on_raw_edit(self):
        original = envelope_from_message(self.message())
        chat = self.attach_chat(original)
        changed = self.message(content='Correction synthétique', edited_at=datetime(2026, 11, 10, 11, tzinfo=timezone.utc))
        changed.guild.fetch_member = AsyncMock(side_effect=OSError('synthetic outage'))
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=changed))
        payload = SimpleNamespace(guild_id=1, channel_id=2, message_id=10)
        with patch.object(self.bot, 'get_channel', return_value=channel):
            await self.bot.on_raw_message_edit(payload)
            chat.deleted.assert_not_called()
            chat.edited.assert_not_called()
            chat.invalidate.assert_called_with(original.scope, original.message_id)
            changed.guild.fetch_member.side_effect = None
            changed.guild.fetch_member.return_value = changed.author
            await self.bot.on_raw_message_edit(payload)
        self.assertEqual(chat.edited.call_args.args[0].text, changed.content)

    async def test_backfill_is_silent_and_scope_checked(self):
        chat = self.attach_chat()
        message = self.message(content='Haddock, ancienne demande')
        async def history(**kwargs):
            self.assertEqual(kwargs['limit'], 1000)
            yield message
        channel = SimpleNamespace(id=2, history=history)
        with patch.object(self.bot, 'get_guild', return_value=None):
            await self.bot.backfill_conversations(channel)
        envelope = chat.observe.call_args.args[0]
        self.assertEqual(envelope.ingestion_mode, 'backfill')
        self.assertIsNone(chat.observe.call_args.kwargs['message'])
        self.assertFalse(chat.observe.call_args.kwargs['mentioned'])

    async def test_complete_backfill_reconciles_missing_source_without_replaying(self):
        from courswatch.memory import Journal
        now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        journal = Journal(Path(self.temp.name)/'conversation.db', now=lambda:now)
        created = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)
        source = envelope_from_message(self.message(id=discord.utils.time_snowflake(created), created_at=created), now=now)
        journal.ingest(source)
        self.attach_chat().journal = journal
        async def empty_history(**kwargs):
            if False:
                yield None
        channel = SimpleNamespace(id=2, history=empty_history)
        with patch.object(self.bot, 'get_guild', return_value=None), \
                patch('courswatch.discord_app.bot.datetime', wraps=datetime) as clock:
            clock.now.return_value = now
            await self.bot.backfill_conversations(channel)
        self.assertIsNone(journal.active(source.scope, source.message_id))
        self.assertTrue(journal.coverage(source.scope)['complete'])
        self.bot.luna.observe.assert_not_called()

    async def test_first_history_failure_is_retried_without_a_cursor(self):
        chat = self.attach_chat()
        chat.media = None
        attempts = []

        async def history(**kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise OSError('history temporarily unavailable')
            for message in ():
                yield message

        channel = SimpleNamespace(id=2, history=history)
        with patch.object(self.bot, 'get_guild', return_value=None), \
                patch.object(self.bot, 'get_channel', return_value=channel):
            await self.bot.backfill_conversations(channel)
            self.assertTrue(self.bot.history_retry_needed)
            self.assertFalse(chat.journal.checkpoint.called)
            with patch.object(self.bot, 'is_closed', side_effect=[False, True]), \
                    patch('courswatch.discord_app.bot.asyncio.sleep', new_callable=AsyncMock):
                await self.bot.conversation_maintenance()
            await self.bot.history_task
        self.assertEqual(len(attempts), 2)
        self.assertFalse(self.bot.history_retry_needed)
        self.assertEqual(self.bot.write_status.call_args.kwargs['conversation_backfill']['status'], 'complete')

    async def test_atomic_status_writers_preserve_independent_fields(self):
        from concurrent.futures import ThreadPoolExecutor
        import json
        # Use the actual writer, under a local fixture root.
        self.bot.write_status = CourseBot.write_status.__get__(self.bot)
        with patch('courswatch.discord_app.bot.DATA', Path(self.temp.name)):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda index:self.bot.write_status(**{'phase_'+str(index):index}), range(16)))
            report = json.loads((Path(self.temp.name)/'reports/bot-status.json').read_text())
            self.assertEqual([report['phase_'+str(index)] for index in range(16)], list(range(16)))
            self.assertEqual(list((Path(self.temp.name)/'reports').glob('.bot-status-*')), [])

    async def test_publish_rechecks_role_and_detects_source_correction(self):
        message = self.message()
        self.attach_chat(envelope_from_message(message))
        member = SimpleNamespace(id=4, roles=[])
        message.guild.fetch_member = AsyncMock(return_value=member)
        message.channel.fetch_message = AsyncMock(return_value=message)
        self.assertFalse(await self.bot.conversation_publish_allowed(message))
        member.roles = [SimpleNamespace(id=3)]
        message.channel.fetch_message.return_value = self.message(content='Date corrigée', guild=message.guild)
        self.assertFalse(await self.bot.conversation_publish_allowed(message))
        self.bot.luna.edited.assert_called_once()

    async def test_rest_user_is_authorized_by_fetched_member_without_new_intent(self):
        original = self.message()
        self.attach_chat(envelope_from_message(original))
        rest = self.message(author=SimpleNamespace(id=4, bot=False, display_name='Alice'))
        member = original.author
        rest.guild.fetch_member = AsyncMock(return_value=member)
        original.channel.fetch_message = AsyncMock(return_value=rest)
        original.guild.fetch_member = AsyncMock(return_value=member)
        self.assertTrue(await self.bot.conversation_publish_allowed(original))
        self.assertFalse(self.bot.intents.members)
        rest.guild.fetch_member.assert_awaited_once_with(4)
        original.guild.fetch_member.assert_not_awaited()

    async def test_current_versions_validate_shared_author_and_reject_changed_ancestor_embed(self):
        author = self.message().author
        guild = SimpleNamespace(id=1, fetch_member=AsyncMock(return_value=author))
        def embed(path):
            return [SimpleNamespace(image=SimpleNamespace(url='https://cdn.discordapp.com/' + path))]
        old = self.message(guild=guild, embeds=embed('a.png'))
        target = self.message(id=11, guild=guild, reference=SimpleNamespace(message_id=10))
        active = {str(message.id): envelope_from_message(message) for message in (old, target)}
        chat = self.attach_chat()
        chat.journal.active.side_effect = lambda scope, message_id: active.get(message_id)
        snapshot = SimpleNamespace(scope=Scope('1','2'), reply_target_id='11', dependencies=(('10',1),('11',1)))
        current = {10:old, 11:target}
        async def fetch_message(identity):
            return current[identity]
        target.channel.fetch_message = AsyncMock(side_effect=fetch_message)
        self.assertTrue(await self.bot.conversation_dependencies_allowed(snapshot, None, target))
        guild.fetch_member.assert_awaited_once_with(author.id)
        current[10] = self.message(guild=guild, embeds=embed('b.png'))
        self.assertFalse(await self.bot.conversation_dependencies_allowed(snapshot, None, target, publishing=True))
        self.assertEqual(chat.edited.call_args.args[0].media[0].url, 'https://cdn.discordapp.com/b.png')
        self.assertEqual(chat.edited.call_args.args[0].event_kind, 'rest')

    async def test_partial_backfill_continues_from_checkpoint_instead_of_first_page(self):
        from courswatch.memory import Journal
        now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        self.cfg['discord']['luna']['memory'] = {'backfill_limit':2}
        journal = Journal(Path(self.temp.name)/'conversation.db', now=lambda:now)
        chat = self.attach_chat()
        chat.journal = journal
        chat.observe.side_effect = lambda envelope, **kwargs:journal.ingest(envelope, backfill=True)
        created = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
        base_id = discord.utils.time_snowflake(created)
        # All messages share a timestamp. The missing messages span both sides
        # of the page cursor, including the prefix and the final page.
        messages = [self.message(id=base_id+i, created_at=created) for i in (1,3,5)]
        for offset in (2,4):
            journal.ingest(envelope_from_message(self.message(id=base_id+offset, created_at=created), now=now))
        after_values=[]
        async def history(**kwargs):
            after_values.append(kwargs['after'])
            chosen=[message for message in messages if message.id > kwargs['after'].id]
            for message in chosen[:kwargs['limit']]:
                yield message
        channel=SimpleNamespace(id=2, history=history)
        with patch.object(self.bot, 'get_guild', return_value=None), \
                patch('courswatch.discord_app.bot.datetime', wraps=datetime) as clock:
            clock.now.return_value=now
            await self.bot.backfill_conversations(channel)
            self.assertFalse(journal.coverage(Scope('1','2'))['complete'])
            self.assertIsNone(journal.active(Scope('1','2'),str(base_id+2)))
            self.assertIsNotNone(journal.active(Scope('1','2'),str(base_id+4)))
            await self.bot.resume_conversation_backfill(channel)
        self.assertEqual(after_values[1].id,base_id+3)
        self.assertIsNone(journal.active(Scope('1','2'),str(base_id+4)))
        self.assertIsNotNone(journal.active(Scope('1','2'),str(base_id+5)))
        self.assertTrue(journal.coverage(Scope('1','2'))['complete'])
        self.assertEqual(journal.coverage(Scope('1','2'))['cursor'],'')

    async def test_media_memory_citation_survives_runtime_publication_guard_at_day_seven(self):
        from courswatch.memory import Journal
        from courswatch.knowledge import KnowledgeService
        now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        message = self.message(content='', attachments=[SimpleNamespace(id=20,
            url='https://cdn.discordapp.com/attachments/1/2/tiny.gif', filename='tiny.gif',
            content_type='image/gif', size=64)])
        journal = Journal(Path(self.temp.name)/'conversation.db', now=lambda:now)
        envelope = journal.ingest(envelope_from_message(message, now=now))
        journal.save_observation(envelope.scope, envelope.message_id, envelope.revision,
            envelope.media[0].media_id, {'status':'ready', 'description':'Une luciole apparaît puis clignote.',
                'ocr':[], 'animation':'Le point lumineux change.', 'limitations':['Trois instants examinés.']})
        # Reopen both services, then exercise the actual raw-event/publication adapter.
        journal = Journal(journal.path, now=lambda:now)
        service = KnowledgeService(Path(self.temp.name), {}, journal=journal, clock=lambda:now)
        bundle = service.query(envelope.scope, 'Haddock, le GIF luciole envoyé par Alice la semaine dernière')
        self.assertTrue(any(item.kind == 'media_observation' for item in bundle.evidence))
        self.attach_chat().journal = journal
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        with patch.object(self.bot, 'get_channel', return_value=channel):
            self.assertTrue(await self.bot.conversation_evidence_allowed(bundle))
            journal.delete(envelope.scope, envelope.message_id)
            self.assertFalse(await self.bot.conversation_evidence_allowed(bundle))
