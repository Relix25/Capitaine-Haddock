"""Diagnose a Moodle resource using the dedicated VPS session; print no credentials."""
import asyncio
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from courswatch.runtime import moodle_cookies


async def probe(resource_id):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        request = await p.request.new_context(storage_state={'cookies': moodle_cookies(Path('/var/lib/cours-watch')), 'origins': []})
        try:
            for method in ('HEAD', 'GET'):
                try:
                    response = await request.fetch('https://curs.upb.ro/2026/mod/resource/view.php?id=' + str(resource_id), method=method, timeout=60000)
                    url = urlsplit(response.url)
                    info = {'method': method, 'status': response.status, 'location': url.netloc + url.path,
                            'headers': {k:v for k,v in response.headers.items() if k in ('content-type','content-length','last-modified','etag')}}
                    if method == 'GET':
                        body = await response.body()
                        info.update(size=len(body), is_pdf=body.startswith(b'%PDF'), sha256=hashlib.sha256(body).hexdigest())
                    print(json.dumps(info))
                except Exception as error:
                    print(json.dumps({'method': method, 'error_type': type(error).__name__}))
        finally:
            await request.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('resource_id', type=int, choices=[20524,44618], nargs='?', default=20524)
    resource_id = parser.parse_args().resource_id
    asyncio.run(probe(resource_id))


if __name__ == '__main__':
    main()
