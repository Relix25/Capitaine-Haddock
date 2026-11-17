"""Scoped Luna conversation decisions, short RAM context and a durable daily token ceiling."""
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo
from courswatch.conversation.persona import PERSONA_VERSION, PROMPT
from courswatch.conversation.metrics import MeasuredResult, local_metrics, read_model_response, response_metrics

TZ = ZoneInfo('Europe/Bucharest')
BUDGET_REPLY = 'Mille sabords ! J’ai englouti ma ration de mots pour aujourd’hui. Le capitaine est en cale sèche jusqu’à minuit, heure de Bucarest !'
OFFLINE_REPLY = 'Tonnerre de Brest ! La liaison avec ma vigie fait des siennes. Le capitaine revient dès que la radio remarche !'
REPHRASE_REPLY = 'Tonnerre ! J’ai perdu le fil. Redis-moi ça.'
REFUSAL_REPLY = 'Celle-là, je la passe. Trouve mieux, ectoplasme.'
RESPONSE_ERRORS = frozenset(('model_refusal','output_limit','incomplete_response','no_output',
                             'invalid_json','invalid_schema','empty_reply','invalid_response'))
PROVIDERS = {
    'openai': {'endpoint':'https://api.openai.com/v1/responses',
               'key_name':'OPENAI_API_KEY','model':'gpt-6-luna'},
    'commandcode': {'endpoint':'https://api.commandcode.ai/provider/v1/responses',
                    'key_name':'CMD_API_KEY','model':'deepseek/deepseek-v4.1-flash'},
}
SAMPLE_HINTS = {
    'abordage':'Enthousiasme pour démarrer une action, cri à l’abordage.',
    'parachute':'Un plan risqué ou absurde, sauter sans parachute.',
    'bachi_bouzouk':'Défi ou colère théâtrale et bon enfant.',
    'cafe':'Café récalcitrant, maladresse ou petite mésaventure.',
    'scolopendres':'Demande comique de silence ; ne pas interrompre gratuitement les gens.',
}



class BudgetExhausted(RuntimeError):
    pass


class LunaError(RuntimeError):
    def __init__(self,code,metrics=None):
        super().__init__(code)
        self.code = code
        self.metrics = dict(metrics or {})


def parse_decision(response,identifiers):
    """Handle documented non-JSON outcomes before validating the decision schema."""
    if response.get('status') == 'incomplete':
        reason = (response.get('incomplete_details') or {}).get('reason')
        raise LunaError('output_limit' if reason == 'max_output_tokens' else 'incomplete_response')
    if response.get('status') != 'completed':
        raise LunaError('api_response_failed')
    parts = [part for item in response.get('output',[]) if item.get('type') == 'message'
             for part in item.get('content',[])]
    if any(part.get('type') == 'refusal' for part in parts):
        raise LunaError('model_refusal')
    output = ''.join(part.get('text','') for part in parts if part.get('type') == 'output_text')
    if not output.strip():
        raise LunaError('no_output')
    try:
        decision = json.loads(output)
    except ValueError:
        raise LunaError('invalid_json') from None
    if (not isinstance(decision,dict) or set(decision) != {'action','reply','sample_id'}
            or decision['action'] not in ('ignore','text','sample') or decision['sample_id'] not in identifiers
            or not isinstance(decision['reply'],str)):
        raise LunaError('invalid_schema')
    decision['reply'] = safe_text(decision['reply'],1800).strip()
    if decision['action'] == 'ignore':
        decision.update(reply='',sample_id='none')
    elif not decision['reply']:
        raise LunaError('empty_reply')
    elif decision['action'] == 'text':
        decision['sample_id'] = 'none'
    elif decision['sample_id'] == 'none':
        decision['action'] = 'text'
    return decision


def read_api_key(path=Path('/etc/cours-watch/.env'),key_name='OPENAI_API_KEY'):
    try:
        for line in path.read_text().splitlines():
            if not line.lstrip().startswith('#') and '=' in line:
                key,value = line.split('=',1)
                if key.strip() == key_name:
                    return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ''


