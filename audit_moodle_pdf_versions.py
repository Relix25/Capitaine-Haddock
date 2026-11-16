"""Compare current Moodle PDF bytes with the preserved PC baseline. Never submits any form."""
import argparse
import asyncio
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from cours_watch import moodle_cookies
from moodle_pdf_versions import archive_pdf,fetch_pdf,PdfFetchError

async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',required=True,type=Path)
    parser.add_argument('--store',default='/var/lib/cours-watch-publish/documents',type=Path)
    args=parser.parse_args()
    baseline=json.loads((args.baseline/'baseline.json').read_text())['documents']
    expected={(entry['course'],entry['key']):entry for entry in baseline}
    for entry in baseline:
        content=(args.baseline/entry['baseline_file']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=entry['sha256']: raise SystemExit('Baseline integrity failed')
        archive_pdf({'bytes':content,'sha256':entry['sha256'],'pages':entry['pages'],
            'filename':entry['filename'],'final_url':entry['source_url']},args.store,entry['course'],entry['key'],entry['title'],entry['source_url'])
    from playwright.async_api import async_playwright
    results=[]
    with closing(sqlite3.connect('file:/var/lib/cours-watch/state.db?mode=ro',uri=True)) as db:
        candidates=[(source.split(':')[1],json.loads(payload)) for source,payload in db.execute(
            "SELECT source,payload FROM items WHERE source LIKE 'moodle:%' AND key LIKE 'module-%' AND key NOT LIKE '%:%'")]
    async with async_playwright() as p:
        client=await p.request.new_context(storage_state={'cookies':moodle_cookies(Path('/var/lib/cours-watch')),'origins':[]})
        try:
            for course,item in candidates:
                if not any('/mod/'+kind+'/' in item['url'] for kind in ('resource','folder','assign')): continue
                entry=expected.get((course,item['key']))
                try:
                    pdf=await fetch_pdf(client,item['url'])
                    result=archive_pdf(pdf,args.store,course,item['key'],' '.join(item['title'].split()),item['url'])
                    record={'course':course,'key':item['key'],'filename':pdf['filename'],'source_url':item['url'],
                        'status':'verified','sha256':pdf['sha256'],'pages':pdf['pages'],
                        'baseline_sha256':entry['sha256'] if entry else None,'baseline_pages':entry['pages'] if entry else None,
                        'changed_from_pc':bool(entry and entry['sha256']!=pdf['sha256']),
                        'page_delta':pdf['pages']-entry['pages'] if entry else None,'relative_file':result['relative_file']}
                except PdfFetchError:
                    record={'course':course,'key':item['key'],'status':'unverified_expected_pdf' if entry else 'no_pdf_verified',
                            'source_url':item['url']}
                results.append(record)
                print(json.dumps(record,ensure_ascii=False),flush=True)
        finally: await client.dispose()
    report={'checked_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),'documents':results,
            'baseline_pdfs':len(baseline),'verified_pdfs':sum(x['status']=='verified' for x in results),
            'changed_pdfs':sum(x.get('changed_from_pc',False) for x in results),
            'unverified_expected_pdfs':sum(x['status']=='unverified_expected_pdf' for x in results)}
    path=args.store.parent/'pdf-audit.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)); path.chmod(0o640)
    print(json.dumps({key:value for key,value in report.items() if key!='documents'},ensure_ascii=False),flush=True)
    return 2 if report['unverified_expected_pdfs'] else 0

if __name__=='__main__': raise SystemExit(asyncio.run(main()))
