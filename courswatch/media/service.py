"""Background media enrichment with scoped persistent reuse and occurrence checks.

No raw private media is retained. Intrinsic descriptions can be reused only
inside the same scope/profile/version; author, time and source always come
from the independently authorized occurrence, never from a cached analysis.
"""
import asyncio
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import time

from courswatch.conversation.contracts import utc_iso
from courswatch.media.acquisition import AcquiredMedia, MediaUnavailable, fetch_media
from courswatch.media.decode import (DESCRIPTION_SCHEMA_VERSION, PREPROCESS_VERSION,
                                     DecodeLimits, decode_media)
from courswatch.media.normalize import media_identity

_UNSET_PROFILE = object()


def vision_available(profile, *, multiple=False):
    if profile is None:
        return False
    capabilities = profile.capabilities
    fields = ('image_input', 'structured_output', 'api_image_input', 'api_structured_output')
    if multiple:
        fields += ('multi_image',)
    return bool(capabilities.verified_real and capabilities.evidence and
                all(getattr(capabilities, field) == 'verified' for field in fields))


class MediaCache:
    """Internal cache. Callers need a live occurrence before reading or saving."""
    def __init__(self, path, *, now=time.time, source_ttl_seconds=86400, retention_days=90):
        self.path = Path(path)
        self.now = now
        self.source_ttl = source_ttl_seconds
        self.retention = retention_days * 86400
        self._next_purge = float('-inf')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(self.connect()) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS media_cache_meta(version INTEGER NOT NULL);
                INSERT INTO media_cache_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM media_cache_meta);
                CREATE TABLE IF NOT EXISTS analyses(
                    scope TEXT NOT NULL, cache_key TEXT NOT NULL, body TEXT NOT NULL,
                    created REAL NOT NULL, PRIMARY KEY(scope,cache_key));
                CREATE TABLE IF NOT EXISTS source_cache(
                    scope TEXT NOT NULL, source_id TEXT NOT NULL, profile_key TEXT NOT NULL,
                    cache_key TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(scope,source_id,profile_key));
                CREATE INDEX IF NOT EXISTS analyses_expiry ON analyses(created);
                CREATE INDEX IF NOT EXISTS source_cache_expiry ON source_cache(created);
            ''')
            version = db.execute('SELECT version FROM media_cache_meta').fetchone()[0]
            if version != 1:
                raise ValueError('Unsupported media cache schema')
            db.commit()
        self.path.chmod(0o600)
        self.purge(force=True)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        # Remove expired descriptions from freed SQLite pages as well as rows.
        db.execute('PRAGMA secure_delete=ON')
        return db

    def purge(self, *, force=False, batch_size=256):
        """Physically remove bounded batches even when no new analysis succeeds.

        Startup, reads and the service's maintenance timer share this operation.
        Expiry indexes bound selection work. A remaining backlog is eligible on
        the next pass; otherwise maintenance writes are limited to once a minute.
        """
        if not isinstance(batch_size, int) or not 1 <= batch_size <= 4096:
            raise ValueError('A bounded purge batch is required')
        now = self.now()
        if not force and now < self._next_purge:
            return {'analyses': 0, 'sources': 0}
        with closing(self.connect()) as db:
            analyses = db.execute(
                'DELETE FROM analyses WHERE rowid IN ('
                'SELECT rowid FROM analyses WHERE created<? ORDER BY created LIMIT ?)',
                (now - self.retention, batch_size)).rowcount
            sources = db.execute(
                'DELETE FROM source_cache WHERE rowid IN ('
                'SELECT rowid FROM source_cache WHERE created<? ORDER BY created LIMIT ?)',
                (now - self.source_ttl, batch_size)).rowcount
            db.commit()
        self._next_purge = now if max(analyses, sources) == batch_size else now + 60
        return {'analyses': analyses, 'sources': sources}

    def get(self, scope, cache_key):
        self.purge()
        with closing(self.connect()) as db:
            result = db.execute('SELECT body,created FROM analyses WHERE scope=? AND cache_key=?',
                                (scope.key, cache_key)).fetchone()
        if result and result[1] >= self.now() - self.retention:
            return json.loads(result[0])
        return None

    def by_source(self, scope, source_id, profile_key):
        self.purge()
        with closing(self.connect()) as db:
            row = db.execute('SELECT cache_key,created FROM source_cache WHERE scope=? AND source_id=? AND profile_key=?',
                             (scope.key, source_id, profile_key)).fetchone()
        if row and row[1] >= self.now() - self.source_ttl:
            return self.get(scope, row[0])
        return None

    def put(self, scope, source_id, profile_key, cache_key, body):
        self.purge()
        now = self.now()
        with closing(self.connect()) as db:
            db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?)',
                       (scope.key, cache_key, json.dumps(body, ensure_ascii=False), now))
            db.execute('INSERT OR REPLACE INTO source_cache VALUES(?,?,?,?,?)',
                       (scope.key, source_id, profile_key, cache_key, now))
            db.commit()

    def remember_source(self, scope, source_id, profile_key, cache_key):
        self.purge()
        with closing(self.connect()) as db:
            db.execute('INSERT OR REPLACE INTO source_cache VALUES(?,?,?,?,?)',
                       (scope.key, source_id, profile_key, cache_key, self.now()))
            db.commit()


class MediaService:
    def __init__(self, data, journal, describer=None, profile_resolver=None, *,
                 fetcher=fetch_media, decoder=decode_media, clock=time.monotonic,
                 now=time.time, limits=None, trusted_test=False, queue_size=64,
                 max_media_per_message=4, refresh_source=None, authorize=None, authorize_dispatch=None):
        self.journal, self.describer, self.profile_resolver = journal, describer, profile_resolver
        self.fetcher, self.decoder, self.clock = fetcher, decoder, clock
        self.now = now
        self.limits = limits or DecodeLimits()
        # This is exclusively an injectable local test adapter, never inferred
        # from a successful mock or enabled in normal construction.
        self.trusted_test = trusted_test
        self.refresh_source = refresh_source
        self.authorize = authorize or (lambda envelope: True)
        self.authorize_dispatch = authorize_dispatch
        self.cache = MediaCache(Path(data) / 'media-cache.db', now=now)
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.max_media = max_media_per_message
        self.background_task = None
        self.closed = False
        self.processing = asyncio.Semaphore(2)
        self.inflight = {}
        self.source_inflight = {}
        self.submitted = set()
        self.maintenance_task = None
        self._start_maintenance()

    def _start_maintenance(self):
        if self.closed or self.maintenance_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # Synchronous bot construction; start on its first async use.
        self.maintenance_task = loop.create_task(self._maintain_cache())

    async def _maintain_cache(self):
        while not self.closed:
            # Bounded local SQL, independent of provider availability/success.
            self.cache.purge()
            await asyncio.sleep(60)

    def _active(self, envelope, media=None):
        current = self.journal.active(envelope.scope, envelope.message_id)
        identity = media_identity(media) if media is not None else None
        return bool(current and not current.deleted and current.revision == envelope.revision and
                    self.authorize(current) and (identity is None or
                    any(media_identity(item) == identity for item in current.media)))

    def _profile_key(self, profile):
        value = {'profile': asdict(profile), 'preprocess': PREPROCESS_VERSION,
                 'schema': DESCRIPTION_SCHEMA_VERSION, 'test_adapter': self.trusted_test}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _source_id(media):
        # Hash only as an internal acceleration key. It cannot be looked up by
        # user/API and never returns another scope's usage or signed URL.
        return hashlib.sha256(json.dumps(media_identity(media)[-1]).encode()).hexdigest()

    def submit(self, envelope, interactive=False):
        """Schedule silent background work. Interactive callers should await enrich.

        One background consumer leaves a second processing slot for interactive
        work. The queue is bounded; saturation is persisted as deferred, never
        converted into an unsolicited public reply.
        """
        self._start_maintenance()
        if self.closed or not envelope.media or not self._active(envelope):
            return None
        identity = (envelope.scope.key, envelope.message_id, envelope.revision)
        if identity in self.submitted:
            return self.background_task
        try:
            self.queue.put_nowait(envelope)
            self.submitted.add(identity)
        except asyncio.QueueFull:
            for media in envelope.media:
                self._save(envelope, media, self._observation(envelope, media, 'deferred_budget',
                                                           reason='background_queue_full'))
            return None
        if self.background_task is None or self.background_task.done():
            self.background_task = asyncio.create_task(self._run_background())
        return self.background_task

    async def _run_background(self):
        while not self.closed:
            try:
                envelope = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await self.enrich(envelope, interactive=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                # No exception text: an URL or OCR must never reach diagnostics.
                for media in envelope.media:
                    self._save(envelope, media, self._observation(envelope, media, 'failed',
                                                               reason='background_failed'))
            finally:
                self.submitted.discard((envelope.scope.key, envelope.message_id, envelope.revision))
                self.queue.task_done()

    def _observation(self, envelope, media, status, *, reason='', technical=None,
                     profile=None, cache_hit=False, timing=None):
        result = {'status': status, 'reason': reason, 'media_id': media.media_id,
                  'message_id': envelope.message_id, 'message_revision': envelope.revision,
                  'author_id': envelope.author_id, 'sent_at': envelope.created_at,
                  'source_url': envelope.source_url, 'source_kind': media.source_kind,
                  'media_kind': media.kind, 'position': media.position,
                  'emoji_id': media.emoji_id, 'observed_name': media.filename,
                  'animated': media.animated,
                  'description': '', 'ocr': [], 'animation': '', 'limitations': [],
                  'course_fact_status': 'not_verified', 'cache_hit': cache_hit,
                  'metrics': dict(timing or {})}
        if technical:
            result.update(technical)
        # Author/time/source are always rebuilt from this occurrence. A cache
        # result cannot replace these fields with an earlier sender's values.
        result.update(media_id=media.media_id, message_id=envelope.message_id,
                      message_revision=envelope.revision, author_id=envelope.author_id,
                      sent_at=envelope.created_at, source_url=envelope.source_url,
                      source_kind=media.source_kind, media_kind=media.kind, position=media.position,
                      emoji_id=media.emoji_id, observed_name=media.filename, animated=media.animated,
                      cache_hit=cache_hit, metrics=dict(timing or {}))
        provenance = dict(result.get('provenance', {}))
        provenance.update(source_message_ref=envelope.message_id,
                          preprocess_version=PREPROCESS_VERSION,
                          description_schema_version=DESCRIPTION_SCHEMA_VERSION)
        if profile:
            provenance.update(model_profile_id=profile.profile_id, provider=profile.provider,
                              requested_model_id=profile.requested_model_id,
                              profile_config_version=profile.config_version,
                              require_zdr=profile.require_zdr)
        provenance['provider_vision_verified'] = bool(profile and vision_available(profile) and not self.trusted_test)
        provenance['test_adapter'] = self.trusted_test
        result['provenance'] = provenance
        return result

    def _save(self, envelope, media, observation):
        if not self._active(envelope, media):
            return False
        return self.journal.save_observation(envelope.scope, envelope.message_id,
                                             envelope.revision, media.media_id, observation)

    async def _call(self, function, *args, **kwargs):
        if inspect.iscoroutinefunction(function):
            return await function(*args, **kwargs)
        result = await asyncio.to_thread(function, *args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    def _existing_ready(self, envelope, media):
        reader = getattr(self.journal, 'observations', None)
        if reader is None:
            return None
        for row in reader(envelope.scope, envelope.message_id, media.media_id):
            observation = row.get('observation', row)
            if (observation.get('status') == 'ready' and
                    observation.get('message_revision') == envelope.revision and
                    (self.trusted_test or not observation.get('provenance', {}).get('test_adapter'))):
                return observation
        return None

    async def enrich(self, envelope, profile=_UNSET_PROFILE, *, refresh=False, interactive=True):
        """Return observations for active media; no participation/publication here.

        An existing observation remains a dated observation even after model
        selection changes or a source URL expires. ``refresh`` asks the current
        pipeline/profile to evaluate it again; valid intrinsic caches may still
        be reused. Additional visual detail requires a new processing version.
        Omission resolves a background profile; an explicit ``None`` preserves
        the caller's captured unavailable capability without rerouting.
        """
        self._start_maintenance()
        self.cache.purge()
        if self.closed or not self._active(envelope):
            return ()
        if profile is _UNSET_PROFILE:
            profile = self.profile_resolver() if self.profile_resolver else None
        output = []
        for index, media in enumerate(envelope.media):
            if not self._active(envelope, media):
                continue
            prior = self._existing_ready(envelope, media)
            if prior and not refresh:
                # Keep original provenance and analysis time; never relabel an
                # old observation as a fresh analysis under a new model.
                output.append({**prior, 'cache_hit': True, 'prior_observation': True,
                               'metrics': {'media_fetch_ms': 0.0, 'media_decode_ms': 0.0, 'vision_ms': 0.0}})
                continue
            if index >= self.max_media:
                observation = self._observation(envelope, media, 'deferred_budget', reason='media_batch_limit', profile=profile)
            elif media.kind not in ('image', 'animation'):
                observation = self._observation(envelope, media, 'unsupported', reason='unsupported_source_kind', profile=profile)
            elif (not profile or not self.describer or
                  (not self.trusted_test and not vision_available(profile))):
                observation = self._observation(envelope, media, 'unsupported', reason='capability_unavailable', profile=profile)
            else:
                observation = await self._enrich_one(envelope, media, profile, interactive)
            if prior and observation['status'] in ('source_unavailable', 'failed', 'unsupported', 'deferred_budget'):
                # A failed recheck doesn't erase a useful authorized memory or
                # pretend its old details have just been visually reverified.
                observation = {**prior, 'prior_observation': True, 'recheck_status': observation['status'],
                               'recheck_reason': observation['reason'],
                               'limitations': [*prior.get('limitations', []),
                                               'Observation antérieure conservée ; le média n’a pas été revérifié.']}
            if self._save(envelope, media, observation):
                output.append(observation)
        return tuple(output)

    async def _shared_result(self, jobs, key, work, is_active):
        joined = key in jobs
        while True:
            task = jobs.get(key)
            if task is None:
                task = jobs[key] = asyncio.create_task(work())
            try:
                return await asyncio.shield(task)
            except MediaUnavailable as error:
                # A shared job's owner can lose access while a separate sender
                # still has an independently authorized copy of the same media.
                # Retry only this pre-dispatch failure and at most once.
                if not joined or error.code != 'source_not_authorized' or not is_active():
                    raise
                joined = False
            finally:
                if task.done() and jobs.get(key) is task:
                    jobs.pop(key, None)

    async def _enrich_one(self, envelope, media, profile, interactive):
        profile_key = self._profile_key(profile)
        source_id = self._source_id(media)
        timing = {}
        cached = self.cache.by_source(envelope.scope, source_id, profile_key)
        if cached:
            return self._observation(envelope, media, 'ready', technical=cached, profile=profile,
                                     cache_hit=True, timing={'media_fetch_ms': 0.0,
                                                             'media_decode_ms': 0.0, 'vision_ms': 0.0})
        key = (envelope.scope.key, source_id, profile_key)
        try:
            technical, timing, cache_hit = await self._shared_result(
                self.source_inflight, key,
                lambda: self._prepare(envelope, media, profile, profile_key, source_id, interactive),
                lambda: self._active(envelope, media))
            return self._observation(envelope, media, 'ready', technical=technical, profile=profile,
                                     cache_hit=cache_hit, timing=timing)
        except MediaUnavailable as error:
            status = ('unsupported' if error.code.startswith(('unsupported', 'source_not_allowed', 'source_too_large', 'redirect', 'pixel', 'decode', 'decoded', 'invalid', 'capability'))
                      else 'source_unavailable')
            return self._observation(envelope, media, status, reason=error.code, profile=profile, timing=timing)
        except Exception as error:
            code = getattr(error, 'code', '')
            status = 'deferred_budget' if type(error).__name__ == 'BudgetExhausted' else 'failed'
            if code in ('capability_unavailable', 'vision_unavailable', 'unsupported_vision'):
                status = 'unsupported'
            return self._observation(envelope, media, status, reason=code or type(error).__name__,
                                     profile=profile, timing=timing)

    async def _prepare(self, envelope, media, profile, profile_key, source_id, interactive):
        timing = {}
        async with self.processing:
            # Recheck after queue/slot waiting. This also prevents fresh private
            # bytes being fetched after a deletion or permissions withdrawal.
            if not self._active(envelope, media):
                raise MediaUnavailable('source_not_authorized')
            started = self.clock()
            try:
                acquired = await self._call(self.fetcher, media)
            except MediaUnavailable as error:
                if error.code != 'source_expired' or self.refresh_source is None or not self._active(envelope, media):
                    raise
                refreshed = await self._call(self.refresh_source, envelope, media)
                if refreshed is None or media_identity(refreshed) != media_identity(media):
                    raise MediaUnavailable('source_unavailable')
                if not self._active(envelope, media):
                    raise MediaUnavailable('source_not_authorized')
                acquired = await self._call(self.fetcher, refreshed)
            timing['media_fetch_ms'] = round((self.clock() - started) * 1000, 2)
            raw = acquired.data if isinstance(acquired, AcquiredMedia) else acquired
            if not isinstance(raw, bytes) or len(raw) > self.limits.max_bytes:
                raise MediaUnavailable('source_too_large')
            if not self._active(envelope, media):
                raise MediaUnavailable('source_not_authorized')
            digest = hashlib.sha256(raw).hexdigest()
            cache_key = hashlib.sha256((digest + profile_key).encode()).hexdigest()
            cached = self.cache.get(envelope.scope, cache_key)
            if cached:
                self.cache.remember_source(envelope.scope, source_id, profile_key, cache_key)
                timing.update(media_decode_ms=0.0, vision_ms=0.0)
                return cached, timing, True
            job_key = (envelope.scope.key, cache_key)
            technical, analysis_timing = await self._shared_result(
                self.inflight, job_key,
                lambda: self._analyse(raw, profile, envelope, media, interactive),
                lambda: self._active(envelope, media))
            timing.update(analysis_timing)
            # A late result must not create searchable or reusable content from
            # an occurrence that was removed while the model was running.
            if self._active(envelope, media):
                self.cache.put(envelope.scope, source_id, profile_key, cache_key, technical)
            return technical, timing, False

    async def _dispatch_allowed(self, envelope, media):
        if self.closed or not self._active(envelope, media):
            return False
        if self.authorize_dispatch is not None and not await self._call(self.authorize_dispatch, envelope, media):
            return False
        return not self.closed and self._active(envelope, media)

    async def _describe(self, frames, profile, envelope, media, interactive):
        try:
            parameters = inspect.signature(self.describer).parameters
        except (TypeError, ValueError):
            parameters = {}
        kwargs = {'interactive': interactive} if 'interactive' in parameters else {}
        async_callable = (inspect.iscoroutinefunction(self.describer)
                          or inspect.iscoroutinefunction(getattr(self.describer, '__call__', None)))
        if 'before_dispatch' in parameters and not async_callable:
            loop = asyncio.get_running_loop()

            def before_dispatch():
                guard = asyncio.run_coroutine_threadsafe(self._dispatch_allowed(envelope, media), loop)
                return guard.result()

            kwargs['before_dispatch'] = before_dispatch
        elif not await self._dispatch_allowed(envelope, media):
            raise MediaUnavailable('source_not_authorized')
        try:
            return await self._call(self.describer, frames, profile, **kwargs)
        except Exception as error:
            if getattr(error, 'code', None) == 'request_invalidated':
                raise MediaUnavailable('source_not_authorized') from None
            raise

    async def _analyse(self, raw, profile, envelope, media, interactive):
        started = self.clock()
        decoded = await self._call(self.decoder, raw, limits=self.limits)
        timing = {'media_decode_ms': round((self.clock() - started) * 1000, 2)}
        if not self.trusted_test and not vision_available(profile, multiple=len(decoded.frames) > 1):
            raise MediaUnavailable('capability_unavailable')
        started = self.clock()
        result = await self._describe(decoded.frames, profile, envelope, media, interactive)
        timing['vision_ms'] = round((self.clock() - started) * 1000, 2)
        if not isinstance(result, dict):
            raise MediaUnavailable('invalid_description')
        description = result.get('visual_description', result.get('description', ''))
        ocr = result.get('visible_text', result.get('ocr', []))
        animation = result.get('animation_summary', result.get('animation', ''))
        limitations = result.get('limitations', [])
        if (not isinstance(description, str) or not isinstance(animation, str) or
                not isinstance(ocr, list) or not isinstance(limitations, list) or
                not all(isinstance(value, str) for value in limitations)):
            raise MediaUnavailable('invalid_description')
        if (len(description) > 12000 or len(animation) > 6000 or len(ocr) > 100 or
                len(limitations) > 30 or any(len(value) > 2000 for value in limitations) or
                not (description.strip() or ocr or animation.strip())):
            raise MediaUnavailable('invalid_description')
        normalized_ocr = []
        for row in ocr[:100]:
            if (not isinstance(row, dict) or not isinstance(row.get('text'), str) or len(row.get('text', '')) > 4000 or
                    not isinstance(row.get('at_ms'), int) or row['at_ms'] not in decoded.sampled_at_ms or
                    row.get('quality') not in ('readable', 'uncertain', 'partial', 'unreadable')):
                raise MediaUnavailable('invalid_description')
            normalized_ocr.append({'text': row['text'][:4000], 'at_ms': row['at_ms'], 'quality': row['quality']})
        provenance = result.get('provenance') or {}
        if not isinstance(provenance, dict):
            raise MediaUnavailable('invalid_description')
        # Only known provider metadata survives; a model cannot invent source
        # URLs, author IDs, dates or policies through the observation result.
        provenance = {key: provenance[key] for key in ('resolved_model_revision', 'resolved_model_id')
                      if key in provenance and (provenance[key] is None or isinstance(provenance[key], str))}
        provenance.setdefault('resolved_model_revision', None)
        technical = {'description': description[:12000], 'ocr': normalized_ocr,
                     'animation': animation[:6000], 'sha256': decoded.sha256,
                     'actual_format': decoded.actual_format, 'sampled_at_ms': list(decoded.sampled_at_ms),
                     'frame_count': decoded.frame_count, 'duration_ms': decoded.duration_ms,
                     'limitations': list(dict.fromkeys([*decoded.limitations, *limitations]))[:30],
                     'analyzed_at': utc_iso(datetime.fromtimestamp(self.now(), timezone.utc)), 'provenance': provenance,
                     'course_fact_status': 'not_verified'}
        return technical, timing

    async def close(self):
        self.closed = True
        if self.maintenance_task is not None:
            self.maintenance_task.cancel()
            await asyncio.gather(self.maintenance_task, return_exceptions=True)
        # Let in-flight HTTP work reach a terminal state so budget accounting is
        # not mistaken for cancellation of a remote request. The caller owns
        # application shutdown deadlines; no retry is started here.
        tasks = {task for task in [self.background_task, *self.source_inflight.values(), *self.inflight.values()]
                 if task is not None and not task.done()}
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.submitted.clear()
