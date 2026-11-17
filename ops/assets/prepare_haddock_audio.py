"""Fetch five existing short public excerpts and prepare bounded Opus voice assets."""
from array import array
import base64
import hashlib
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import json
import math
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request


SAMPLES = [
    ('abordage','Abordage','/dessins-animes/tintin-et-le-lac-aux-requins/434864'),
    ('parachute','Parachute','/dessins-animes/tintin-et-le-lac-aux-requins/434394'),
    ('bachi_bouzouk','Bachi-bouzouk','/dessins-animes/les-aventures-de-tintin-1959/487685'),
    ('cafe','Café','/dessins-animes/les-aventures-de-tintin-1959/487281'),
    ('scolopendres','Scolopendres','/dessins-animes/les-aventures-de-tintin-1959/487636'),
]
ROOT = Path('/var/lib/cours-watch/audio-staging')
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


class AudioPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.audio = None

    def handle_starttag(self,tag,attrs):
        if tag == 'audio':
            self.audio = dict(attrs).get('src') or self.audio


def download(url,limit,referrer=None):
    headers = {'User-Agent':'UPBCourseWatch/1.0'}
    if referrer:
        headers['Referer'] = referrer
    request = urllib.request.Request(url,headers=headers)
    with OPENER.open(request,timeout=25) as response:
        if urllib.parse.urlsplit(response.url).hostname != 'zonesons.com':
            raise ValueError('Unexpected media host')
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Sample exceeds the download limit')
    return data


def main():
    import imageio_ffmpeg

    ROOT.mkdir(mode=0o700,exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    output = []
    for identity,label,path in SAMPLES:
        source = 'https://zonesons.com' + path
        html = download(source,500000).decode()
        if '/capitaine-haddock' not in html:
            raise ValueError('Haddock attribution absent')
        page = AudioPage()
        page.feed(html)
        if not page.audio:
            raise ValueError('No public audio link')
        # The site's public player decodes its audio src in JavaScript before loading it.
        # Follow that documented page transformation rather than requesting the encoded placeholder.
        encoded = urllib.parse.urlsplit(page.audio).path.lstrip('/')
        decoded_path = base64.b64decode(encoded + '=' * (-len(encoded) % 4),validate=True).decode()
        if not decoded_path.startswith('citations/') or '..' in decoded_path.split('/'):
            raise ValueError('Unexpected public media path')
        audio_url = 'https://zonesons.com/' + urllib.parse.quote(decoded_path,safe='/!()-._~')
        if urllib.parse.urlsplit(audio_url).hostname != 'zonesons.com':
            raise ValueError('Unexpected audio host')
        raw = download(audio_url,1024 * 1024,source)
        original = ROOT / (identity + '.mp3')
        original.write_bytes(raw)
        pcm = subprocess.run([ffmpeg,'-v','error','-i',str(original),'-t','12.1','-f','s16le',
            '-ac','1','-ar','48000','pipe:1'],capture_output=True,check=True,timeout=30).stdout
        duration = len(pcm) / (2 * 48000)
        if not 0.25 <= duration <= 12:
            raise ValueError('Excerpt is not short enough: ' + identity + ' ' + str(duration))
        target = ROOT / (identity + '.ogg')
        subprocess.run([ffmpeg,'-v','error','-y','-i',str(original),'-vn','-ac','1','-ar','48000',
            '-af','loudnorm=I=-18:TP=-2:LRA=7','-c:a','libopus','-b:a','32k',
            '-application','voip',str(target)],capture_output=True,check=True,timeout=30)
        decoded = subprocess.run([ffmpeg,'-v','error','-i',str(target),'-f','s16le','-ac','1',
            '-ar','48000','pipe:1'],capture_output=True,check=True,timeout=30).stdout
        values = array('h',decoded)
        count = min(256,max(1,math.ceil(len(values) / 4800)))
        wave = []
        for i in range(count):
            part = values[i * len(values) // count:(i + 1) * len(values) // count]
            rms = math.sqrt(sum(value * value for value in part) / max(1,len(part)))
            wave.append(min(255,round(rms / 32768 * 255)))
        blob = target.read_bytes()
        assert blob.startswith(b'OggS') and b'OpusHead' in blob[:128]
        record = {'id':identity,'label':label,'file':target.name,'duration_secs':round(len(values)/48000,4),
                  'waveform':base64.b64encode(bytes(wave)).decode(),'sha256':hashlib.sha256(blob).hexdigest(),
                  'source_url':source,'original_sha256':hashlib.sha256(raw).hexdigest(),
                  'format':'ogg/opus','channels':1,'sample_rate':48000}
        output.append(record)
        print(json.dumps({k:record[k] for k in ('id','duration_secs','source_url')},ensure_ascii=False),flush=True)
    (ROOT / 'manifest.json').write_text(json.dumps({'version':1,'samples':output},ensure_ascii=False,indent=2))
    print(json.dumps({'prepared_samples':len(output),'total_seconds':round(sum(s['duration_secs'] for s in output),2)}))


if __name__ == '__main__':
    main()