def safe_text(text,limit=1800):
    text = str(text or '')
    text = re.sub(r'https://(?:\w+\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\S+','[webhook masqué]',text)
    text = re.sub(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}','[clé masquée]',text)
    text = re.sub(r'\b(?:cmd|cc)[_-][A-Za-z0-9_-]{12,}','[clé masquée]',text)
    text = re.sub(r'\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}\b','[jeton masqué]',text)
    text = re.sub(r'<@!?\d+>|<@&\d+>','[mention]',text)
    return text.replace('@','@\u200b')[:limit]


class TokenBudget:
    def __init__(self,path,limit=1_000_000,now=None):
        self.path = Path(path)
        self.limit = int(limit)
        self.now = now or (lambda:datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as db:
            from courswatch.storage.schema import migrate
            migrate(db, 'budget')
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path,timeout=10)

    def day(self):
        return self.now().astimezone(TZ).date().isoformat()

    def reserve(self,tokens,kind):
        tokens = max(1,int(tokens))
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            day = self.day()
            used = db.execute('SELECT COALESCE(SUM(charged),0) FROM calls WHERE day=?',(day,)).fetchone()[0]
            blocked = db.execute('SELECT 1 FROM blocked_days WHERE day=?',(day,)).fetchone()
            if blocked or used + tokens > self.limit:
                db.execute('INSERT OR IGNORE INTO blocked_days(day) VALUES(?)',(day,))
                db.commit()
                raise BudgetExhausted()
            result = db.execute('INSERT INTO calls(day,kind,status,reserved,charged,created) VALUES(?,?,?,?,?,?)',
                (day,kind,'reserved',tokens,tokens,self.now().isoformat()))
            db.commit()
            return result.lastrowid

    def settle(self,identity,input_tokens=0,output_tokens=0,status='ok',known_usage=True,
               api_total_tokens=None,cached_input_tokens=0):
        with closing(self.connect()) as db:
            if known_usage:
                input_tokens,output_tokens = max(0,int(input_tokens)),max(0,int(output_tokens))
                total = max(0,int(api_total_tokens)) if api_total_tokens is not None else input_tokens+output_tokens
                db.execute('UPDATE calls SET status=?,charged=?,input_tokens=?,output_tokens=?,api_total_tokens=?,cached_input_tokens=? WHERE id=?',
                    (status,total,input_tokens,output_tokens,api_total_tokens,max(0,int(cached_input_tokens)),identity))
            else:
                # A timed-out request may have been billed: retain the full reservation.
                db.execute('UPDATE calls SET status=? WHERE id=?',(status,identity))
            db.commit()

    def snapshot(self):
        with closing(self.connect()) as db:
            totals = usage_totals(db,self.day())
            blocked = bool(db.execute('SELECT 1 FROM blocked_days WHERE day=?',(self.day(),)).fetchone())
        now = self.now().astimezone(TZ)
        tomorrow = datetime.combine(now.date() + timedelta(days=1),datetime.min.time(),tzinfo=TZ)
        return {'day':self.day(),'daily_token_limit':self.limit,**totals,
                'remaining_tokens':max(0,self.limit-totals['budget_used_tokens']),'blocked':blocked,
                'reset_utc':tomorrow.astimezone(timezone.utc).isoformat()}

    def first_notice(self):
        with closing(self.connect()) as db:
            result = db.execute('INSERT OR IGNORE INTO notices(day) VALUES(?)',(self.day(),))
            db.commit()
            return bool(result.rowcount)


def usage_totals(db,day):
    """Confirmed API usage is distinct from conservative in-flight/unknown quota holds."""
    columns = {row[1] for row in db.execute('PRAGMA table_info(calls)')}
    total = 'COALESCE(api_total_tokens,input_tokens+output_tokens)' if 'api_total_tokens' in columns else 'input_tokens+output_tokens'
    cached = 'COALESCE(SUM(cached_input_tokens),0)' if 'cached_input_tokens' in columns else '0'
    used,budget,calls,input_tokens,output_tokens,cached_tokens = db.execute(
        'SELECT COALESCE(SUM('+total+'),0),COALESCE(SUM(charged),0),COUNT(*),'
        'COALESCE(SUM(input_tokens),0),COALESCE(SUM(output_tokens),0),'+cached+' FROM calls WHERE day=?',(day,)).fetchone()
    return {'used_tokens':used,'budget_used_tokens':budget,'reserved_tokens':max(0,budget-used),
            'calls':calls,'input_tokens':input_tokens,'output_tokens':output_tokens,'cached_input_tokens':cached_tokens}


