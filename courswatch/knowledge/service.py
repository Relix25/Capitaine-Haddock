"""Scoped retrieval with source validation, complete evidence units and citations.

No provider call and no source-site access occurs here. Course snapshots are
read-only. Revisions first seen by this service are recorded in knowledge.db;
older, never observed source text is explicitly unknown.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from courswatch.conversation.contracts import Evidence, EvidenceBundle, RetrievalRequest, Scope, utc_iso
from courswatch.media import observation_digest, observation_limitations
from courswatch.media.normalize import CUSTOM_EMOJI
from courswatch.reminders import reminder_config_path, resolve_task

_STOP = frozenset('le la les de des du un une au aux et est ce c ça c’est cest quand pour '
                  'projet projets cours tu te notre nous vous on en a qu quelle quelles '
                  'quel quels discussion discussions semaine dernière derniere souviens '
                  'rappelles rappelle souvenir il y avait avais dit envoyé envoye image gif qu que qui par sur haddock capitaine'.split())
_HISTORY = re.compile(r'\b(souviens|souvenir|rappelles|discussion|avions|avait|semaine derni[eè]re|il y a une semaine)\b', re.I)
_DATE = re.compile(r'\b(quand|dates?|[eé]ch[eé]ances?|remises?|rendus?|d[eé]p[oô]ts?|oral|oraux|pr[eé]sentations?|deadlines?)\b', re.I)
_UPCOMING = re.compile(r'\b(prochain(?:e?s?)|[aà] venir)\b', re.I)
_DEADLINE = re.compile(r'\b([eé]ch[eé]ances?|deadlines?)\b', re.I)
_COURSE_FOLLOWUP = re.compile(r'^(?:\[mention\]\W*|(?:capitaine\s+)?haddock\W+)?et\s+(?:pour\s+)?([\w -]+?)\s*\?*$', re.I)
_MEDIA = re.compile(r'\b(images?|photos?|gifs?|emojis?|m[ée]dias?|animations?|anim[eé]s?)\b', re.I)
_COURSE_INTENT = re.compile(r'\b(consignes?|annonces?|pdf|projets?|documents?|rendre|calendrier)\b', re.I)
_VISUAL_CONTEXT = re.compile(
    r'\b(?:dans|sur)\s+(?:(?:le|la|mon|ma|ce|cet|cette|cette dernière)\s+|l[’\x27])'
    r'(?:image|photo|gif|emoji|m[ée]dia|animation)\b', re.I)
_REQUEST_BOUNDARY = re.compile(
    r'[?!;\n]+|\bet\s+(?=(?:quel(?:le)?s?|quand|que\s+(?:montre|voit)|'
    r'd[ée]cri[st]?|d[ée]crire|lis|peux-tu|pourrais-tu)\b)', re.I)
_OWN_MEDIA = re.compile(
    r'\b(?:mon|ma|mes)\s+(?:(?:dernier|dernière)s?\s+)?'
    r'(?:images?|photos?|gifs?|emojis?|m[ée]dias?|animations?)\b'
    r'|\bj[’\x27]ai\s+(?:envoy[ée]|post[ée]|partag[ée])\b', re.I)
_MEDIA_REFERENCE_WORDS = frozenset(
    'avec mon ma mes dernier dernière derniers dernières envoyé envoyée posté postée partagé partagée'.split())


def _words(text):
    text = re.sub(r'([^\W\d_])((?:19|20)\d{2})(?!\d)', r'\1 \2', str(text))
    return re.findall(r'[^\W_]+', text.casefold())


def _tokens(text):
    return {token for token in _words(text) if token not in _STOP and len(token) > 1}


def is_own_media_request(question):
    """Only an explicit first-person reference binds author identity to the turn."""
    return bool(_MEDIA.search(question) and _OWN_MEDIA.search(question))


def _course_source_id(source):
    namespace, _, identifier = source.partition(':')
    return identifier.casefold() if namespace in ('moodle', 'teams') else ''


def _course_ids(question, settings, sources):
    aliases = {}
    for source in sources:
        identifier = _course_source_id(source)
        if identifier:
            aliases.setdefault(' '.join(_words(identifier)), set()).add(identifier)
    for course in settings.get('courses', ()):
        identifier = course['id'].casefold()
        for alias in (identifier, course.get('label') or identifier):
            aliases.setdefault(' '.join(_words(alias)), set()).add(identifier)
    if not aliases:
        return frozenset()
    # At a given position consume a full course name or compound identifier
    # before a shorter alias. Never match a substring of a word.
    alternatives = '|'.join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    names = re.findall(r'\b(?:' + alternatives + r')\b', ' '.join(_words(question)))
    return frozenset(identifier for name in names for identifier in aliases[name])


def _requested_course_ids(question, settings, allowed):
    # A visual clause must not move an independently requested deadline to
    # memory search. A date visible inside an image is still a visual request.
    clauses = _REQUEST_BOUNDARY.split(CUSTOM_EMOJI.sub('', question))
    return frozenset(
        identifier for clause in clauses
        if (_DATE.search(clause) or _COURSE_INTENT.search(clause))
        and not (_VISUAL_CONTEXT.search(clause) or is_own_media_request(clause))
        for identifier in _course_ids(clause, settings, allowed)
    )


def _requested_years(text):
    return set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', str(text)))


def _upcoming_deadlines(text):
    return bool(_UPCOMING.search(text) and (_DATE.search(text) or re.search(r'\brendre\b', text, re.I)))


def _year_matches(question, values, explicit=None):
    requested = _requested_years(question)
    if not requested:
        return True
    known = _requested_years(explicit) if explicit else _requested_years(' '.join(str(value or '') for value in values))
    return bool(requested & known)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _safe(text):
    return str(text).replace('@', '@\u200b').replace('<@', '<@\u200b')


def _url(value):
    if not isinstance(value, str) or len(value) > 600 or any(c in value for c in '\n\r<>'):
        return ''
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else ''
    except ValueError:
        return ''


def _citation(evidence, label='Source'):
    url = _url(evidence.source_url)
    return f' [{label}](<{url}>)' if url else ''


def _date(value, precision):
    if precision == 'date_only':
        return datetime.fromisoformat(value).strftime('%d/%m/%Y') + ' — heure non publiée'
    moment = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Bucharest'))
    return moment.strftime('%d/%m/%Y à %H:%M') + ' (heure de Bucarest)'


def _relative_date(body, published_at, source_timezone='Europe/Bucharest'):
    """Resolve only a narrow, explicit expression anchored to source publication."""
    if not published_at or not re.search(r'\bdemain\b', body, re.I):
        return None, None
    try:
        publication = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
        if publication.tzinfo is None:
            return None, None
        local = publication.astimezone(ZoneInfo(source_timezone)) + timedelta(days=1)
        hour = re.search(r'\bdemain\s+(?:[aà]\s+)?(\d{1,2})(?:\s*h\s*(\d{2})?|:(\d{2}))', body, re.I)
        if hour:
            local = local.replace(hour=int(hour[1]), minute=int(hour[2] or hour[3] or 0), second=0, microsecond=0)
            return local.isoformat(), 'datetime'
        return local.date().isoformat(), 'date_only'
    except (ValueError, TypeError, KeyError):
        return None, None


def _bounded(evidence, budget):
    chosen, used = [], 0
    for item in evidence:
        # A proof is an indivisible unit. Do not slice source text or JSON.
        cost = len(json.dumps(asdict(item), ensure_ascii=False))
        if used + cost <= budget:
            chosen.append(item); used += cost
    return tuple(chosen)


class KnowledgeService:
    def __init__(self, data_dir, config, allowed_sources=None, journal=None, clock=None):
        self.data = Path(data_dir)
        self.config = config
        self.allowed_sources = allowed_sources or {}
        self.journal = journal
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.path = self.data / 'knowledge.db'

    def _settings(self):
        result = self.config() if callable(self.config) else self.config
        return dict(result or {})

    def _sources(self, scope):
        # No wildcard, global source list, role/user based expansion or defaults.
        mapping = self.allowed_sources() if callable(self.allowed_sources) else self.allowed_sources
        value = mapping.get(scope, mapping.get(scope.key, ()))
        if isinstance(value, str):
            raise ValueError('Sources must be an explicit sequence per scope')
        return frozenset(str(source) for source in value)

    def is_course_question(self, question, scope=None):
        settings = self._settings()
        allowed = self._sources(scope) if scope is not None else ()
        return bool(_requested_course_ids(question, settings, allowed) or (
            _DEADLINE.search(question) and not (_MEDIA.search(question) or _HISTORY.search(question))))

    def followup_question(self, snapshot):
        """Carry a verified participant's calendar request over to the new course."""
        match = _COURSE_FOLLOWUP.fullmatch(snapshot.goal.strip())
        if not match or len(snapshot.requester_ids) != 1:
            return snapshot.goal
        course = match.group(1).strip()
        aliases = {str(value).casefold() for item in self._settings().get('courses', ())
                   for value in (item['id'], item.get('label', ''))}
        if course.casefold() not in aliases:
            return snapshot.goal
        previous = next((message for message in reversed(snapshot.messages)
                         if message.message_id not in snapshot.trigger_message_ids and not message.deleted
                         and not message.is_bot and message.author_id == snapshot.requester_ids[0]), None)
        if previous and _upcoming_deadlines(previous.text):
            return 'Quelles sont les prochaines échéances pour ' + course + ' ?'
        return snapshot.goal

    def _now(self, value=None):
        moment = value or self.clock()
        if isinstance(moment, str):
            moment = datetime.fromisoformat(moment.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError('An aware timestamp is required')
        return moment.astimezone(timezone.utc)

    def _history_db(self):
        self.data.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(component TEXT,version INTEGER,'
                           'applied_utc TEXT,PRIMARY KEY(component,version))')
                versions = {row[0] for row in db.execute(
                    "SELECT version FROM schema_migrations WHERE component='knowledge'")}
                if versions - {1}:
                    raise RuntimeError('Knowledge schema is newer than this application')
                db.execute('''CREATE TABLE IF NOT EXISTS source_versions(
                    scope TEXT,source TEXT,item_key TEXT,version TEXT,payload TEXT,
                    observed_at TEXT,published_at TEXT,status TEXT,
                    PRIMARY KEY(scope,source,item_key,version))''')
                db.execute('''CREATE TABLE IF NOT EXISTS pdf_text(
                    scope TEXT,source TEXT,item_key TEXT,version TEXT,pages TEXT,
                    PRIMARY KEY(scope,source,item_key,version))''')
                db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)',
                           ('knowledge', 1, utc_iso(self._now())))
            self.path.chmod(0o600)
            return db
        except BaseException:
            db.close()
            raise

    def _remember(self, scope, rows):
        observed = utc_iso(self._now())
        with closing(self._history_db()) as history:
            with history:
                for source, key, fingerprint, payload in rows:
                    item = json.loads(payload)
                    history.execute("UPDATE source_versions SET status='replaced' WHERE scope=? AND source=? "
                                    "AND item_key=? AND version<>? AND status='current'",
                                    (scope.key, source, key, fingerprint))
                    history.execute("UPDATE source_versions SET status='current' WHERE scope=? AND source=? AND item_key=? AND version=?",
                                    (scope.key, source, key, fingerprint))
                    history.execute('INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?,?,?)',
                                    (scope.key, source, key, fingerprint, payload, observed,
                                     item.get('published_at'), 'current'))

    def _tasks(self, scope, question, settings, now, allowed, task_ids=None, named_courses=()):
        try:
            reminders = json.loads(reminder_config_path(settings).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return (), '', ('Configuration des tâches indisponible.',) if settings.get('reminders_file') else ()
        except (OSError, ValueError):
            return (), '', ('Configuration des tâches indisponible.',)
        terms = _tokens(question)
        upcoming = task_ids is None and _upcoming_deadlines(question)
        tasks = [task for task in reminders.get('tasks', [])
                 if task.get('reference', {}).get('source') in allowed
                 and not (upcoming and task.get('completed'))]
        if task_ids is not None:
            tasks = [task for task in tasks if task.get('id') in task_ids]
        elif not named_courses and not upcoming:
            tasks = [task for task in tasks if terms & _tokens(task.get('title', '') + ' ' + task['course'])]
        course_terms = set().union(*(_tokens(course) for course in named_courses))
        for course in settings.get('courses', ()):
            if course['id'].casefold() in named_courses:
                course_terms.update(_tokens(course.get('label', '')))
        topic_terms = terms - course_terms
        if upcoming:
            topic_terms -= _tokens('donne moi montre liste toutes prochaines prochains échéances dates deadlines venir')
        if topic_terms and task_ids is None:
            scores = {task['id']: len(topic_terms & _tokens(task.get('title', '') + ' ' + task.get('action', '')))
                      for task in tasks}
            best = max(scores.values(), default=0)
            if best:
                tasks = [task for task in tasks if scores[task['id']] == best]
        # Generic date requests must not arbitrarily pick the nearest task.
        if not tasks:
            return (), '', ()
        try:
            state = sqlite3.connect((self.data / 'state.db').resolve().as_uri() + '?mode=ro', uri=True)
        except sqlite3.Error:
            return (), '', ('Cache métier indisponible.',)
        evidence = []
        with closing(state):
            # Read fingerprint, body and source status from the same SQLite snapshot.
            state.execute('BEGIN')
            for task in tasks:
                reference = task['reference']
                source, key = reference['source'], reference['key']
                try:
                    row = state.execute('SELECT fingerprint,payload FROM items WHERE source=? AND key=?',
                                        (source, key)).fetchone()
                    source_state = state.execute('SELECT status,checked FROM sources WHERE source=?', (source,)).fetchone()
                    candidate = {**task, 'completed': False} if task.get('completed') else task
                    if reference.get('capture_body_hash') and not reference.get('body_hash'):
                        resolved = {'unconfirmed': True, 'reason': 'empreinte de l’annonce non liée'}
                    else:
                        resolved = resolve_task(SimpleNamespace(db=state), candidate,
                                                now.astimezone(ZoneInfo(reminders.get('timezone', 'Europe/Bucharest'))),
                                                reminders.get('policy', {}), include_past=not upcoming)
                except (sqlite3.Error, KeyError, TypeError, ValueError):
                    resolved, row, source_state = {'unconfirmed': True, 'reason': 'source à revalider'}, None, None
                if resolved is None:
                    continue
                if row and task_ids is None:
                    self._remember(scope, [(source, key, *row)])
                payload = json.loads(row[1]) if row else {}
                valid = resolved and not resolved.get('unconfirmed')
                status = ('completed' if task.get('completed') else
                          'stale' if valid and resolved.get('stale') else 'current') if valid else 'unconfirmed'
                due = resolved.get('due') if valid else None
                date_value = (due.isoformat() if resolved['has_time'] else due.date().isoformat()) if due else None
                precision = ('datetime' if resolved['has_time'] else 'date_only') if due else None
                source_url = _url(payload.get('url')) or _url(task.get('url'))
                version = _hash([task, row[0] if row else None, source_url, date_value, precision, status,
                                 resolved.get('reason') if resolved else None])
                evidence.append(Evidence('task:' + task['id'], 'task', scope,
                    json.dumps([source, key]), source_url, task['title'],
                    payload.get('body', '') if valid else resolved.get('reason', 'date à confirmer'),
                    version, source_state[1] if source_state and source_state[1] else utc_iso(self._now()),
                    payload.get('published_at'), payload.get('author_id') or payload.get('author'),
                    date_value, precision, status))
        requested_years = _requested_years(question) if task_ids is None else set()
        if requested_years:
            by_id = {'task:' + task['id']: task for task in tasks}
            evidence = [item for item in evidence if item.date_value and
                        _year_matches(question, [item.date_value],
                                      by_id[item.id].get('academic_year') or by_id[item.id].get('year'))]
            if not evidence:
                return (), 'Je n’ai pas de date confirmée pour ' + ', '.join(sorted(requested_years)) + '. Tu parles de l’année de l’échéance ou de l’année universitaire ?', ()
        if upcoming:
            evidence.sort(key=lambda item: (item.date_value is None, item.date_value or '', item.id))
            return tuple(evidence), '', ()
        if task_ids is None and len(evidence) > 1 and not re.search(r'\b(toutes?|dates|[eé]ch[eé]ances|r[eé]capitul)\b', question, re.I):
            options = evidence[:4]
            return tuple(options), 'Tu parles de quelle échéance : ' + ' ; '.join(item.title for item in options) + ' ?', ()
        return tuple(evidence), '', ()

    def _source_evidence(self, scope, question, allowed):
        if not allowed:
            return (), ()
        try:
            with closing(sqlite3.connect((self.data / 'state.db').resolve().as_uri() + '?mode=ro', uri=True)) as state:
                rows = state.execute('SELECT source,key,fingerprint,payload FROM items WHERE source IN (' +
                                     ','.join('?' for _ in allowed) + ')', tuple(sorted(allowed))).fetchall()
                statuses = {row[0]: (row[1], row[2]) for row in state.execute(
                    'SELECT source,status,checked FROM sources WHERE source IN (' +
                    ','.join('?' for _ in allowed) + ')', tuple(sorted(allowed)))}
        except sqlite3.Error:
            return (), ('Cache des sources indisponible.',)
        self._remember(scope, rows)
        terms, candidates = _tokens(question), []
        for source, key, fingerprint, payload in rows:
            item = json.loads(payload)
            searchable = item.get('title', '') + ' ' + item.get('body', '') + ' ' + source
            score = len(terms & _tokens(searchable))
            if not score or key.endswith((':document', ':document-status', ':metadata')):
                continue
            if not _year_matches(question, [item.get('body'), item.get('title'), item.get('url')],
                                 item.get('academic_year') or item.get('year')):
                continue
            source_state = statuses.get(source, (None, None))
            date_value, date_precision = _relative_date(item.get('body', ''), item.get('published_at'),
                                                       item.get('source_timezone') or 'Europe/Bucharest')
            candidates.append((score, Evidence('source:' + _hash([source, key])[:20], 'source', scope,
                json.dumps([source, key]), item.get('url', ''), item.get('title', source), item.get('body', ''),
                fingerprint, source_state[1] or utc_iso(self._now()), item.get('published_at'),
                item.get('author_id') or item.get('author'), date_value, date_precision,
                status='current' if source_state[0] == 'ok' else 'stale')))
        candidates.sort(key=lambda row: (-row[0], row[1].source_id))
        return tuple(item for _, item in candidates[:8]), ()

    def _pdf_evidence(self, scope, question, allowed, settings):
        root_value = settings.get('knowledge_documents_dir') or settings.get('obsidian_export', {}).get('documents_dir')
        if not root_value:
            return (), ()
        from courswatch.documents.publish import (_existing_root, _load_catalog, _extract_pages,
                                                   _assert_no_links, PublishError)
        try:
            root = _existing_root(root_value, 'documents')
        except (OSError, PublishError):
            return (), ('Archives PDF indisponibles.',)
        terms, evidence, gaps = _tokens(question), [], []
        for source in sorted(allowed):
            if not source.startswith('moodle:'):
                continue
            course = source.split(':', 1)[1]
            if not re.fullmatch(r'[A-Z0-9_-]{2,16}', course):
                continue
            catalog_dir = root / course / 'catalog'
            try:
                _assert_no_links(catalog_dir)
                entries = sorted(catalog_dir.glob('*.json'))[:100]
                for catalog_path in entries:
                    versions = _load_catalog(catalog_path, root, course)
                    current = versions[-1]
                    if current.byte_count > 25 * 1024 * 1024 or current.pages > 500:
                        gaps.append('PDF trop volumineux pour la recherche locale bornée.'); continue
                    with closing(self._history_db()) as cache:
                        row = cache.execute('SELECT pages FROM pdf_text WHERE scope=? AND source=? AND item_key=? AND version=?',
                                            (scope.key, source, current.key, current.sha256)).fetchone()
                        pages = json.loads(row[0]) if row else _extract_pages(current.source_path, current.pages)
                        if not row:
                            cache.execute('INSERT OR REPLACE INTO pdf_text VALUES(?,?,?,?,?)',
                                          (scope.key, source, current.key, current.sha256, json.dumps(pages, ensure_ascii=False)))
                            cache.commit()
                    for number, text in enumerate(pages, 1):
                        if not terms & _tokens(text + ' ' + current.title + ' ' + course):
                            continue
                        if not _year_matches(question, [text, current.title, current.source_url]):
                            continue
                        evidence.append(Evidence('pdf:' + _hash([source, current.key, number])[:20], 'pdf', scope,
                            json.dumps([source, current.key, number]), current.source_url,
                            current.title + ' — page ' + str(number), text, current.sha256,
                            current.downloaded_utc, status='current'))
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                gaps.append('Une archive PDF n’a pas pu être validée.')
        return tuple(evidence[:12]), tuple(gaps)

    def _conversation(self, request, now):
        if self.journal is None:
            return EvidenceBundle(coverage='Historique conversationnel indisponible.')
        after, before = request.after, request.before
        tz = ZoneInfo('Europe/Bucharest')
        local = now.astimezone(tz)
        if not after and re.search(r'semaine derni[eè]re', request.query, re.I):
            week = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            after, before = utc_iso(week - timedelta(days=7)), utc_iso(week)
        elif not after and re.search(r'il y a une semaine', request.query, re.I):
            after, before = utc_iso(now - timedelta(days=9)), utc_iso(now - timedelta(days=5))
        media_request = bool(_MEDIA.search(request.query))
        terms = _tokens(request.query)
        if media_request:
            terms = {term for term in terms - _MEDIA_REFERENCE_WORDS if not _MEDIA.fullmatch(term)}
        author_id = request.author_id
        if author_id is None:
            query_names = _tokens(request.query)
            matched = [(identifier, name) for identifier, name in self.journal.authors(request.scope)
                       if _tokens(name) and _tokens(name) <= query_names]
            if len({identifier for identifier, _ in matched}) > 1:
                return EvidenceBundle(clarification='Plusieurs personnes portent ce nom. De quelle personne parles-tu ?')
            if matched:
                author_id, name = matched[0]
                terms -= _tokens(name)
        matches = self.journal.search(request.scope, ' '.join(sorted(terms)),
                                      after=after, before=before, author_id=author_id, limit=8,
                                      media_only=media_request)
        evidence = []
        for message in matches:
            if message.is_bot:
                continue
            if message.text:
                evidence.append(Evidence('message:' + message.message_id, 'conversation', request.scope,
                    message.message_id, message.source_url, message.display_name, message.text, str(message.revision),
                    message.received_at or message.created_at, message.created_at, message.author_id))
            if media_request:
                observations = self.journal.observations(request.scope, message.message_id)
                for row in observations:
                    observation = row['observation']
                    if observation.get('status') != 'ready':
                        continue
                    pieces = []
                    for key, label in (('description', 'Description'), ('ocr', 'Texte visible'),
                                       ('animation', 'Animation'), ('limitations', 'Limites')):
                        value = observation_limitations(observation) if key == 'limitations' else observation.get(key)
                        if value:
                            pieces.append(label + ' : ' + (value if isinstance(value, str) else
                                          json.dumps(value, ensure_ascii=False)))
                    evidence.append(Evidence('media:' + message.message_id + ':' + row['media_id'],
                        'media_observation', request.scope, json.dumps([message.message_id, row['media_id']]),
                        message.source_url, message.display_name, '\n'.join(pieces),
                        str(message.revision) + ':' + observation_digest(observation),
                        observation.get('analyzed_at') or observation.get('analysed_at') or '',
                        message.created_at, message.author_id, status='observation'))
        coverage = self.journal.coverage(request.scope)
        status = ''
        if not evidence:
            status = 'Je n’ai pas retrouvé cet échange dans l’historique disponible.'
        if after and not self.journal.is_covered(request.scope, after, before or utc_iso(now)):
            status += ' Mon historique disponible ne couvre pas toute cette période.'
        gaps = tuple(('Historique incomplet du ' + str(gap.get('start', ''))[:10] + ' au ' +
                      str(gap.get('end', ''))[:10] + '.') if isinstance(gap, dict) else str(gap)
                     for gap in coverage['gaps'])
        if not coverage['complete']:
            gaps += ('Couverture historique partielle ; modifications hors ligne non garanties.',)
        return EvidenceBundle(_bounded(evidence, request.budget_chars), coverage=status.strip(), gaps=gaps)

    def query(self, scope, question=None, *, now=None, budget_chars=7000):
        request = scope if isinstance(scope, RetrievalRequest) else RetrievalRequest(scope, question or '', budget_chars=budget_chars)
        moment = self._now(now)
        if request.intent == 'conversation':
            return self._conversation(request, moment)
        allowed = self._sources(request.scope)
        settings = self._settings()
        requested_courses = _requested_course_ids(request.query, settings, allowed)
        if (request.intent != 'course' and not requested_courses
                and (_HISTORY.search(request.query) or _MEDIA.search(request.query))):
            return self._conversation(request, moment)
        named_courses = requested_courses or _course_ids(CUSTOM_EMOJI.sub('', request.query), settings, allowed)
        if request.intent == 'auto' and not (named_courses or _DEADLINE.search(request.query) or _COURSE_INTENT.search(request.query)):
            return EvidenceBundle()
        private_help = 'Utilise /a-faire pour consulter tes échéances en privé.'
        if not allowed:
            return EvidenceBundle(coverage='La recherche des cours n’est pas configurée pour ce salon. ' + private_help)
        if named_courses:
            allowed = frozenset(source for source in allowed if _course_source_id(source) in named_courses)
            if not allowed:
                return EvidenceBundle(coverage='Ce cours n’est pas partagé avec ce salon. ' + private_help)
            if _COURSE_FOLLOWUP.fullmatch(request.query.strip()):
                return EvidenceBundle(clarification='Tu cherches les prochaines échéances de ce cours, ses consignes ou autre chose ?')
        tasks, clarification, gaps = self._tasks(
            request.scope, request.query, settings, moment, allowed, named_courses=named_courses)
        if clarification:
            return EvidenceBundle(clarification=clarification, gaps=gaps, dependencies=tasks)
        if tasks:
            selected = _bounded(tasks, request.budget_chars)
            return EvidenceBundle(selected, coverage='' if selected else 'Budget insuffisant pour une preuve complète.', gaps=gaps)
        if _upcoming_deadlines(request.query):
            courses = ' pour ' + ', '.join(sorted(name.upper() for name in named_courses)) if named_courses else ''
            return EvidenceBundle(coverage='Je n’ai trouvé aucune échéance à venir confirmée' + courses
                                  + ' dans la liste de tâches disponible.', gaps=gaps)
        sources, source_gaps = self._source_evidence(request.scope, request.query, allowed)
        pdfs, pdf_gaps = self._pdf_evidence(request.scope, request.query, allowed, settings)
        selected = _bounded([*sources, *pdfs], request.budget_chars)
        return EvidenceBundle(selected, coverage='' if selected else 'Je n’ai pas retrouvé de source probante dans le cache disponible.',
                              gaps=tuple([*gaps, *source_gaps, *pdf_gaps]))

    def revalidate(self, scope, bundle):
        allowed = self._sources(scope)
        items = (*bundle.evidence, *bundle.dependencies)
        task_versions = None
        for item in items:
            if item.scope != scope:
                return False
            if item.kind in {'conversation', 'media_observation'}:
                identifier = json.loads(item.source_id)[0] if item.kind == 'media_observation' else item.source_id
                current = self.journal.active(scope, identifier) if self.journal else None
                if current is None:
                    return False
                if item.kind == 'media_observation':
                    media_id = json.loads(item.source_id)[1]
                    observations = self.journal.observations(scope, identifier, media_id)
                    if not any(row['observation'].get('status') == 'ready' and
                               str(current.revision) + ':' + observation_digest(row['observation']) == item.version
                               for row in observations):
                        return False
                elif str(current.revision) != item.version:
                    return False
                continue
            try:
                parts = json.loads(item.source_id)
                source, key = parts[:2]
            except (ValueError, TypeError):
                return False
            if source not in allowed:
                return False
            if item.kind == 'task':
                if task_versions is None:
                    ids = {row.id.removeprefix('task:') for row in items if row.kind == 'task'}
                    tasks, _, _ = self._tasks(scope, '', self._settings(), self._now(), allowed, task_ids=ids)
                    task_versions = {row.id: row.version for row in tasks}
                if task_versions.get(item.id) != item.version:
                    return False
            elif item.kind == 'source':
                try:
                    with closing(sqlite3.connect((self.data / 'state.db').resolve().as_uri() + '?mode=ro', uri=True)) as state:
                        current = state.execute('SELECT fingerprint FROM items WHERE source=? AND key=?', (source, key)).fetchone()
                        state_status = state.execute('SELECT status FROM sources WHERE source=?', (source,)).fetchone()
                    expected_status = 'current' if state_status and state_status[0] == 'ok' else 'stale'
                    if not current or current[0] != item.version or item.status != expected_status:
                        return False
                except sqlite3.Error:
                    return False
            elif item.kind == 'pdf':
                settings = self._settings()
                root = settings.get('knowledge_documents_dir') or settings.get('obsidian_export', {}).get('documents_dir')
                if not root:
                    return False
                from courswatch.documents.publish import _existing_root, _load_catalog, _safe_catalog_filename
                try:
                    store = _existing_root(root, 'documents')
                    current = _load_catalog(store / source.split(':', 1)[1] / 'catalog' / _safe_catalog_filename(key),
                                            store, source.split(':', 1)[1])[-1]
                    if current.sha256 != item.version:
                        return False
                except (OSError, ValueError, RuntimeError):
                    return False
            else:
                return False
        return True

    render_answer = staticmethod(lambda bundle: render_answer(bundle))


def render_answer(bundle, max_chars=1700):
    """Build citations from authorized evidence, never model-created links/dates."""
    if bundle.clarification:
        text = _safe(bundle.clarification)
        return text if len(text) <= max_chars else 'Tu parles de la remise, de la proposition ou de la présentation ?'
    notices = []
    for value in (bundle.coverage, *bundle.gaps):
        text = _safe(value)
        if text and text not in notices and len('\n'.join([*notices, text])) <= min(500, max_chars):
            notices.append(text)
    if not bundle.evidence:
        return '\n'.join(notices) or 'Je n’ai pas retrouvé de source probante.'
    lines, omitted = [], 0
    notice_size = len('\n'.join(notices)) + (1 if notices else 0)
    for item in bundle.evidence:
        if item.kind == 'task':
            if item.status == 'unconfirmed' or not item.date_value:
                text = item.title + ' : date à confirmer — ' + item.excerpt
            else:
                text = item.title + ' : ' + _date(item.date_value, item.date_precision)
                if item.status == 'stale':
                    text += '. Dernière date connue ; source à revérifier'
                if item.status == 'completed':
                    text += '. Tâche marquée terminée dans la configuration'
            line = _safe(text) + '.' + _citation(item)
        elif item.kind == 'conversation':
            date = datetime.fromisoformat(item.published_at.replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Bucharest'))
            line = _safe(f'Le {date:%d/%m/%Y}, {item.title} : « {item.excerpt} »') + _citation(item, 'Échange')
        elif item.kind == 'media_observation':
            date = datetime.fromisoformat(item.published_at.replace('Z', '+00:00')).astimezone(ZoneInfo('Europe/Bucharest'))
            analysis = ''
            if item.observed_at:
                try:
                    analyzed = datetime.fromisoformat(item.observed_at.replace('Z', '+00:00')).astimezone(date.tzinfo)
                    analysis = f' (analyse du {analyzed:%d/%m/%Y})'
                except (TypeError, ValueError):
                    pass
            line = _safe(f'Observation mémorisée du média envoyé par {item.title} le {date:%d/%m/%Y}{analysis} : {item.excerpt}') + _citation(item, 'Média')
        else:
            # Retrieved source text is quoted as data, not promoted to a parsed date.
            suffix = ' (source à revérifier)' if item.status == 'stale' else ''
            date_text = ' : ' + _date(item.date_value, item.date_precision) if item.date_value else ''
            line = _safe(item.title + suffix + date_text + ' : « ' + item.excerpt + ' »') + _citation(item)
        if len('\n'.join([*lines, line])) <= max_chars - notice_size - 80:
            lines.append(line)
        else:
            omitted += 1
    if omitted:
        note = 'D’autres éléments sont disponibles dans les sources.'
        if not lines:
            first = bundle.evidence[0]
            note = 'Source retrouvée ; extrait trop long pour cette réponse.' + _citation(first)
        if len('\n'.join([*lines, note])) <= max_chars - notice_size:
            lines.append(note)
    return '\n'.join([*lines, *notices])
