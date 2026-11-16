"""Try the public Linktree in an ordinary browser; no credentials or bypasses."""
import asyncio
import json
from pathlib import Path

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True,chromium_sandbox=True)
        try:
            page=await browser.new_page()
            response=await page.goto('https://linktr.ee/BucharestEvents',wait_until='domcontentloaded',timeout=45000)
            await page.wait_for_timeout(1000)
            data=await page.locator('script#__NEXT_DATA__').text_content() if await page.locator('script#__NEXT_DATA__').count() else None
            print(json.dumps({'status':response.status if response else None,'title':await page.title(),'next_data':bool(data)}),flush=True)
            if data:
                target=Path('/var/lib/cours-watch/esn-linktree-probe.html')
                target.write_text(await page.content(),encoding='utf-8'); target.chmod(0o600)
                props=json.loads(data).get('props',{}).get('pageProps',{})
                print(json.dumps({'keys':list(props),'links':props.get('links')},ensure_ascii=False))
        finally: await browser.close()

if __name__=='__main__': asyncio.run(main())
