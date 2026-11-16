"""Scoped failure metadata; --probe replays one failed request without posting to Discord."""
from datetime import datetime
import json
from pathlib import Path
import sys
import urllib.request
from unittest.mock import patch

from cours_watch import load_discord_secrets
from haddock_voice import load_samples
from luna_brain import LunaBrain, LunaError, BudgetExhausted, OFFLINE_REPLY, safe_text

DATA = Path('/var/lib/cours-watch')
config = json.loads(Path('/etc/cours-watch/config.json').read_text())['discord']
token = load_discord_secrets()['DISCORD_BOT_TOKEN']
report = json.loads((DATA/'reports/luna-status.json').read_text())

def get(path):
    request = urllib.request.Request('https://discord.com/api/v10'+path,
        headers={'Authorization':'Bot '+token,'User-Agent':'UPBCourseWatch/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response: return json.load(response)
    except Exception:
        raise SystemExit('Discord diagnostic request failed; no content or credential logged') from None

messages = list(reversed(get('/channels/'+config['channel_id']+'/messages?limit=40')))
fallbacks = [message for message in messages if message['author']['id'] == config['application_id']
             and message.get('content') == OFFLINE_REPLY and message.get('message_reference',{}).get('message_id')]
print(json.dumps({'luna_status':report.get('status'),'error_code':report.get('error_code'),
    'checked_utc':report.get('checked_utc'),'recent_fallbacks':[{'id':m['id'],'created':m['timestamp'],
    'reference_id':m['message_reference']['message_id']} for m in fallbacks[-6:]]}),flush=True)
if '--probe' not in sys.argv: raise SystemExit(0)
if not fallbacks: raise SystemExit('No failed reply available to probe')
checked = datetime.fromisoformat(report['checked_utc'])
fallback = min(fallbacks,key=lambda message:abs((datetime.fromisoformat(message['timestamp'])-checked).total_seconds()))
target_id = fallback['message_reference']['message_id']
target_index = next((index for index,message in enumerate(messages) if message['id'] == target_id),None)
if target_index is None: raise SystemExit('Failed source message outside recent diagnostic window')
context,allowed,aliases = [],{},{}
for message in messages[max(0,target_index-20):target_index+1]:
    author = message['author']
    if author.get('bot'):
        if author['id'] != config['application_id'] or message.get('content') == OFFLINE_REPLY: continue
        speaker = 'Haddock'
    else:
        if author['id'] not in allowed:
            member = get('/guilds/'+config['guild_id']+'/members/'+author['id'])
            allowed[author['id']] = bool(set(member['roles']) & set(config['role_ids']))
        if not allowed[author['id']]: continue
        speaker = aliases.setdefault(author['id'],'Camarade '+str(len(aliases)+1))
    if message.get('content'):
        context.append({'speaker':speaker,'text':safe_text(message['content'],1600),'is_bot':bool(author.get('bot'))})
print(json.dumps({'probe_target_id':target_id,'context_messages':len(context),'posts_to_discord':False}),flush=True)

# A diagnostic must not replace the live client's health report.
with patch.object(LunaBrain,'publish'):
    brain = LunaBrain(DATA,config['luna'])
    def inspect_response(payload):
        response = brain.request(payload)
        parts = [part for item in response.get('output',[]) if item.get('type') == 'message' for part in item.get('content',[])]
        output = ''.join(part.get('text','') for part in parts if part.get('type') == 'output_text')
        metadata = {'api_status':response.get('status'),'incomplete_reason':(response.get('incomplete_details') or {}).get('reason'),
            'content_types':[part.get('type') for part in parts],'output_chars':len(output),'usage':response.get('usage')}
        try:
            value = json.loads(output)
            metadata.update(json_valid=True,keys_valid=isinstance(value,dict) and set(value) == {'action','reply','sample_id'},
                            action=value.get('action') if isinstance(value,dict) and value.get('action') in ('ignore','text','sample') else 'invalid',
                            sample_id_valid=isinstance(value,dict) and value.get('sample_id') in ['none']+[sample.id for sample in load_samples()])
        except ValueError: metadata['json_valid'] = False
        print(json.dumps(metadata,ensure_ascii=False),flush=True)
        return response
    brain.transport = inspect_response
    try:
        decision = brain.decide(context,True,load_samples())
        print(json.dumps({'probe_result':'valid','action':decision['action'],'reply_chars':len(decision['reply'])}),flush=True)
    except (LunaError,BudgetExhausted) as error:
        print(json.dumps({'probe_result':'failed','error_code':getattr(error,'code','budget_exhausted')}),flush=True)