class ChatMemory:
    def __init__(self,max_messages=30,ttl_seconds=1800,max_bytes=18000,clock=time.monotonic):
        self.rows = deque(maxlen=max_messages)
        self.ttl,self.max_bytes,self.clock = ttl_seconds,max_bytes,clock
        self.bot_ids = deque(maxlen=60)

    def add(self,speaker,text,message_id=None,is_bot=False):
        now = self.clock()
        self.rows.append({'at':now,'speaker':'Haddock' if is_bot else safe_text(speaker,40),
                          'text':safe_text(text,1600),'is_bot':is_bot})
        if is_bot and message_id:
            self.bot_ids.append(int(message_id))
        self.context()

    def context(self):
        now = self.clock()
        while self.rows and now-self.rows[0]['at'] > self.ttl:
            self.rows.popleft()
        while len(self.rows) > 1 and len(json.dumps(list(self.rows),ensure_ascii=False).encode()) > self.max_bytes:
            self.rows.popleft()
        return [{key:row[key] for key in ('speaker','text','is_bot')} for row in self.rows]


def addressed(content,mentioned=False,reply_to_bot=False,bare_trigger=False):
    return bool(mentioned or reply_to_bot or bare_trigger or
                re.search(r'^(?:(?:salut|hey|hé|eh|dis|bonjour|bonsoir|merci|ok|alors)\W+)*(?:capitaine\s+)?haddock\b|\bhaddock\s*[!?.,:]?\s*$',str(content).strip(),re.I))


class ChatCadence:
    """Only ephemeral activity metadata; direct replies and unsolicited posts have separate limits."""
    def __init__(self,clock=time.monotonic):
        self.clock = clock
        self.last_sent = self.last_analysis = float('-inf')
        self.last_ambient = float('-inf')
        self.ambient_times = deque()
        self.partner = None
        self.follow_until = 0

    def followup(self,user_id):
        return self.partner == user_id and self.clock() < self.follow_until

    def ambient_allowed(self):
        now = self.clock()
        while self.ambient_times and now-self.ambient_times[0] >= 3600:
            self.ambient_times.popleft()
        return (now-self.last_analysis >= 30 and now-self.last_sent >= 90
                and now-self.last_ambient >= 180 and len(self.ambient_times) < 4)

    def sent(self,direct,user_id=None,conversation=True):
        now = self.clock()
        self.last_sent = now
        if direct and conversation:
            self.partner,self.follow_until = user_id,now+120
        elif not direct:
            self.last_ambient = now
            self.ambient_times.append(now)


