"""Read-only health, real timer schedule and confirmed announcement history."""
from datetime import datetime, time, timedelta, timezone
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from courswatch.reminders import reminder_events
from courswatch.discord_app.emojis import icon, image_url
from courswatch.conversation.brain import usage_totals
from courswatch.conversation.models import effective_settings

TZ = ZoneInfo('Europe/Bucharest')
UNITS = ['cours-watch-bot.service','cours-watch.service','cours-watch.timer','cours-watch-session.timer','cours-watch-session.service','cours-watch-esn.timer','cours-watch-esn.service']


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def instant(value):
    try:
        if str(value).startswith('@'):
            return datetime.fromtimestamp(float(str(value)[1:]),timezone.utc)
        result = datetime.fromisoformat(str(value))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError,OverflowError):
        return None


def collect_units():
    properties = ['Id','ActiveState','SubState','Result','ExecMainStatus','ExecMainExitTimestamp',
                  'NextElapseUSecRealtime','LastTriggerUSec']
    args = ['systemctl','show','--no-pager','--timestamp=unix']
    for name in properties:
        args += ['-p',name]
    try:
        result = subprocess.run(args + UNITS,capture_output=True,text=True,timeout=5,check=False)
        units = {}
        for block in result.stdout.split('\n\n'):
            record = dict(line.split('=',1) for line in block.splitlines() if '=' in line)
            if record.get('Id'):
                units[record['Id']] = record
        return units
    except (OSError,subprocess.SubprocessError):
        return {}


def announcement_history(db, config):
    rows = db.execute('SELECT id,event_key,created,delivered,payload FROM outbox ORDER BY id DESC LIMIT 500').fetchall()
    groups = {}
    daily = {}
    for event_id,key,created,delivered,payload in rows:
        parts = key.split(':')
        prefix = parts[0]
        if prefix not in ('daily-reminder','initial-brief','interactive-demo','translation-archive'):
            try:
                if json.loads(payload).get('kind') not in ('new','modified'):
                    continue
            except ValueError:
                continue
            prefix = 'updates'
        day = parts[1] if prefix in ('daily-reminder','initial-brief') else (created[:16] if prefix == 'updates' else (instant(created).astimezone(TZ).date().isoformat() if instant(created) else ''))
        group_key = prefix + ':' + day
        group = groups.setdefault(group_key,{'kind':prefix,'date':day,'total':0,'confirmed':0,'last_utc':created,'url':None})
        group['total'] += 1
        group['confirmed'] += int(bool(delivered))
        if created > group['last_utc']:
            group['last_utc'] = created
        if delivered and not group['url']:
            receipt = db.execute('SELECT message_id,channel_id FROM discord_receipts WHERE event_id=? AND channel_id=?',
                                 (event_id,str(config['discord']['channel_id']))).fetchone()
            if receipt:
                group['url'] = 'https://discord.com/channels/' + str(config['discord']['guild_id']) + '/' + receipt[1] + '/' + receipt[0]
        if prefix == 'daily-reminder':
            daily[day] = group
    pending = db.execute('SELECT COUNT(*) FROM outbox WHERE delivered IS NULL').fetchone()[0]
    last_delivery = db.execute('SELECT MAX(delivered) FROM deliveries').fetchone()[0]
    return sorted(groups.values(),key=lambda group:group['last_utc'],reverse=True)[:3], daily, pending, last_delivery


