"""Local Pillow decoding and synthetic vision contracts; no provider vision tests."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import io
import sqlite3
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from courswatch.conversation.contracts import (MediaDescriptor, MessageEnvelope,
                                               ModelCapabilities, ModelProfile, Scope)
from courswatch.media import normalize_media
from courswatch.media.acquisition import (AcquiredMedia, MediaUnavailable, _NoRedirect,
                                          fetch_media, validate_url)
from courswatch.media.decode import DecodeLimits, decode_media
from courswatch.media.service import MediaCache, MediaService, vision_available


FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures' / 'media'
PROFILE = ModelProfile('deepseek', 'commandcode',
                       'https://api.commandcode.ai/provider/v1/responses',
                       'deepseek/deepseek-v4.1-flash', True,
                       capabilities=ModelCapabilities(image_input='declared'))


class FakeJournal:
    """Occurrence-authorized journal adapter, never evidence of provider support."""
    def __init__(self):
        self.messages = {}
        self.saved = {}

    def active(self, scope, identity):
        return self.messages.get((scope.key, identity))

    def add(self, envelope):
        self.messages[(envelope.scope.key, envelope.message_id)] = envelope
        return envelope

    def save_observation(self, scope, message_id, revision, media_id, observation):
        row = self.active(scope, message_id)
        if (not row or row.deleted or row.revision != revision or
                not any(media.media_id == media_id for media in row.media)):
            return False
        self.saved[(scope.key, message_id, media_id)] = observation
        return True

    def observations(self, scope, message_id=None, media_id=None):
        return tuple({'observation': value} for key, value in self.saved.items()
                     if key[0] == scope.key and (message_id is None or key[1] == message_id)
                     and (media_id is None or key[2] == media_id))


def message(identity='m1', *, author='alice', scope=None, media=None, revision=1):
    return MessageEnvelope(scope or Scope('guild', 'channel'), identity, author,
                           'Même nom', '', '2026-11-10T18:30:00+00:00',
                           revision=revision,
                           source_url='https://discord.com/channels/guild/channel/' + identity,
                           media=media or (MediaDescriptor('a1', 'image',
                               'https://cdn.discordapp.com/attachments/1/2/test.png'),))


def description(frames, profile):
    # Explicit fake transcription. It tests schema/provenance, not real OCR.
    return {'visual_description': 'Dessin synthétique sur fond clair.',
            'visible_text': [{'text': 'TEST 2099', 'at_ms': frames[-1]['at_ms'], 'quality': 'readable'}],
            'animation_summary': 'Les couleurs changent.' if len(frames) > 1 else '',
            'limitations': ['Description et OCR simulés pour le test.'],
            'provenance': {'resolved_model_revision': None}}


class NormalizeTests(unittest.TestCase):
    def test_image_only_message_and_distinct_attachment_positions(self):
        source = SimpleNamespace(content='', embeds=[], attachments=[
            SimpleNamespace(id=1, url='https://cdn.discordapp.com/a.png', content_type='image/png', size=10, filename='A.png'),
            SimpleNamespace(id=2, url='https://cdn.discordapp.com/b.gif', content_type='image/gif', size=20, filename='B.gif')])
        rows = normalize_media(source)
        self.assertEqual([row.media_id for row in rows], ['attachment:1', 'attachment:2'])
        self.assertEqual([row.position for row in rows], [0, 1])
        self.assertEqual([row.kind for row in rows], ['image', 'animation'])

    def test_custom_emoji_name_not_a_description_and_animation_is_preserved(self):
        source = SimpleNamespace(content='<:heureux:12345> <a:heureux:67890> <:heureux:12345>',
                                 attachments=[], embeds=[])
        rows = normalize_media(source)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row.media_id for row in rows}), 3)
        self.assertEqual(rows[0].emoji_id, '12345')
        self.assertTrue(rows[1].animated)
        self.assertIn('.webp?', rows[1].url)
        self.assertIn('animated=true', rows[1].url)
        self.assertFalse(hasattr(rows[0], 'description'))

    def test_video_embed_is_not_misrepresented_as_animation_and_page_not_crawled(self):
        source = SimpleNamespace(content='https://tenor.com/view/test-123', attachments=[],
                                 embeds=[{'video': {'url': 'https://media.tenor.com/test.mp4'},
                                          'thumbnail': {'url': 'https://media.tenor.com/still.png'}}])
        self.assertEqual([row.kind for row in normalize_media(source)], ['video', 'link'])


class AcquisitionTests(unittest.TestCase):
    def test_allowlist_refuses_private_urls_credentials_ports_and_lookalike_hosts(self):
        for url in ('http://cdn.discordapp.com/a.png', 'https://127.0.0.1/a.png',
                    'https://cdn.discordapp.com.evil.invalid/a.png',
                    'https://user@cdn.discordapp.com/a.png', 'https://cdn.discordapp.com:444/a.png',
                    'https://cdn.discordapp.com/a.png#secret', 'https://[malformed'):
            with self.subTest(url=url), self.assertRaises(MediaUnavailable):
                validate_url(url)
        self.assertEqual(validate_url('https://cdn.discordapp.com/emojis/123.webp?animated=true'),
                         'https://cdn.discordapp.com/emojis/123.webp?animated=true')

    def test_redirects_are_refused_without_following_new_destination(self):
        with self.assertRaisesRegex(MediaUnavailable, 'redirect_not_allowed'):
            _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://127.0.0.1/private')

    def test_byte_and_content_type_limits_before_decode(self):
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'image/png'}
        client = SimpleNamespace(open=lambda *args, **kwargs: Response(b'12345'))
        with self.assertRaisesRegex(MediaUnavailable, 'source_too_large'):
            fetch_media(message().media[0], max_bytes=4, opener=client)
        client = SimpleNamespace(open=lambda *args, **kwargs: Response(b'123'))
        result = fetch_media(message().media[0], max_bytes=4, opener=client)
        self.assertEqual(result.data, b'123')
        oversized = replace(message().media[0], size_bytes=100)
        opener = Mock()
        with self.assertRaises(MediaUnavailable):
            fetch_media(oversized, max_bytes=4, opener=opener)
        opener.open.assert_not_called()


class DecodeTests(unittest.TestCase):
    def test_static_actual_format_and_not_filename_or_claimed_type(self):
        result = decode_media((FIXTURES / 'synthetic-date.png').read_bytes())
        self.assertEqual(result.actual_format, 'PNG')
        self.assertEqual(result.sampled_at_ms, (0,))
        self.assertEqual(result.frames[0]['mime_type'], 'image/png')
        with self.assertRaisesRegex(MediaUnavailable, 'invalid_image'):
            decode_media(b'<html>not an image</html>')

    def test_gif_blank_first_frame_and_brief_late_text_are_sampled_by_time(self):
        result = decode_media((FIXTURES / 'late-overlay.gif').read_bytes())
        self.assertEqual(result.actual_format, 'GIF')
        self.assertEqual(result.frame_count, 4)
        self.assertEqual(result.duration_ms, 1140)
        self.assertIn(700, result.sampled_at_ms)
        self.assertEqual(len(result.frames), 4)
        self.assertTrue(any('mouvements' in limit for limit in result.limitations))

    def test_animated_webp_is_reconstructed_as_multiple_png_frames(self):
        result = decode_media((FIXTURES / 'animated-emoji.webp').read_bytes())
        self.assertEqual(result.actual_format, 'WEBP')
        self.assertEqual(len(result.frames), 4)
        self.assertEqual(result.sampled_at_ms, (0, 100, 700, 740))

    def test_pixels_frames_duration_and_decode_deadline_are_bounded(self):
        data = (FIXTURES / 'late-overlay.gif').read_bytes()
        with self.assertRaisesRegex(MediaUnavailable, 'pixel_limit'):
            decode_media(data, limits=DecodeLimits(max_pixels=10))
        with self.assertRaisesRegex(MediaUnavailable, 'decoded_pixel_limit'):
            decode_media(data, limits=DecodeLimits(max_total_pixels=96 * 64))
        partial = decode_media(data, limits=DecodeLimits(max_duration_ms=200))
        self.assertLess(partial.frame_count, 4)
        self.assertLessEqual(partial.duration_ms, 200)
        self.assertTrue(any('fenêtre' in limit for limit in partial.limitations))
        single = decode_media(data, limits=DecodeLimits(max_frames=1))
        self.assertEqual(len(single.frames), 1)
        times = iter((0, 3))
        with self.assertRaisesRegex(MediaUnavailable, 'decode_deadline'):
            decode_media(data, clock=lambda: next(times))


class MediaCacheRetentionTests(unittest.TestCase):
    def test_reads_physically_purge_expired_sources_and_descriptions_without_new_success(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0]
            path = Path(directory) / 'media-cache.db'
            cache = MediaCache(path, now=lambda: now[0])
            scope = Scope('guild', 'channel')
            cache.put(scope, 'source', 'profile', 'hash', {'description': 'EXPIRED_PRIVATE_DESCRIPTION'})
            now[0] = 86401
            self.assertIsNone(cache.by_source(scope, 'source', 'profile'))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM source_cache').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], 1)
            now[0] = 90 * 86400 + 1
            self.assertIsNone(cache.get(scope, 'hash'))
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], 0)
            self.assertNotIn(b'EXPIRED_PRIVATE_DESCRIPTION', path.read_bytes())

    def test_startup_purges_expiry_and_large_backlog_is_drained_in_bounded_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            now = [0]
            path = Path(directory) / 'media-cache.db'
            cache = MediaCache(path, now=lambda: now[0])
            scope = Scope('guild', 'channel')
            cache.put(scope, 'source', 'profile', 'hash', {'description': 'old'})
            now[0] = 90 * 86400 + 1
            cache = MediaCache(path, now=lambda: now[0])
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM source_cache').fetchone()[0], 0)
                db.executemany('INSERT INTO analyses VALUES(?,?,?,?)',
                               [(scope.key, str(index), '{}', 0) for index in range(300)])
                db.commit()
            self.assertEqual(cache.purge(force=True, batch_size=128)['analyses'], 128)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], 172)
            cache.get(scope, 'missing')  # Backlog continues on a read; no put.
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0], 0)


class MediaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.journal = FakeJournal()
        self.services = []

    async def asyncTearDown(self):
        for service in self.services:
            await service.close()
        self.temp.cleanup()

    def service(self, **kwargs):
        values = dict(describer=description, profile_resolver=lambda: PROFILE,
                      fetcher=lambda media: (FIXTURES / 'synthetic-date.png').read_bytes(), trusted_test=True)
        values.update(kwargs)
        service = MediaService(self.root, self.journal, **values)
        self.services.append(service)
        return service

    async def test_image_only_remains_silent_and_observation_keeps_attribution_source_and_ocr(self):
        envelope = self.journal.add(message())
        result = (await self.service().enrich(envelope))[0]
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['author_id'], 'alice')
        self.assertEqual(result['sent_at'], envelope.created_at)
        self.assertEqual(result['source_url'], envelope.source_url)
        self.assertEqual(result['ocr'][0]['text'], 'TEST 2099')
        self.assertEqual(result['course_fact_status'], 'not_verified')
        self.assertEqual(result['provenance']['requested_model_id'], PROFILE.requested_model_id)
        self.assertTrue(result['provenance']['test_adapter'])
        self.assertFalse(result['provenance']['provider_vision_verified'])
        self.assertNotIn('reply', result)

    async def test_unknown_model_or_api_vision_keeps_explicit_unavailable_state_without_fetch(self):
        envelope = self.journal.add(message())
        fetcher, describer = Mock(), Mock()
        service = self.service(trusted_test=False, fetcher=fetcher, describer=describer)
        result = (await service.enrich(envelope))[0]
        self.assertEqual(result['reason'], 'capability_unavailable')
        fetcher.assert_not_called()
        describer.assert_not_called()
        self.assertFalse(vision_available(PROFILE))

    async def test_explicit_captured_unavailable_profile_never_reresolves_after_settings_switch(self):
        envelope = self.journal.add(message())
        # The turn captured no permitted vision route. Settings then select a
        # different profile, which a new background request could resolve.
        selected_after_switch = replace(PROFILE, profile_id='hy4',
                                        requested_model_id='tencent/hy4-flash', require_zdr=False)
        resolver = Mock(return_value=selected_after_switch)
        fetcher = Mock(return_value=(FIXTURES / 'synthetic-date.png').read_bytes())
        describer = Mock(side_effect=description)
        service = self.service(profile_resolver=resolver, fetcher=fetcher, describer=describer)
        unavailable = (await service.enrich(envelope, profile=None))[0]
        self.assertEqual(unavailable['reason'], 'capability_unavailable')
        resolver.assert_not_called()
        fetcher.assert_not_called()
        describer.assert_not_called()
        # Only omission, as used for autonomous background work, resolves it.
        ready = (await service.enrich(envelope))[0]
        self.assertEqual(ready['status'], 'ready')
        self.assertEqual(ready['provenance']['requested_model_id'], selected_after_switch.requested_model_id)
        resolver.assert_called_once_with()

    async def test_unavailable_or_deferred_job_can_retry_without_stale_failure_cache(self):
        envelope = self.journal.add(message())
        service = self.service(profile_resolver=lambda: None)
        unavailable = (await service.enrich(envelope))[0]
        self.assertEqual(unavailable['reason'], 'capability_unavailable')
        service.profile_resolver = lambda: PROFILE
        ready = (await service.enrich(envelope))[0]
        self.assertEqual(ready['status'], 'ready')
        self.assertFalse(ready['cache_hit'])

    async def test_budget_deferral_does_not_cache_a_success_or_repeat_in_same_attempt(self):
        class BudgetExhausted(RuntimeError):
            pass
        envelope = self.journal.add(message())
        describer = Mock(side_effect=BudgetExhausted())
        service = self.service(describer=describer)
        deferred = (await service.enrich(envelope))[0]
        self.assertEqual(deferred['status'], 'deferred_budget')
        self.assertEqual(describer.call_count, 1)
        describer.side_effect = description
        ready = (await service.enrich(envelope))[0]
        self.assertEqual(ready['status'], 'ready')
        self.assertFalse(ready['cache_hit'])

    async def test_analysis_timestamp_uses_controlled_clock(self):
        envelope = self.journal.add(message())
        observation = (await self.service(now=lambda: 0).enrich(envelope))[0]
        self.assertEqual(observation['analysed_at'], '1970-01-01T00:00:00+00:00')

    async def test_existing_dated_description_survives_expired_url_and_changed_model(self):
        envelope = self.journal.add(message())
        first = (await self.service(now=lambda: 0).enrich(envelope))[0]
        unavailable = Mock(side_effect=MediaUnavailable('source_expired'))
        service = self.service(fetcher=unavailable, now=lambda: 100_000)
        changed = replace(PROFILE, config_version='2', requested_model_id='tencent/hy4-flash', profile_id='hy4')
        second = (await service.enrich(envelope, changed))[0]
        unavailable.assert_not_called()
        self.assertTrue(second['prior_observation'])
        self.assertEqual(second['analysed_at'], first['analysed_at'])
        self.assertEqual(second['provenance']['requested_model_id'], PROFILE.requested_model_id)
        refreshed = (await service.enrich(envelope, changed, refresh=True))[0]
        self.assertEqual(refreshed['status'], 'ready')
        self.assertEqual(refreshed['recheck_reason'], 'source_expired')
        self.assertEqual(refreshed['analysed_at'], first['analysed_at'])

    async def test_test_adapter_cache_and_observation_never_count_as_production_vision(self):
        envelope = self.journal.add(message())
        await self.service().enrich(envelope)
        fetcher = Mock()
        production = self.service(trusted_test=False, fetcher=fetcher)
        result = (await production.enrich(envelope))[0]
        self.assertEqual(result['reason'], 'capability_unavailable')
        fetcher.assert_not_called()

    async def test_same_emoji_reuses_description_and_download_after_restart_but_occurrences_differ(self):
        fetcher = Mock(return_value=(FIXTURES / 'synthetic-date.png').read_bytes())
        describer = Mock(side_effect=description)
        first = self.journal.add(message('m1', author='alice'))
        second = self.journal.add(message('m2', author='bob'))
        a = (await self.service(fetcher=fetcher, describer=describer).enrich(first))[0]
        b = (await self.service(fetcher=fetcher, describer=describer).enrich(second))[0]
        self.assertFalse(a['cache_hit'])
        self.assertTrue(b['cache_hit'])
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(describer.call_count, 1)
        self.assertEqual(b['author_id'], 'bob')
        self.assertEqual(b['message_id'], 'm2')
        self.assertEqual(b['provenance']['source_message_ref'], 'm2')

    async def test_same_bytes_different_source_urls_join_one_analysis_in_flight(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def slow(frames, profile):
            calls.append(profile)
            started.set()
            await release.wait()
            return description(frames, profile)
        first = self.journal.add(message('m1'))
        second = self.journal.add(message('m2', author='bob', media=(
            replace(first.media[0], url='https://cdn.discordapp.com/attachments/1/3/other.png'),)))
        service = self.service(describer=slow)
        a = asyncio.create_task(service.enrich(first))
        await started.wait()
        b = asyncio.create_task(service.enrich(second))
        await asyncio.sleep(.03)
        release.set()
        results = await asyncio.gather(a, b)
        self.assertEqual(len(calls), 1)
        self.assertEqual(results[1][0]['author_id'], 'bob')

    async def test_scope_is_part_of_cache_and_active_occurrence_is_required(self):
        describer = Mock(side_effect=description)
        service = self.service(describer=describer)
        first = self.journal.add(message('m1'))
        other = self.journal.add(message('m2', scope=Scope('guild', 'private')))
        await service.enrich(first)
        await service.enrich(other)
        self.assertEqual(describer.call_count, 2)
        unknown = message('unknown')
        self.assertEqual(await service.enrich(unknown), ())

    async def test_profile_policy_and_version_are_cache_dimensions(self):
        describer = Mock(side_effect=description)
        service = self.service(describer=describer)
        envelope = self.journal.add(message())
        await service.enrich(envelope, PROFILE)
        await service.enrich(envelope, replace(PROFILE, config_version='2'), refresh=True)
        await service.enrich(envelope, replace(PROFILE, require_zdr=False), refresh=True)
        self.assertEqual(describer.call_count, 3)

    async def test_deleted_or_edited_occurrence_rejects_late_observation(self):
        for action in ('delete', 'edit'):
            with self.subTest(action=action):
                started, release = asyncio.Event(), asyncio.Event()
                async def slow(frames, profile):
                    started.set()
                    await release.wait()
                    return description(frames, profile)
                envelope = self.journal.add(message('m-' + action))
                service = self.service(describer=slow)
                task = asyncio.create_task(service.enrich(envelope))
                await started.wait()
                if action == 'delete':
                    self.journal.messages.pop((envelope.scope.key, envelope.message_id))
                else:
                    self.journal.add(replace(envelope, revision=2))
                release.set()
                self.assertEqual(await task, ())
                self.assertFalse(any(key[1] == envelope.message_id for key in self.journal.saved))

    async def test_access_removed_before_enrichment_never_uses_cache_or_fetch(self):
        envelope = self.journal.add(message())
        fetcher = Mock()
        service = self.service(fetcher=fetcher, authorize=lambda envelope: False)
        self.assertEqual(await service.enrich(envelope), ())
        fetcher.assert_not_called()

    async def test_multi_attachment_and_gif_each_keep_own_source_and_timestamps(self):
        media = (MediaDescriptor('still', 'image', 'https://cdn.discordapp.com/a.png'),
                 MediaDescriptor('gif', 'animation', 'https://cdn.discordapp.com/b.gif', position=1))
        envelope = self.journal.add(message(media=media))
        def fetcher(item):
            name = 'late-overlay.gif' if item.media_id == 'gif' else 'synthetic-date.png'
            return (FIXTURES / name).read_bytes()
        result = await self.service(fetcher=fetcher).enrich(envelope)
        self.assertEqual([row['media_id'] for row in result], ['still', 'gif'])
        self.assertEqual(result[0]['sampled_at_ms'], [0])
        self.assertIn(700, result[1]['sampled_at_ms'])
        self.assertEqual(result[1]['position'], 1)

    async def test_background_overflow_and_batch_limit_are_explicit_and_no_publication(self):
        envelope = self.journal.add(message())
        other = self.journal.add(message('m2'))
        service = self.service(queue_size=1)
        task = service.submit(envelope)
        self.assertIsNone(service.submit(other))
        row = self.journal.saved[(other.scope.key, 'm2', 'a1')]
        self.assertEqual(row['reason'], 'background_queue_full')
        await task
        many = self.journal.add(message('m3', media=tuple(
            MediaDescriptor(str(index), 'image', 'https://cdn.discordapp.com/a.png', position=index)
            for index in range(5))))
        result = await service.enrich(many)
        self.assertEqual(result[-1]['reason'], 'media_batch_limit')

    async def test_expired_url_can_refresh_once_from_authorized_message(self):
        envelope = self.journal.add(message())
        calls = []
        def fetcher(media):
            calls.append(media.url)
            if len(calls) == 1:
                raise MediaUnavailable('source_expired')
            return (FIXTURES / 'synthetic-date.png').read_bytes()
        refresh = Mock(return_value=replace(envelope.media[0], url='https://cdn.discordapp.com/refreshed.png'))
        result = await self.service(fetcher=fetcher, refresh_source=refresh).enrich(envelope)
        self.assertEqual(result[0]['status'], 'ready')
        self.assertEqual(len(calls), 2)
        refresh.assert_called_once()

    async def test_invalid_transcription_frame_is_not_persisted_as_ready(self):
        envelope = self.journal.add(message())
        def bad(frames, profile):
            return {'description': 'Texte', 'ocr': [{'text': '2099', 'at_ms': 99999, 'quality': 'readable'}],
                    'animation': '', 'limitations': []}
        row = (await self.service(describer=bad).enrich(envelope))[0]
        self.assertEqual(row['status'], 'unsupported')
        self.assertEqual(row['reason'], 'invalid_description')

    async def test_media_memory_recall_after_restart_has_right_occurrence_and_deletion_invalidates_index(self):
        from courswatch.memory.journal import Journal
        current = lambda: datetime(2026, 11, 17, tzinfo=timezone.utc)
        self.journal = Journal(self.root / 'conversation.db', now=current)
        first = self.journal.ingest(message('m1', author='alice'))
        second = self.journal.ingest(message('m2', author='bob'))
        service = self.service()
        await service.enrich(first)
        await service.enrich(second)
        restarted = Journal(self.root / 'conversation.db', now=current)
        matches = restarted.search(first.scope, '2099', author_id='alice')
        self.assertEqual([row.message_id for row in matches], ['m1'])
        observation = restarted.observations(first.scope, 'm1')[0]
        self.assertEqual(observation['author_id'], 'alice')
        self.assertEqual(observation['created_at'], first.created_at)
        self.assertEqual(observation['observation']['ocr'][0]['text'], 'TEST 2099')
        restarted.delete(first.scope, 'm1')
        self.assertEqual(restarted.observations(first.scope, 'm1'), ())
        self.assertEqual(restarted.search(first.scope, '2099', author_id='alice'), ())
        self.assertEqual([row.message_id for row in restarted.search(first.scope, '2099')], ['m2'])
