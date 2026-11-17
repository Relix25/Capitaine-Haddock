"""Collector CLI, dedicated sessions and independently reported pipeline stages."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request

from courswatch.models import Item, Result, stamp, clean, digest, canonical_forum_url
from courswatch.storage.state import State
from courswatch.discord_app.transport import discord_request, load_discord_secrets
from courswatch.discord_app.delivery import format_notification, notify as _deliver
from courswatch.collectors.courses import page_status, moodle_pdf_items, moodle, click_optional, teams


def notify(state, configuration, secrets):
    """Compatibility facade retaining the historical injectable HTTP transport."""
    return _deliver(state, configuration, secrets, request=discord_request)


def moodle_cookies(data_dir):
    path = data_dir / 'moodle-cookies.json'
    if not path.exists():
        return []
    cookies = json.loads(path.read_text())
    if not isinstance(cookies, list) or any(c.get('domain', '').lstrip('.') != 'curs.upb.ro' for c in cookies):
        raise ValueError('État de session Moodle invalide')
    return cookies


def save_moodle_cookies(data_dir, cookies):
    # Only the dedicated VPS session is saved, never the local browser profile.
    selected = [c for c in cookies if c.get('domain', '').lstrip('.') == 'curs.upb.ro']
    if not any(c.get('name', '').lower().startswith('moodlesession') for c in selected):
        return
    path = data_dir / 'moodle-cookies.json'
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(selected))
    pending.chmod(0o600)
    os.replace(pending, path)


async def _scan_browser(config, data_dir, dry_run):
    from playwright.async_api import async_playwright
    results = []
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(data_dir/'browser-profile'), channel='chrome', headless=True, chromium_sandbox=True,
            locale='fr-FR', timezone_id='Europe/Bucharest', accept_downloads=False,
            viewport={'width': 1440, 'height': 1000})
        try:
            # Login tabs can be restored by a persistent Chrome profile. Release them
            # before scanning so Teams does not run twice within the memory budget.
            # Keep one blank tab: closing Chrome's last tab can end its cookie session.
            anchor = await context.new_page()
            for existing in list(context.pages):
                if existing != anchor:
                    await existing.close()
            for course in config['courses']:
                for collect in (moodle, teams):
                    if collect is moodle:
                        saved_cookies = moodle_cookies(data_dir)
                        if saved_cookies:
                            await context.clear_cookies(domain=re.compile(r'^\.?curs\.upb\.ro$'))
                            await context.add_cookies(saved_cookies)
                    if collect is moodle:
                        export=config.get('obsidian_export',{})
                        store=Path(export.get('documents_dir','/var/lib/cours-watch-publish/documents')) if export.get('enabled') and not dry_run else None
                        result=await collect(context,course,config.get('timeout_ms',40000),pdf_store=store)
                    else:
                        result = await collect(context, course, config.get('timeout_ms', 40000))
                    results.append(result)
                    if collect is moodle and result.status == 'ok' and not dry_run:
                        save_moodle_cookies(data_dir, await context.cookies())
                    print(json.dumps({'progress': result.source, 'status': result.status,
                                      'items': len(result.items), 'checked_utc': stamp()}),
                          file=sys.stderr, flush=True)
        finally:
            await context.close()
    report = complete_scan(config, data_dir, results, dry_run=dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'ok' else 2


def complete_scan(config, data_dir, results, *, dry_run=False):
    """Finish independent stages and retain a useful report after a delivery failure."""
    report = {'checked_utc': stamp(), 'dry_run': dry_run,
              'sources': [{'source': x.source, 'course': x.course, 'status': x.status,
                           'items': len(x.items), 'detail': x.detail} for x in results],
              'changes': [], 'reminders': {'status': 'dry_run'},
              'delivery': {'status': 'dry_run'}, 'obsidian_export': {'status': 'dry_run'}}
    report['status'] = 'ok' if all(x.status == 'ok' for x in results) else 'partial'
    if dry_run:
        return report
    state = State(data_dir / 'state.db')
    try:
        for result in results:
            report['changes'].extend(state.ingest(result))
        from courswatch.reminders import enqueue_daily_reminders
        try:
            report['reminders'] = enqueue_daily_reminders(state, config)
        except Exception as error:
            report['reminders'] = {'status': 'error', 'error_type': type(error).__name__}
        try:
            secrets_path = Path(os.environ.get('COURS_WATCH_SECRETS', '/etc/cours-watch/secrets.json'))
            report['delivery'] = notify(state, config.get('discord', {}), load_discord_secrets(secrets_path))
        except Exception as error:
            report['delivery'] = {'status': 'error', 'error_type': type(error).__name__,
                                  'pending': len(state.pending())}
        from courswatch.publishing.capture import publish_snapshot
        try:
            report['obsidian_export'] = publish_snapshot(data_dir, config, report, results)
        except Exception as error:
            report['obsidian_export'] = {'status': 'error', 'error_type': type(error).__name__}
        if any(report[name].get('status') == 'error' for name in ('reminders', 'delivery', 'obsidian_export')):
            report['status'] = 'partial'
        reports = data_dir / 'reports'
        reports.mkdir(mode=0o700, exist_ok=True)
        pending = reports / 'latest.pending'
        pending.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.chmod(0o600)
        os.replace(pending, reports / 'latest.json')
        return report
    finally:
        state.db.close()


def _copy_scan_inputs(source, target):
    """Copy only this service's dedicated session into a private disposable profile."""
    profile = source / 'browser-profile'
    if (source / 'login.active').exists():
        raise RuntimeError('Close the dedicated login session before an isolated scan')
    if profile.exists():
        if profile.is_symlink():
            raise ValueError('Symlinked browser profile is not a safe simulation input')
        for entry in profile.rglob('*'):
            if entry.is_symlink() and not entry.name.startswith('Singleton'):
                raise ValueError('Symlink in browser profile is not a safe simulation input')
        shutil.copytree(profile, target / 'browser-profile',
                        ignore=shutil.ignore_patterns('Singleton*', 'LOCK', 'lockfile'))
    cookies = source / 'moodle-cookies.json'
    if cookies.exists():
        if cookies.is_symlink():
            raise ValueError('Symlinked cookie file is not a safe simulation input')
        shutil.copyfile(cookies, target / cookies.name)
        (target / cookies.name).chmod(0o600)


