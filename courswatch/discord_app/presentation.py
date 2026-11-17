"""Compact public embeds; full event text is retained for private interaction replies."""
import re
from courswatch.discord_app.emojis import icon, task_icon, button_emoji, image_url
from courswatch.translation import display_event

DETAIL_CUSTOM_ID = 'cours-watch:details'
COLORS = {'urgent': 0xE74C3C, 'important': 0xF39C12, 'normal': 0x3498DB, 'recovered': 0x2ECC71}
REMINDER = re.compile(r'^(🔴 \*\*URGENT\*\*|🟠 \*\*IMPORTANT\*\*|📌 À prévoir) — \*\*(.+?)\*\* : \*\*(.+?)\*\* \((.+?)\)\.$')


def safe(value):
    return str(value or '').replace('@', '@\u200b')


def short_text(value):
    return safe(re.sub(r'\s+', ' ', str(value or '')).strip())


def safe_url(value):
    return value if value and value.startswith('https://') and len(value) <= 600 and '\n' not in value and '\r' not in value else None


def compact_embed(event, interactive=False):
    translation = event.get('translation',{})
    event = display_event(event)
    kind = event.get('kind', '')
    if kind == 'translation_archive':
        card = {'title':short_text(event.get('course','UPB') + ' · Annonces en français')[:256],
                'color':COLORS['normal'],
                'description':icon('project') + ' **' + str(event['announcement_count']) + ' messages archivés** de Teams/Moodle.\nDates des publications d’origine ; détails et textes originaux avec le bouton.',
                'fields':[], 'footer':{'text':'Traduction automatique • Archives, pas de nouvelle échéance'}}
        for item in event.get('preview',[])[:3]:
            card['fields'].append({'name':short_text(item['date'] + ' · ' + item['title'])[:180],
                                   'value':safe(re.sub(r'\s+',' ',item['body']))[:230] + '…','inline':False})
        thumbnail = image_url('project')
        if thumbnail:
            card['thumbnail'] = {'url':thumbnail}
        return card
    color = COLORS['normal']
    fields = []
    pending = None
    for line in str(event.get('body', '')).splitlines():
        match = REMINDER.match(line.strip())
        if match:
            label, title, when, countdown = match.groups()
            urgency_icon = '🔴' if 'URGENT' in label else '🟠' if 'IMPORTANT' in label else '📌'
            status = 'URGENT' if urgency_icon == '🔴' else 'IMPORTANT' if urgency_icon == '🟠' else ''
            if urgency_icon == '🔴':
                color = COLORS['urgent']
            elif urgency_icon == '🟠' and color != COLORS['urgent']:
                color = COLORS['important']
            # Midnight is a meaningful deadline distinction, not an extra detail.
            when = when.replace(' à 00:00', ' à 00:00 (début de journée)')
            pending = {'name': short_text(title)[:110],
                       'value': task_icon(title,urgent=status == 'URGENT') + ' ' + safe(when + ' · ' + countdown + (' · ' + status if status else ''))[:300],
                       'inline': False}
            fields.append(pending)
        elif line.startswith('⚠️ **DATE À CONFIRMER'):
            fields.append({'name': 'Date à confirmer', 'value': icon('warning','⚠️') + ' ' + safe(line.replace('⚠️ ', ''))[:350], 'inline':False})
            if color != COLORS['urgent']:
                color = COLORS['important']
        elif pending and not interactive:
            link = re.match(r'^\[Source\]\(<(https://[^>]+)>\)$', line)
            if link and safe_url(link[1]):
                pending['value'] += '\n[Consignes](' + link[1] + ')'
    labels = {'daily_reminder':'Projets à venir', 'initial_brief':'Projets à venir',
              'source_error':'Vérification impossible', 'source_recovered':'Accès rétabli',
              'new':'Nouveau', 'modified':'Information actualisée', 'test':'Capitaine Haddock'}
    embed = {'title': short_text(event.get('course', 'UPB') + ' · ' + labels.get(kind, 'Point des cours'))[:256],
             'color': color}
    if fields:
        embed['fields'] = fields[:5]
        embed['footer'] = {'text': 'Mille sabords ! • ' + ('Détails privés avec le bouton' if interactive else 'Dates affichées par Moodle ou publiées dans le calendrier')}
    else:
        embed['title'] = short_text(event.get('course', 'UPB') + ' · ' + event.get('title', labels.get(kind, 'Actualité')))[:180]
        body = re.sub(r'\s+', ' ', str(event.get('body', ''))).strip()
        if body.startswith('SHA256 '):
            body = 'Le contenu du document a changé.'
        embed['description'] = safe(body[:210] + ('…' if len(body) > 210 else '')) or labels.get(kind, 'Consulter les détails.')
        emoji_kind = 'warning' if kind == 'source_error' else 'success' if kind == 'source_recovered' else 'project'
        embed['description'] = icon(emoji_kind) + ' ' + embed['description']
        if kind == 'source_error':
            embed['color'] = COLORS['urgent']
        elif kind == 'source_recovered':
            embed['color'] = COLORS['recovered']
        embed['footer'] = {'text': 'Capitaine Haddock • ' + ('Plus d’infos en privé' if interactive else 'Mille sabords !')}
    url = safe_url(event.get('url'))
    if 'Dernière date connue' in str(event.get('body', '')):
        embed['footer'] = {'text':'Dernières dates connues • plateforme à revérifier'}
    if url:
        embed['url'] = url
    thumbnail = image_url('warning' if color in (COLORS['urgent'],COLORS['important']) else 'project')
    if kind == 'source_error':
        thumbnail = image_url('warning')
    elif kind == 'source_recovered':
        thumbnail = image_url('success')
    if thumbnail:
        embed['thumbnail'] = {'url':thumbnail}
    if translation.get('status') == 'translated':
        embed['footer'] = {'text':'Traduction automatique en français • ' + ('Original dans les détails' if interactive else 'Consulter aussi la source originale')}
    elif translation.get('status') == 'failed':
        embed['footer'] = {'text':'Traduction indisponible • Texte original affiché'}
    return embed