def build_status_snapshot(data_dir, config, bot_ready, latency, manual_running=False, now=None, units=None, gate_ready=None):
    now = now.astimezone(TZ) if now else datetime.now(TZ)
    units = collect_units() if units is None else units
    gate_ready = Path('/etc/cours-watch/ready').exists() if gate_ready is None else gate_ready
    report = read_json(data_dir / 'reports/latest.json')
    session = read_json(data_dir / 'reports/moodle-session.json')
    bot_report = read_json(data_dir / 'reports/bot-status.json')
    source_rows = []
    batches, daily, pending, last_delivery = [], {}, 0, None
    expected_today = None
    issues = []
    reminder_settings = read_json(Path(config.get('reminders_file','/etc/cours-watch/reminders.json')))
    if not reminder_settings:
        issues.append('Règles de rappel indisponibles')
    if config['discord'].get('mode') == 'disabled':
        issues.append('Diffusion Discord désactivée')
    try:
        with closing(sqlite3.connect('file:' + str(data_dir / 'state.db') + '?mode=ro',uri=True)) as db:
            source_rows = db.execute('SELECT source,status,initialized,checked FROM sources').fetchall()
            batches, daily, pending, last_delivery = announcement_history(db,config)
            if reminder_settings.get('enabled',True) and reminder_settings.get('tasks'):
                _, events = reminder_events(SimpleNamespace(db=db),reminder_settings,now)
                expected_today = len(events)
            elif reminder_settings:
                expected_today = 0
    except (OSError,sqlite3.Error,ValueError,KeyError):
        issues.append('Historique ou base de suivi illisible')
    indexed = {row[0]:row for row in source_rows}
    sources = {}
    for platform in ('moodle','teams'):
        expected = [platform + ':' + course['id'] for course in config.get('courses',[])]
        sources[platform] = {'ok':sum(indexed.get(key,('',None))[1] == 'ok' for key in expected),'total':len(expected)}
    sources_ok = sum(value['ok'] for value in sources.values())
    sources_total = sum(value['total'] for value in sources.values())
    if not sources_total or sources_ok != sources_total:
        issues.append('Sources à revérifier : ' + str(sources_ok) + '/' + str(sources_total) + ' OK')
    timer = units.get('cours-watch.timer',{})
    collector = units.get('cours-watch.service',{})
    running = manual_running or collector.get('ActiveState') in ('activating','active','reloading')
    automatic = timer.get('ActiveState') == 'active' and gate_ready
    if not automatic:
        issues.append('Contrôle automatique désactivé ou état indisponible')
    if not bot_ready or units.get('cours-watch-bot.service',{}).get('ActiveState') != 'active':
        issues.append('Connexion ou service du bot à vérifier')
    checked = instant(report.get('checked_utc'))
    if checked is None or now - checked > timedelta(hours=26):
        issues.append('Dernière collecte absente ou trop ancienne')
    session_time = instant(session.get('checked_utc'))
    if session.get('status') != 'ok':
        issues.append('Session Moodle à vérifier')
    elif session_time is None or now - session_time > timedelta(minutes=45):
        issues.append('Maintien de session Moodle à vérifier')
    if units.get('cours-watch-session.timer',{}).get('ActiveState') != 'active':
        issues.append('Maintien automatique de Moodle désactivé')
    maintenance = units.get('cours-watch-session.service',{})
    maintenance_exit = instant(maintenance.get('ExecMainExitTimestamp'))
    if maintenance.get('Result') not in (None,'success') and (not session_time or not maintenance_exit or maintenance_exit > session_time):
        issues.append('Dernier maintien de session en échec')
    if pending and not running:
        issues.append(str(pending) + ' annonce(s) en attente d’envoi')
    exited = instant(collector.get('ExecMainExitTimestamp'))
    bad_run = collector.get('Result') not in (None,'success') or collector.get('ExecMainStatus') not in (None,'','0','2')
    if bad_run and not running and (not checked or not exited or exited > checked):
        issues.append('Dernier passage programmé en échec')
    manual = bot_report.get('last_manual_scan',{})
    manual_finished = instant(manual.get('finished_utc'))
    if manual.get('status') in ('failed','timeout','interrupted') and not running and (not checked or (manual_finished and manual_finished > checked)):
        issues.append('Dernière actualisation manuelle non aboutie')
    day = now.date().isoformat()
    today = daily.get(day,{'confirmed':0,'total':0})
    hour = reminder_settings.get('policy',{}).get('send_hour',8)
    due = datetime.combine(now.date(),time(hour),tzinfo=TZ)
    if not reminder_settings:
        today_state = 'unknown'
    elif today['total'] and today['confirmed'] == today['total']:
        today_state = 'confirmed'
    elif not reminder_settings.get('enabled',True) or not automatic:
        today_state = 'disabled'
    elif expected_today == 0:
        today_state = 'not_required'
    elif now < due:
        today_state = 'upcoming'
    elif running or now < due + timedelta(minutes=20):
        today_state = 'preparing'
    else:
        today_state = 'unconfirmed'
        issues.append('Rappel du jour non confirmé')
    next_check = instant(timer.get('NextElapseUSecRealtime')) if automatic else None
    luna = {}
    luna_config = effective_settings(data_dir,config.get('discord',{}).get('luna',{}))
    if luna_config.get('enabled'):
        luna = read_json(data_dir / 'reports/luna-status.json')
        provider = luna_config.get('provider','openai')
        default_model = 'deepseek/deepseek-v4.1-flash' if provider == 'commandcode' else 'gpt-6-luna'
        luna.update(provider=provider,model=luna_config.get('model',default_model),
                    daily_token_limit=luna_config.get('daily_token_limit',1_000_000))
        try:
            with closing(sqlite3.connect('file:' + str(data_dir / 'luna-usage.db') + '?mode=ro',uri=True)) as db:
                totals = usage_totals(db,day)
                blocked = bool(db.execute('SELECT 1 FROM blocked_days WHERE day=?',(day,)).fetchone())
            luna.update(**totals,remaining_tokens=max(0,luna['daily_token_limit']-totals['budget_used_tokens']),blocked=blocked)
            if blocked:
                luna['status'] = 'daily_limit'
            elif luna.get('status') == 'daily_limit':
                luna['status'] = 'ready'
        except sqlite3.Error:
            luna.update(status='unavailable',used_tokens=None)
    esn = {}
    if config.get('esn_file'):
        settings = read_json(Path(config['esn_file']))
        if settings.get('enabled'):
            esn = read_json(data_dir / 'reports/esn-status.json')
            esn.update(enabled=True,timer_active=units.get('cours-watch-esn.timer',{}).get('ActiveState') == 'active')
            if not (settings.get('role_id') or esn.get('role_id')):
                esn['delivery'] = 'waiting_role'
                issues.append('ESN : rôle de notification à configurer')
            last = instant(esn.get('last_success_utc'))
            if not last or now-last > timedelta(minutes=15):
                issues.append('ESN : dernier contrôle absent ou trop ancien')
            elif esn.get('status') == 'error':
                issues.append('ESN : dernier contrôle ou envoi en échec')
            if not esn['timer_active']:
                issues.append('ESN : surveillance automatique inactive')
    return {'checked_utc':now.astimezone(timezone.utc).isoformat(timespec='seconds'),
            'bot_ready':bool(bot_ready),'latency_ms':round(latency * 1000) if latency is not None and math.isfinite(latency) else None,
            'sources':sources,'sources_ok':sources_ok,'sources_total':sources_total,
            'last_collection_utc':report.get('checked_utc'),'session':session,
            'running':running,'automatic':automatic,
            'next_check_utc':next_check.isoformat() if next_check else None,
            'collector':collector,'manual':manual,'pending':pending,'last_delivery_utc':last_delivery,
            'batches':batches,'today':{'state':today_state,'confirmed':today['confirmed'],
                                      'total':today['total'],'expected':expected_today,'hour':hour},
            'issues':issues,'luna':luna,'esn':esn}


