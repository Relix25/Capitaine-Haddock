"""Read the SOS General shared-files tab; no uploads, forms or content changes."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


async def main():
    import fcntl

    lock=Path('/var/lib/cours-watch/browser.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    config=json.loads(Path('/etc/cours-watch/config.json').read_text())
    course=next(c for c in config['courses'] if c['id']=='SOS')
    from playwright.async_api import async_playwright
    result={'course':'SOS','checked_utc':datetime.now(timezone.utc).isoformat(),
            'area':'General / Shared','status':'navigation_unverified'}
    async with async_playwright() as p:
        context=await p.chromium.launch_persistent_context('/var/lib/cours-watch/browser-profile',
            channel='chrome',headless=True,chromium_sandbox=True,locale='fr-FR',
            timezone_id='Europe/Bucharest',accept_downloads=False,viewport={'width':1440,'height':1000})
        try:
            page=await context.new_page()
            for old in list(context.pages):
                if old!=page: await old.close()
            await page.goto('https://teams.cloud.microsoft/',wait_until='domcontentloaded',timeout=60000)
            await page.get_by_role('button',name=re.compile('Votre profil|Your profile',re.I)).wait_for(timeout=60000)
            back=page.get_by_role('button',name=re.compile(r'^Retour à toutes les équipes$|^Back to all teams$',re.I))
            if await back.count(): await back.first.click()
            tile=page.get_by_role('button',name=course['team_name'],exact=True)
            await tile.wait_for(timeout=40000)
            await tile.click()
            await page.get_by_role('treeitem',name=re.compile(r'^Général$|^General$')).click()
            await page.locator('[id^="message-body-"]').first.wait_for(timeout=40000)
            result['channels']=await page.get_by_role('treeitem').all_text_contents()
            result['tabs']=await page.get_by_role('tab').all_text_contents()
            shared=page.get_by_role('tab',name=re.compile(r'^Partagé$|^Shared$|^Fichiers$|^Files$',re.I))
            if not await shared.count():
                result['available_buttons']=await page.get_by_role('button').all_text_contents()
            else:
                await shared.first.click()
                await page.wait_for_timeout(15000)
                result['frames']=[]
                for frame in page.frames:
                    parsed=urlsplit(frame.url)
                    # Report only URL origin/path, never query, runtime scripts,
                    # authentication bootstrap data or other teams' file lists.
                    record={'host':parsed.hostname,'path':parsed.path,
                            'grids':await frame.get_by_role('grid').count()}
                    is_sos='03acsmctia2s1ssia' in parsed.path.lower()
                    record['sos_site_verified']=is_sos
                    if is_sos:
                        empty=frame.get_by_text(re.compile(r'Ce dossier est vide|This folder is empty|Aucun fichier|No files|Aucun élément|No items',re.I))
                        if await empty.count() and await empty.first.is_visible():
                            result['status']='empty'
                        rows=await frame.get_by_role('row').all_text_contents()
                        record['visible_rows']=[row[:500] for row in rows]
                        if len(rows)>1: result['status']='files_observed'
                    result['frames'].append(record)
        finally: await context.close()
    output=Path('/var/lib/cours-watch-publish/sos-shared-audit.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    output.chmod(0o640)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': asyncio.run(main())
