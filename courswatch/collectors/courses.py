"""Read-only Moodle and Teams collection using the dedicated browser context."""
from __future__ import annotations
import asyncio
import contextlib
import hashlib
import re
import tempfile
import urllib.parse
from pathlib import Path
from courswatch.models import Item, Result, clean, digest, canonical_forum_url


async def page_status(page, expected_host):
    parsed = urllib.parse.urlparse(page.url)
    if parsed.hostname != expected_host or '/login/' in parsed.path:
        return 'auth_required'
    if await page.locator('input[type="password"]').count():
        return 'auth_required'
    return None



async def moodle_pdf_items(context,course,row,pdf_store=None,urls=None):
    from courswatch.documents.versions import fetch_pdf, archive_pdf
    items = []
    fetched = {}
    candidates = urls if urls is not None else [row['url']]
    temporary = (tempfile.TemporaryDirectory(prefix='cours-watch-pdf-batch-')
                 if pdf_store is not None else contextlib.nullcontext(None))
    with temporary as staging:
        if staging is not None:
            Path(staging).chmod(0o700)
        for index, url in enumerate(candidates):
            sensor_key = (row['key'] if urls is None else row['key'] + ':attachment-' + hashlib.sha256(url.encode()).hexdigest()[:12])
            try:
                pdf = await fetch_pdf(context.request, url, browser_context=context)
                metadata = {key: value for key, value in pdf.items() if key != 'bytes'}
                if staging is not None and 'bytes' in pdf:
                    staged = Path(staging) / (str(index) + '.pdf')
                    staged.write_bytes(pdf['bytes'])
                    staged.chmod(0o600)
                    metadata['_staged_pdf'] = staged
                fetched[sensor_key] = metadata
                items.append(Item(sensor_key + ':document', row['title'], 'SHA256 ' + pdf['sha256'], row['url']))
                del pdf
            except Exception:
                items.append(Item(sensor_key + ':document-status', row['title'],
                                  'Vérification du contenu du fichier indisponible', row['url']))
        if pdf_store is not None and fetched:
            try:
                warning = None
                if urls is None:
                    keys = {key: key for key in fetched}
                else:
                    from courswatch.documents.identities import attachment_archive_keys
                    keys, warning = attachment_archive_keys(pdf_store, course['id'], row['key'], fetched)
                for sensor_key, metadata in fetched.items():
                    pdf = {key: value for key, value in metadata.items() if key != '_staged_pdf'}
                    try:
                        if '_staged_pdf' in metadata:
                            pdf['bytes'] = metadata['_staged_pdf'].read_bytes()
                        archive_pdf(pdf, pdf_store, course['id'], keys[sensor_key], clean(row['title']), row['url'])
                        if warning:
                            items.append(Item(sensor_key + ':document-status', row['title'],
                                              'Empreinte PDF vérifiée ; correspondance avec le catalogue historique à vérifier', row['url']))
                    except (OSError, ValueError, RuntimeError):
                        items.append(Item(sensor_key + ':document-status', row['title'],
                                          'Empreinte PDF vérifiée, archivage des versions indisponible', row['url']))
                    finally:
                        pdf.pop('bytes', None)
            except (OSError, ValueError, RuntimeError):
                for sensor_key in fetched:
                    items.append(Item(sensor_key + ':document-status', row['title'],
                                      'Empreinte PDF vérifiée, archivage des versions indisponible', row['url']))
    return items