def embed_payload(event, mentions='', interactive=False):
    payload = {'content': mentions, 'embeds':[compact_embed(event, interactive)]}
    if interactive:
        payload['components'] = [{'type':1,'components':[
            {'type':2,'style':2,'label':'Plus d’infos','emoji':button_emoji(),'custom_id':DETAIL_CUSTOM_ID}]}]
    return payload


def private_details(event):
    """Private pages preserve the complete snapshot and never create mentions."""
    original = event
    event = display_event(event)
    translation = original.get('translation',{})
    heading = '**' + safe(re.sub(r'\s+',' ',event.get('course', 'UPB')))[:80] + ' · ' + safe(re.sub(r'\s+',' ',event.get('title', 'Détails')))[:150] + '**'
    body = safe(event.get('body', 'Aucun détail supplémentaire.'))
    if translation.get('status') == 'translated':
        language = {'en':'anglais','ro':'roumain'}.get(translation.get('source_language'),'langue détectée')
        body = '**Traduction automatique · ' + language + ' → français**\n' + body
        body += '\n\n**Texte original (' + language + ')**\n' + safe(original.get('title','')) + '\n' + safe(original.get('body',''))
    elif translation.get('status') == 'failed':
        body = '**Traduction indisponible — texte original**\n' + body
    url = safe_url(event.get('url'))
    if url:
        body += '\n<' + url + '>'
    pages = []
    remaining = body
    while remaining:
        split = min(len(remaining), 1700)
        if split < len(remaining):
            boundary = remaining.rfind('\n', 0, split)
            if boundary > 700:
                split = boundary + 1
        pages.append(heading + '\n' + remaining[:split])
        remaining = remaining[split:]
    return pages or [heading]


def interaction_allowed(guild_id, channel_id, role_ids, configuration):
    return (str(guild_id) == str(configuration.get('guild_id'))
            and str(channel_id) == str(configuration.get('channel_id'))
            and bool(set(map(str, role_ids)) & set(map(str, configuration.get('role_ids', [])))))
