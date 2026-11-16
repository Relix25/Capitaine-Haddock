"""Validated custom emoji references, with ordinary emoji fallbacks."""
import json
import os
from pathlib import Path
import re


def catalog():
    try:
        data = json.loads(Path(os.environ.get('COURS_WATCH_EMOJIS','/etc/cours-watch/emojis.json')).read_text())
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):
        return {}


def reference(kind):
    value = catalog().get(kind,{})
    if (isinstance(value,dict) and re.fullmatch(r'[A-Za-z0-9_]{2,32}',str(value.get('name','')))
            and re.fullmatch(r'[0-9]{15,22}',str(value.get('id','')))):
        return {'id':str(value['id']),'name':value['name']}
    return None


def icon(kind,fallback=''):
    value = reference(kind)
    return '<:' + value['name'] + ':' + value['id'] + '>' if value else fallback


def button_emoji():
    return reference('project') or {'name':'📖'}


def image_url(kind):
    value = reference(kind)
    return 'https://cdn.discordapp.com/emojis/' + value['id'] + '.webp?size=128' if value else None


def task_icon(title,urgent=False):
    if urgent:
        return icon('warning','🔴')
    oral = any(word in title.casefold() for word in ('oral','présent','present','soutenance'))
    return icon('oral','📌') if oral else icon('project','📌')