class LunaBrain:
    def __init__(self,data_dir,config,api_key=None,transport=None,now=None):
        self.data = Path(data_dir)
        self.config = config
        self.provider = config.get('provider','openai')
        if self.provider not in PROVIDERS:
            raise ValueError('Unsupported conversation provider')
        profile = PROVIDERS[self.provider]
        self.endpoint = profile['endpoint']
        self.key = read_api_key(key_name=profile['key_name']) if api_key is None else api_key
        self.model = config.get('model',profile['model'])
        self.require_zdr = config.get('require_zdr', True)
        if not isinstance(self.require_zdr, bool):
            raise ValueError('require_zdr must be a boolean')
        self.persona_version = PERSONA_VERSION
        self.budget = TokenBudget(self.data / 'luna-usage.db',config.get('daily_token_limit',1_000_000),now=now)
        self.transport = transport or self.request
        self.publish(('daily_limit' if self.budget.snapshot()['blocked'] else 'ready') if self.key else 'missing_key')

    def publish(self,status,error_code=None,outcome=None):
        report = {**self.budget.snapshot(),'provider':self.provider,'model':self.model,'require_zdr':self.require_zdr,'persona_version':self.persona_version,'status':status,
                  'checked_utc':datetime.now(timezone.utc).isoformat(timespec='seconds')}
        if error_code:
            report['error_code'] = error_code
        if outcome:
            report['last_outcome'] = outcome
        directory = self.data / 'reports'
        directory.mkdir(exist_ok=True)
        pending = directory / 'luna-status.pending'
        pending.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        pending.chmod(0o600)
        os.replace(pending,directory / 'luna-status.json')

    def request(self,payload):
        headers = {'Authorization':'Bearer ' + self.key,'Content-Type':'application/json','User-Agent':'UPBCourseWatch/1.0'}
        if self.provider == 'commandcode' and self.require_zdr:
            headers['x-cmd-zdr'] = '1'
        request = urllib.request.Request(self.endpoint,
            data=json.dumps({**payload,'stream':True},ensure_ascii=False).encode(),method='POST',
            headers=headers)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request,timeout=30) as response:
                return read_model_response(response,started)
        except urllib.error.HTTPError as error:
            raise LunaError('http_' + str(error.code)) from None
        except (OSError,ValueError):
            raise LunaError('connection_failed') from None

    def decide(self,context,direct,samples=(),facts=''):
        if not self.key:
            raise LunaError('missing_key',local_metrics())
        identifiers = ['none'] + [sample.id for sample in samples]
        schema = {'type':'object','properties':{'action':{'type':'string','enum':['ignore','text','sample']},
            'reply':{'type':'string'},'sample_id':{'type':'string','enum':identifiers}},
            'required':['action','reply','sample_id'],'additionalProperties':False}
        data = {'mode':'direct' if direct else 'ambient','discussion':context,
                'samples':{sample.id:SAMPLE_HINTS.get(sample.id,sample.label) for sample in samples},
                'faits_cours_verifies':safe_text(facts,7000)}
        payload = {'model':self.model,'instructions':PROMPT,'store':False,
            'reasoning':{'effort':'none'},'max_output_tokens':700,
            'input':[{'role':'user','content':json.dumps(data,ensure_ascii=False)}],
            'text':{'format':{'type':'json_schema','name':'haddock_decision','strict':True,'schema':schema}}}
        # UTF-8 byte count plus ample protocol overhead is conservative for text tokens.
        bound = len(json.dumps(payload,ensure_ascii=False).encode()) + 4096 + payload['max_output_tokens']
        try:
            reservation = self.budget.reserve(bound,'direct' if direct else 'ambient')
        except BudgetExhausted:
            self.publish('daily_limit')
            raise
        started = time.perf_counter()
        try:
            response = self.transport(payload)
        except LunaError as error:
            known_unbilled = error.code in ('http_400','http_401','http_402','http_403','http_404','http_422','http_429')
            error.metrics = {'model':self.model,'api_ms':(time.perf_counter()-started)*1000,
                             'ttft_ms':None,'total_tokens':0 if known_unbilled else None}
            self.budget.settle(reservation,status=error.code,known_usage=known_unbilled)
            self.publish('error',error.code)
            raise
        except Exception:
            self.budget.settle(reservation,status='unknown_error',known_usage=False)
            self.publish('error','unknown_error')
            raise LunaError('unknown_error',{'model':self.model,'ttft_ms':None,
                'api_ms':(time.perf_counter()-started)*1000,'total_tokens':None}) from None
        metrics = response_metrics(response,self.model,(time.perf_counter()-started)*1000)
        usage = response.get('usage') or {}
        self.budget.settle(reservation,usage.get('input_tokens',0),usage.get('output_tokens',0),
            known_usage='input_tokens' in usage and 'output_tokens' in usage,
            api_total_tokens=usage.get('total_tokens'),
            cached_input_tokens=(usage.get('input_tokens_details') or {}).get('cached_tokens',0))
        try:
            decision = parse_decision(response,identifiers)
        except LunaError as error:
            error.metrics = metrics
            if error.code == 'model_refusal':
                self.publish('ready',outcome='model_refusal')
            else:
                self.publish('degraded' if error.code in RESPONSE_ERRORS else 'error',error.code)
            raise
        except (ValueError,KeyError,TypeError,AttributeError):
            self.publish('degraded','invalid_response')
            raise LunaError('invalid_response',metrics) from None
        self.publish('ready')
        return MeasuredResult(decision,metrics)
