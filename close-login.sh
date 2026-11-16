#!/usr/bin/env bash
set -euo pipefail
systemctl stop cours-watch-login.service
rm -f -- /var/lib/cours-watch/login.active
python3 - <<'PY'
from pathlib import Path
site=Path('/etc/nginx/sites-available/opencode-private')
line='    include /etc/nginx/snippets/cours-watch-private.conf;\n'
text=site.read_text()
site.write_text(text.replace(line,''))
PY
nginx -t
systemctl reload nginx
rm -f /etc/nginx/snippets/cours-watch-private.conf /etc/nginx/cours-watch.htpasswd /etc/cours-watch/login-http-password.txt
printf '%s\n' 'Login interface closed; saved platform sessions preserved in their private profile.'
