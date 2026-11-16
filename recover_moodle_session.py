"""Recover only the dedicated VPS Moodle session using Chrome's normal session restore."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright
from cours_watch import moodle, save_moodle_cookies


async def main():
    root = Path('/var/lib/cours-watch')
    config = json.loads(Path('/etc/cours-watch/config.json').read_text())
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(root / 'browser-profile'), channel='chrome', headless=True,
            chromium_sandbox=True, args=['--restore-last-session'], locale='fr-FR',
            timezone_id='Europe/Bucharest', viewport={'width': 1440, 'height': 1000})
        try:
            result = await moodle(context, config['courses'][0], 40000)
            if result.status == 'ok':
                save_moodle_cookies(root, await context.cookies())
            print(json.dumps({'status': result.status, 'items': len(result.items),
                              'session_saved': (root / 'moodle-cookies.json').exists()}))
            return 0 if result.status == 'ok' else 2
        finally:
            await context.close()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