async def moodle(context, course, timeout,pdf_store=None):
    source = 'moodle:' + course['id']
    page = await context.new_page()
    try:
        response = await page.goto(course['moodle_url'], wait_until='domcontentloaded', timeout=timeout)
        if response and response.status >= 400:
            return Result(source, course['label'], 'network_error', [], f'Moodle HTTP {response.status}')
        status = await page_status(page, 'curs.upb.ro')
        if status:
            return Result(source, course['label'], status, [], 'Connexion Moodle à rétablir sur le VPS')
        # Read link/title and activity description. Do not hash completion buttons or personal grades.
        rows = await page.locator('li.activity').evaluate_all('''elements => elements.map(e => {
          const a=e.querySelector('a[href*="/mod/"]');
          if(!a) return null;
          const name=e.querySelector('.instancename,.activityname') || a;
          const description=e.querySelector('.description,.activity-description');
          return {key:e.id || a.href,title:name.textContent.trim(),
                  body:description ? description.innerText : '',url:a.href};
        }).filter(Boolean)''')
        if not rows:
            return Result(source, course['label'], 'selector_unverified', [], 'Page Moodle sans activités reconnues : contrôle humain nécessaire')
        items = [Item(**row) for row in rows]
        for row in rows:
            if urllib.parse.urlparse(row['url']).hostname != 'curs.upb.ro':
                continue
            if '/mod/resource/' in row['url']:
                items.extend(await moodle_pdf_items(context,course,row,pdf_store))
                continue
            if not any('/mod/'+kind+'/' in row['url'] for kind in ('assign','forum','folder')):
                continue
            detail = await context.new_page()
            try:
                await detail.goto(row['url'], wait_until='domcontentloaded', timeout=timeout)
                if await page_status(detail, 'curs.upb.ro'):
                    raise RuntimeError('auth_required')
                if '/mod/assign/' in row['url'] or '/mod/folder/' in row['url']:
                    if '/mod/assign/' in row['url']:
                        text = await detail.locator('#intro,.activity-dates,[data-region="activity-information"]').all_text_contents()
                        items.append(Item(row['key'] + ':dates', row['title'], clean(' '.join(text)), row['url']))
                    links=await detail.locator('a[href*="pluginfile.php"]').evaluate_all('els=>els.map(e=>e.href)')
                    urls=list(dict.fromkeys(url for url in links if urllib.parse.urlsplit(url).hostname=='curs.upb.ro'
                                            and urllib.parse.urlsplit(url).path.lower().endswith('.pdf')))
                    items.extend(await moodle_pdf_items(context,course,row,pdf_store,urls))
                else:
                    discussions = await detail.locator('a[href*="/mod/forum/discuss.php"]').evaluate_all(
                        'els=>els.map(e=>({url:e.href,title:e.innerText.trim()})).filter(e=>e.title)')
                    unique = {}
                    for discussion in discussions:
                        canonical = canonical_forum_url(discussion['url'])
                        if canonical:
                            unique.setdefault(canonical, {**discussion, 'url': canonical})
                    for discussion in list(unique.values())[:40]:
                        items.append(Item(discussion['url'], discussion['title'], '', discussion['url']))
                        await detail.goto(discussion['url'], wait_until='domcontentloaded', timeout=timeout)
                        if await page_status(detail, 'curs.upb.ro'):
                            raise RuntimeError('auth_required')
                        forum_posts = await detail.locator('article[data-content="forum-post"],article[id^="p"],.forumpost[id]').evaluate_all('''els=>els.map(e=>{
                          const body=e.querySelector('.post-content-container,.posting,.content');
                          return {key:e.id,title:(e.querySelector('h3,h4,h5,h6')||e).textContent.slice(0,180),
                                  body:(body||e).innerText};
                        })''')
                        if not forum_posts:
                            raise RuntimeError('forum_selector_unverified')
                        for post in forum_posts:
                            identity = post['key'] or digest([discussion['url'], post['body']])
                            items.append(Item('forum:'+identity, clean(post['title']), clean(post['body']),
                                              discussion['url']+('#'+post['key'] if post['key'] else '')))
            finally:
                await detail.close()
        return Result(source, course['label'], 'ok', items,
                      'Activités, dates, 40 discussions maximum par forum ; PDF accessibles via ressources, dossiers et pièces jointes, téléchargés et vérifiés par SHA256/pages jusqu’à 25 Mio ; indisponibilités signalées par fichier.')
    except Exception as error:
        kind = 'auth_required' if str(error) == 'auth_required' else 'network_error'
        return Result(source, course['label'], kind, [], 'Moodle inaccessible ou chargement incomplet')
    finally:
        await page.close()



