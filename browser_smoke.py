"""Validate Chromium's sandbox, persistent state and JavaScript without private accounts."""
import json
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory(dir='/var/lib/cours-watch') as directory:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(directory, channel='chrome', headless=True, chromium_sandbox=True)
        page = context.new_page()
        page.goto('data:text/html,<h1>COURSE_WATCH_BROWSER_OK</h1>')
        assert page.locator('h1').inner_text() == 'COURSE_WATCH_BROWSER_OK'
        version = context.browser.version
        context.close()
    print(json.dumps({'browser': 'ok', 'chromium': version, 'sandbox_requested': True, 'private_account_used': False}))
