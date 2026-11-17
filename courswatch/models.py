"""Pure observation models and canonical identities shared by application modules."""
from __future__ import annotations
import hashlib
import json
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')



def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()



def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()



def canonical_forum_url(url):
    parsed = urllib.parse.urlsplit(url)
    discussion = urllib.parse.parse_qs(parsed.query).get('d', [''])[0]
    if parsed.hostname != 'curs.upb.ro' or not parsed.path.endswith('/mod/forum/discuss.php') or not discussion.isdigit():
        return None
    return urllib.parse.urlunsplit(('https', parsed.netloc, parsed.path, 'd=' + discussion, ''))



@dataclass
class Item:
    key: str
    title: str
    body: str
    url: str

    @property
    def fingerprint(self):
        return digest([clean(self.title), clean(self.body), self.url])



@dataclass
class Result:
    source: str
    course: str
    status: str
    items: list[Item]
    detail: str = ''

