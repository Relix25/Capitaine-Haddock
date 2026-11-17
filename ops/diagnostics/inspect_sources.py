"""Read-only diagnostics of the dedicated course-monitor browser; never export session state."""
import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


async def inspect(platform):
    config = json.loads(Path('/etc/cours-watch/config.json').read_text())
    root = Path('/var/lib/cours-watch')
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(root / 'browser-profile'), channel='chrome', headless=True,
            chromium_sandbox=True, locale='fr-FR', timezone_id='Europe/Bucharest',
            viewport={'width': 1440, 'height': 1000}, accept_downloads=False)
        try:
            for page in list(context.pages):
                await page.close()
            page = await context.new_page()
            target = config['courses'][0]['moodle_url'] if platform == 'moodle' else 'https://teams.cloud.microsoft/'
            await page.goto(target, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(10000)
            url = urlsplit(page.url)
            details = {'platform': platform, 'location': url.scheme + '://' + url.netloc + url.path,
                       'title': await page.title(),
                       'visible_text': (await page.locator('body').inner_text())[:9000],
                       'controls': await page.locator('button,[role="button"],[role="treeitem"],a').evaluate_all(
                           '''els=>els.filter(e=>e.getClientRects().length).slice(0,100).map(e=>({
                             role:e.getAttribute('role')||e.tagName.toLowerCase(),
                             name:e.getAttribute('aria-label')||e.innerText.slice(0,180)}))''')}
            output = root / 'reports' / (platform + '-diagnostic.json')
            output.write_text(json.dumps(details, ensure_ascii=False, indent=2))
            output.chmod(0o600)
            await page.screenshot(path=str(output.with_suffix('.png')))
            print(json.dumps(details, ensure_ascii=False, indent=2))
        finally:
            await context.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('platform', choices=['moodle', 'teams'])
    asyncio.run(inspect(parser.parse_args().platform))


if __name__ == '__main__':
    main()