async def scan(config, data_dir, dry_run):
    if not dry_run:
        return await _scan_browser(config, data_dir, False)
    # Never launch Chrome against the real persistent profile for a simulation.
    with tempfile.TemporaryDirectory(prefix='cours-watch-simulation-') as directory:
        isolated = Path(directory)
        isolated.chmod(0o700)
        _copy_scan_inputs(Path(data_dir), isolated)
        return await _scan_browser(config, isolated, True)


async def keep_moodle_session(config, data_dir):
    """Read one protected page through HTTP; no browser and no course-change notification."""
    from playwright.async_api import async_playwright
    cookies = moodle_cookies(data_dir)
    status = 'auth_required'
    if cookies:
        async with async_playwright() as p:
            request = await p.request.new_context(storage_state={'cookies': cookies, 'origins': []})
            try:
                response = await request.get(config['courses'][0]['moodle_url'], timeout=45000)
                url = urllib.parse.urlsplit(response.url)
                html = await response.text()
                if (response.ok and url.hostname == 'curs.upb.ro' and '/course/view.php' in url.path
                        and re.search(r'<li\b[^>]*class=[\"\x27][^\"\x27]*\bactivity\b', html, re.I)):
                    status = 'ok'
                    save_moodle_cookies(data_dir, (await request.storage_state())['cookies'])
            except Exception:
                status = 'network_error'
            finally:
                await request.dispose()
    report = {'checked_utc': stamp(), 'status': status, 'purpose': 'moodle_session_keepalive'}
    (data_dir / 'reports').mkdir(mode=0o700, exist_ok=True)
    (data_dir / 'reports' / 'moodle-session.json').write_text(json.dumps(report))
    print(json.dumps(report))
    return 0 if status == 'ok' else 2


