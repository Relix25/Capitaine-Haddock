"""Inspect only status and cookie metadata of the dedicated VPS session, never cookie values."""
import asyncio
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from cours_watch import moodle_cookies


async def main():
    root = Path('/var/lib/cours-watch')
    config = json.loads(Path('/etc/cours-watch/config.json').read_text())
    saved = moodle_cookies(root)
    print(json.dumps({'cookie_metadata': [{k:c.get(k) for k in ('name','domain','path','expires','sameSite','secure')} for c in saved]}))
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(root / 'browser-profile'), channel='chrome', headless=True, chromium_sandbox=True,
            locale='fr-FR', timezone_id='Europe/Bucharest')
        try:
            anchor = await context.new_page()
            for page in list(context.pages):
                if page != anchor:
                    await page.close()
            for course in config['courses']:
                await context.clear_cookies(domain=re.compile(r'^\.?curs\.upb\.ro$'))
                await context.add_cookies(saved)
                page = await context.new_page()
                await page.goto(course['moodle_url'], wait_until='domcontentloaded', timeout=45000)
                parts = urlsplit(page.url)
                print(json.dumps({'course': course['id'], 'location': parts.netloc + parts.path,
                                  'password_fields': await page.locator('input[type="password"]').count(),
                                  'activities': await page.locator('li.activity').count()}))
                await page.close()
        finally:
            await context.close()


if __name__ == '__main__':
    asyncio.run(main())
