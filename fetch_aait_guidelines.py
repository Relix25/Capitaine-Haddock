"""Download the newly listed assignment PDF with the authorized VPS Moodle session."""
import asyncio
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import pwd
import urllib.parse
from cours_watch import moodle_cookies

SOURCE='https://curs.upb.ro/2026/mod/assign/view.php?id=67666'

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='a' and values.get('href'): self.links.append(values['href'])

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        client = await p.request.new_context(storage_state={'cookies':moodle_cookies(Path('/var/lib/cours-watch')),'origins':[]})
        try:
            page=await client.get(SOURCE,timeout=45000)
            parser=Links(); parser.feed(await page.text())
            candidates=[]
            for link in parser.links:
                url=urllib.parse.urljoin(SOURCE,link)
                parsed=urllib.parse.urlsplit(url)
                filename=urllib.parse.unquote(parsed.path.rsplit('/',1)[-1])
                if parsed.hostname=='curs.upb.ro' and 'pluginfile.php' in parsed.path and filename=='AAIT Project Guidelines.pdf': candidates.append(url)
            if not candidates: raise SystemExit('Expected assignment PDF not found; no file written')
            response=await client.get(candidates[0],timeout=60000)
            content=await response.body()
            if not response.ok or not content.startswith(b'%PDF-') or len(content)>25*1024*1024:
                raise SystemExit('PDF validation failed; no file written')
            root=Path('/tmp/cours-new-files-20261112'); root.mkdir(mode=0o700,exist_ok=True)
            uid=pwd.getpwnam('ubuntu').pw_uid; os.chown(root,uid,-1)
            target=root/'AAIT Project Guidelines.pdf'
            target.write_bytes(content); target.chmod(0o600); os.chown(target,uid,-1)
            proof={'filename':target.name,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'source':SOURCE}
            metadata=root/'AAIT-guidelines-source.json'; metadata.write_text(json.dumps(proof,indent=2)); metadata.chmod(0o600); os.chown(metadata,uid,-1)
            print(json.dumps(proof),flush=True)
        finally: await client.dispose()

if __name__=='__main__': asyncio.run(main())