def import_cookies(path, data_dir):
    """Import only a user-provided cookie export; never scrape a desktop browser profile."""
    values = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    values = values.get('cookies', []) if isinstance(values, dict) else values
    if not isinstance(values, list) or not values:
        raise ValueError('Fichier de cookies vide ou invalide')
    allowed = ('curs.upb.ro', 'teams.cloud.microsoft', 'teams.microsoft.com',
               'login.microsoftonline.com', 'login.live.com', 'login.windows.net')
    cookies = []
    for item in values:
        domain = item.get('domain', '').lstrip('.').lower()
        if not any(domain == x or domain.endswith('.'+x) for x in allowed):
            raise ValueError('Cookie extérieur aux plateformes autorisées : import annulé')
        c = {key: item[key] for key in ('name', 'value', 'domain', 'path', 'httpOnly', 'secure') if key in item}
        c.setdefault('path', '/')
        expiry = item.get('expires', item.get('expirationDate', -1))
        c['expires'] = float(expiry) if expiry else -1
        same = item.get('sameSite', 'Lax')
        c['sameSite'] = {'no_restriction': 'None', 'unspecified': 'Lax', 'lax': 'Lax', 'strict': 'Strict'}.get(same, same)
        cookies.append(c)
    async def apply():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(str(data_dir/'browser-profile'), channel='chrome', headless=True, chromium_sandbox=True)
            try:
                await context.add_cookies(cookies)
                save_moodle_cookies(data_dir, cookies)
            finally:
                await context.close()
    asyncio.run(apply())
    print(json.dumps({'cookies_imported': len(cookies), 'values_logged': False}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['scan', 'login', 'import-cookies', 'status', 'keepalive', 'remind', 'brief'])
    parser.add_argument('--config', default=os.environ.get('COURS_WATCH_CONFIG', '/etc/cours-watch/config.json'))
    parser.add_argument('--data-dir', default=os.environ.get('COURS_WATCH_DATA', '/var/lib/cours-watch'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--cookie-file')
    args = parser.parse_args()
    os.umask(0o077)
    data_dir = Path(args.data_dir)
    if args.dry_run and args.command != 'scan':
        parser.error('--dry-run is supported only for scan')
    if not args.dry_run and args.command != 'status':
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # GUI login, daily scan and HTTP session refresh must not overwrite each other.
    profile_lock = None
    if args.command != 'status' and os.name == 'posix':
        import fcntl
        lock_path = data_dir / 'browser.lock'
        profile_lock = lock_path.open('rb' if args.dry_run else 'a') if lock_path.exists() or not args.dry_run else None
        try:
            if profile_lock is not None:
                fcntl.flock(profile_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status': 'busy', 'action': 'skipped_without_session_change'}))
            return 0
    if args.command == 'import-cookies':
        if not args.cookie_file:
            parser.error('--cookie-file requis')
        import_cookies(args.cookie_file, data_dir)
        return 0
    if args.command == 'status':
        report = data_dir/'reports/latest.json'
        print(report.read_text() if report.exists() else '{"status":"not_run"}')
        return 0
    if args.command == 'login':
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(str(data_dir/'browser-profile'), channel='chrome', headless=False,
                      chromium_sandbox=True, args=['--restore-last-session'], locale='fr-FR', timezone_id='Europe/Bucharest', viewport={'width': 1440, 'height': 1000})
            saved_cookies = moodle_cookies(data_dir)
            if saved_cookies:
                context.add_cookies(saved_cookies)
            page = context.new_page()
            with contextlib.suppress(Exception):
                page.goto('https://teams.cloud.microsoft/')
            moodle_page = context.new_page()
            with contextlib.suppress(Exception):
                moodle_page.goto('https://curs.upb.ro/2026/my/', wait_until='domcontentloaded', timeout=45000)
            while context.pages:
                with contextlib.suppress(Exception):
                    for current in context.pages:
                        url = urllib.parse.urlsplit(current.url)
                        if (url.hostname == 'curs.upb.ro' and ('/my/' in url.path or '/course/' in url.path)
                                and not current.locator('input[type="password"]').count()):
                            save_moodle_cookies(data_dir, context.cookies())
                            break
                    context.pages[0].wait_for_timeout(2000)
        return 0
    configuration = json.loads(Path(args.config).read_text())
    if args.command in ('remind', 'brief'):
        from courswatch.reminders import enqueue_daily_reminders
        state = State(data_dir / 'state.db')
        reminders = enqueue_daily_reminders(state, configuration, initial=args.command == 'brief')
        secret_path = Path(os.environ.get('COURS_WATCH_SECRETS', '/etc/cours-watch/secrets.json'))
        secrets = load_discord_secrets(secret_path)
        report = {'checked_utc': stamp(), 'reminders': reminders,
                  'delivery': notify(state, configuration.get('discord', {}), secrets)}
        (data_dir / 'reports').mkdir(mode=0o700, exist_ok=True)
        (data_dir / 'reports/latest-reminders.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'keepalive':
        return asyncio.run(keep_moodle_session(configuration, data_dir))
    return asyncio.run(scan(configuration, data_dir, args.dry_run))


def cli():
    try:
        return main()
    except (ValueError, RuntimeError) as error:
        print(json.dumps({'status': 'configuration_error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(cli())