def shown(value):
    moment = instant(value)
    return moment.astimezone(TZ).strftime('%d/%m à %H:%M') if moment else 'non disponible'


def status_embed(snapshot):
    issues = snapshot['issues']
    bot = (icon('success','🟢') + ' Connecté' if snapshot['bot_ready'] else icon('warning','🔴') + ' Déconnecté')
    if snapshot['latency_ms'] is not None:
        bot += ' · ' + str(snapshot['latency_ms']) + ' ms'
    platforms = ['Moodle **' + str(snapshot['sources']['moodle']['ok']) + '/' + str(snapshot['sources']['moodle']['total']) + '**',
                 'Teams **' + str(snapshot['sources']['teams']['ok']) + '/' + str(snapshot['sources']['teams']['total']) + '**']
    platform_text = ' · '.join(platforms) + '\nDernière collecte : ' + shown(snapshot['last_collection_utc'])
    platform_text += '\nSession Moodle : ' + ('OK' if snapshot['session'].get('status') == 'ok' else 'à vérifier') + ' · ' + shown(snapshot['session'].get('checked_utc'))
    if snapshot['running']:
        next_text = '**Collecte en cours** — résultat à la fin.\n'
    else:
        next_text = ''
    if snapshot['automatic']:
        next_text += '**' + shown(snapshot['next_check_utc']) + '**, heure de Bucarest.\nPublication après la collecte (~2 min).'
    else:
        next_text += '**Automatisation désactivée ou indisponible.**'
    today = snapshot['today']
    labels = {'upcoming':'Prévu à ' + str(today['hour']).zfill(2) + ':00 — pas encore dû',
              'confirmed':icon('success','✅') + ' Envoyé : ' + str(today['confirmed']) + '/' + str(today['total']) + ' messages confirmés',
              'preparing':icon('project','⏳') + ' En préparation ou envoi en cours', 'unconfirmed':icon('warning','⚠️') + ' Envoi du jour non confirmé',
              'not_required':'Aucun projet à venir à rappeler', 'disabled':'Rappels désactivés',
              'unknown':'Configuration des rappels à vérifier'}
    history = []
    names = {'daily-reminder':'Rappels','initial-brief':'Point initial','interactive-demo':'Point des cours','updates':'Actualités','translation-archive':'Archives traduites'}
    for batch in snapshot['batches']:
        good = batch['confirmed'] == batch['total']
        line = (icon('success','✅') if good else icon('warning','⚠️')) + ' ' + names[batch['kind']] + ' · ' + shown(batch['last_utc'])
        line += ' · **' + str(batch['confirmed']) + '/' + str(batch['total']) + '**'
        if batch.get('url'):
            line += ' · [voir](' + batch['url'] + ')'
        history.append(line)
    delivery = '**' + str(snapshot['pending']) + '** en attente\nDernier envoi confirmé : ' + shown(snapshot['last_delivery_utc'])
    if issues:
        delivery += '\n' + '\n'.join('• ' + issue for issue in issues[:4])
    embed = {'title':'Capitaine Haddock · État du service',
            'color':0x2ECC71 if not issues else 0xF39C12,
            'description':icon('warning' if issues else 'success') + ' Mille sabords ! État des derniers contrôles et des envois.',
            'fields':[{'name':'Bot','value':bot,'inline':True},
                      {'name':'Plateformes','value':platform_text,'inline':False},
                      {'name':'Prochain point automatique','value':next_text,'inline':False},
                      {'name':'Rappel du jour','value':labels[today['state']],'inline':False},
                      {'name':'Annonces précédentes','value':'\n'.join(history) or 'Aucun envoi enregistré.', 'inline':False},
                      {'name':'Envois et points à vérifier','value':delivery[:1000], 'inline':False}],
            'footer':{'text':'Heures de Bucarest • Confirmé = accepté par Discord ; lecture non suivie'}}
    thumbnail = image_url('warning' if issues else 'success')
    esn = snapshot.get('esn',{})
    if esn:
        state = 'À vérifier' if esn.get('status') != 'ok' else 'Surveillance active'
        if esn.get('delivery') == 'waiting_role': state += ' · rôle ESN à configurer'
        value = state+'\nContrôle toutes les 5 minutes · dernier succès : '+shown(esn.get('last_success_utc'))
        value += '\n'+str(esn.get('active_links','?'))+' liens actifs · '+str(esn.get('confirmed',0))+' envois confirmés · '+str(esn.get('pending',0))+' en attente.'
        embed['fields'].append({'name':'Activités · ESN Bucarest','value':value,'inline':False})
    luna = snapshot.get('luna',{})
    if luna:
        label = {'ready':'Disponible','daily_limit':'En cale sèche · quota quotidien atteint',
                 'missing_key':'Clé API absente','error':'Liaison API à vérifier',
                 'degraded':'Dernière réponse inexploitable · nouvel essai possible',
                 'unavailable':'Compteur indisponible'}.get(luna.get('status'),'État à vérifier')
        usage = ('{:,} / {:,}'.format(luna['used_tokens'],luna['daily_token_limit']).replace(',',' ')
                 if luna.get('used_tokens') is not None else 'non disponible')
        provider = {'commandcode':'CommandCode','openai':'OpenAI'}.get(luna.get('provider'),'')
        value = label+' · '+(provider+' · ' if provider else '')+luna['model']+'\nTokens confirmés aujourd’hui : **'+usage+'** (entrée + sortie, cache inclus).'
        if luna.get('reserved_tokens',0):
            value += '\nRéservés / non confirmés : **'+format(luna['reserved_tokens'],',').replace(',',' ')+'** · consommation encore inconnue.'
        value += '\nRemise à zéro à minuit, heure de Bucarest.'
        embed['fields'].append({'name':'Conversations · Haddock','value':value,'inline':False})
    if thumbnail:
        embed['thumbnail'] = {'url':thumbnail}
    return embed