def merge_teams_links(body, links):
    """Append link targets on real newlines, only when not already in the text."""
    extra = [x['text'] + ' ' + x['url'] for x in links if x['url'] not in body]
    if extra:
        body = body + '\n' + '\n'.join(extra)
    return body


async def click_optional(page, role, pattern):
    target = page.get_by_role(role, name=re.compile(pattern, re.I))
    if await target.count() == 1 and await target.is_visible():
        await target.click()
        return True
    return False



async def teams(context, course, timeout):
    source = 'teams:' + course['id']
    page = await context.new_page()
    try:
        await page.goto(course.get('teams_url') or 'https://teams.cloud.microsoft/', wait_until='domcontentloaded', timeout=timeout)
        try:
            await page.get_by_role('button', name=re.compile('Votre profil|Your profile', re.I)).wait_for(timeout=timeout)
        except Exception:
            return Result(source, course['label'], 'auth_required', [], 'Connexion Teams à rétablir sur le VPS')
        if not course.get('teams_url'):
            await click_optional(page, 'button', r'^Retour à toutes les équipes$|^Back to all teams$')
            tile = page.get_by_role('button', name=course['team_name'], exact=True)
            await tile.wait_for(timeout=timeout)
            await tile.click()
            await page.get_by_role('treeitem', name=re.compile(r'^Général$|^General$')).click()
        posts = page.locator('[id^="message-body-"]')
        await posts.first.wait_for(timeout=timeout)
        # Scroll within the actual message panel to reach the newest visible posts.
        for _ in range(4):
            await posts.last.evaluate('''el=>{
              let p=el.parentElement;
              while(p && p.scrollHeight<=p.clientHeight+5) p=p.parentElement;
              if(p) p.scrollTop=p.scrollHeight;
            }''')
            await page.wait_for_timeout(600)
        # Expand explicit reply-count and read-more controls, never the Reply composer.
        for pattern in [r'^voir plus$|^see more$', r'^\d+ réponses?$|^\d+ replies$']:
            controls = page.get_by_role('button', name=re.compile(pattern, re.I))
            for index in range(min(await controls.count(), 30)):
                candidate = controls.nth(index)
                with contextlib.suppress(Exception):
                    if await candidate.is_visible():
                        await candidate.click(timeout=2000)
        rows = await posts.evaluate_all('''elements=>elements.map(e=>{
          const heading=e.querySelector('h2,h3,[role="heading"]');
          const anchors=Array.from(e.querySelectorAll('a[href]')).map(a=>({text:a.innerText.trim(),url:a.href}));
          const clone=e.cloneNode(true);
          clone.querySelectorAll('button,time,[role="toolbar"]').forEach(x=>x.remove());
          return {key:e.id,title:heading ? heading.innerText : 'Publication',
             body:clone.innerText,links:anchors};
        })''')
        if not rows:
            return Result(source, course['label'], 'selector_unverified', [], 'Aucune publication Teams reconnue')
        items = []
        for row in rows:
            body = clean(row['body'])
            links = [x for x in row['links'] if urllib.parse.urlparse(x['url']).scheme == 'https']
            body = merge_teams_links(body, links)
            items.append(Item(row['key'], row['title'], body, course.get('teams_url') or 'https://teams.cloud.microsoft/'))
        return Result(source, course['label'], 'ok', items,
                      'Publications/réponses effectivement chargées dans Général ; fichiers non annoncés et autres canaux hors couverture.')
    except Exception:
        return Result(source, course['label'], 'selector_unverified', [], 'Navigation Teams à revalider ; aucune absence de nouveauté déduite')
    finally:
        await page.close()

