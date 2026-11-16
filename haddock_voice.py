"""Validated local Opus samples and native Discord voice-message multipart uploads."""
import base64
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import re
import secrets
import time
import urllib.error
import urllib.request

ASSETS = Path('/opt/cours-watch/assets/haddock-audio')
VOICE_FLAG = 1 << 13


@dataclass(frozen=True)
class VoiceSample:
    id: str
    label: str
    path: Path
    duration_secs: float
    waveform: str
    sha256: str


class VoiceMessageError(RuntimeError):
    pass


def load_samples(root=ASSETS):
    try:
        root = root.resolve()
        manifest = json.loads((root / 'manifest.json').read_text())
        if manifest.get('version') != 1 or not isinstance(manifest.get('samples'),list) or len(manifest['samples']) > 30:
            return []
        samples = []
        for row in manifest['samples']:
            identity = row['id']
            filename = row['file']
            if not re.fullmatch(r'[a-z0-9_]{1,40}',identity) or filename != identity + '.ogg':
                return []
            path = (root / filename).resolve()
            if not path.is_relative_to(root) or not 100 <= path.stat().st_size <= 512 * 1024:
                return []
            duration = float(row['duration_secs'])
            wave = base64.b64decode(row['waveform'],validate=True)
            if not math.isfinite(duration) or not 0 < duration <= 12 or not 1 <= len(wave) <= 256:
                return []
            blob = path.read_bytes()
            if not blob.startswith(b'OggS') or b'OpusHead' not in blob[:128] or hashlib.sha256(blob).hexdigest() != row['sha256']:
                return []
            samples.append(VoiceSample(identity,str(row['label'])[:80],path,duration,row['waveform'],row['sha256']))
        return samples if len({sample.id for sample in samples}) == len(samples) else []
    except (OSError,ValueError,TypeError,KeyError):
        return []


def choose_sample(samples,previous=None,chooser=random.choice):
    choices = [sample for sample in samples if sample.id != previous]
    return chooser(choices or samples) if samples else None


def voice_payload(sample,nonce,reference_id=None,channel_id=None):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,25}',str(nonce)):
        raise ValueError('Invalid voice nonce')
    payload = {'flags':VOICE_FLAG,'nonce':str(nonce),'enforce_nonce':True,
               'allowed_mentions':{'parse':[],'users':[],'roles':[],'replied_user':False},
               'attachments':[{'id':0,'filename':'haddock-' + sample.id + '.ogg',
                               'duration_secs':sample.duration_secs,'waveform':sample.waveform}]}
    if reference_id is not None:
        if not re.fullmatch(r'\d{15,22}',str(reference_id)) or not re.fullmatch(r'\d{15,22}',str(channel_id)):
            raise ValueError('Invalid voice reply reference')
        payload['message_reference'] = {'message_id':str(reference_id),'channel_id':str(channel_id),'fail_if_not_exists':False}
    return payload


def multipart_body(sample,payload,boundary=None):
    boundary = boundary or 'cours-watch-' + secrets.token_hex(18)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',boundary):
        raise ValueError('Invalid multipart boundary')
    blob = sample.path.read_bytes()
    if len(blob) > 512 * 1024 or hashlib.sha256(blob).hexdigest() != sample.sha256:
        raise ValueError('Voice asset changed')
    filename = 'haddock-' + sample.id + '.ogg'
    parts = [('--' + boundary + '\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode(),
             json.dumps(payload,ensure_ascii=False).encode(),b'\r\n',
             ('--' + boundary + '\r\nContent-Disposition: form-data; name="files[0]"; filename="' + filename + '"\r\nContent-Type: audio/ogg\r\n\r\n').encode(),
             blob,('\r\n--' + boundary + '--\r\n').encode()]
    return b''.join(parts),'multipart/form-data; boundary=' + boundary


def send_voice_message(channel_id,token,sample,nonce,reference_id=None):
    if not re.fullmatch(r'\d{15,22}',str(channel_id)) or not token:
        raise ValueError('Voice destination unavailable')
    payload = voice_payload(sample,nonce,reference_id,channel_id)
    body,content_type = multipart_body(sample,payload)
    for attempt in range(3):
        request = urllib.request.Request('https://discord.com/api/v10/channels/' + str(channel_id) + '/messages',
            data=body,method='POST',headers={'Authorization':'Bot ' + token,
                'User-Agent':'UPBCourseWatch/1.0','Content-Type':content_type})
        try:
            with urllib.request.urlopen(request,timeout=25) as response:
                receipt = json.load(response)
            if not receipt.get('flags',0) & VOICE_FLAG or len(receipt.get('attachments',[])) != 1:
                raise VoiceMessageError('Discord did not confirm a native voice message')
            return receipt
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 2:
                delay = max(1,float(json.loads(error.read()).get('retry_after',5)))
                if delay <= 25:
                    time.sleep(delay)
                    continue
            raise VoiceMessageError('Discord voice HTTP ' + str(error.code)) from None
        except urllib.error.URLError:
            raise VoiceMessageError('Discord voice unavailable') from None
    raise VoiceMessageError('Discord voice rate limit')
