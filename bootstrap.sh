#!/usr/bin/env bash
set -euo pipefail
umask 022
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv ca-certificates xvfb xauth x11vnc novnc websockify openbox
if ! id courswatch >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/cours-watch --create-home --shell /usr/sbin/nologin courswatch
fi
install -d -o root -g root -m 0755 /opt/cours-watch
install -d -o root -g courswatch -m 0750 /etc/cours-watch
install -d -o courswatch -g courswatch -m 0700 /var/lib/cours-watch
python3 -m venv /opt/cours-watch/venv
/opt/cours-watch/venv/bin/pip install --disable-pip-version-check playwright
/opt/cours-watch/venv/bin/python -m playwright install-deps chromium
/opt/cours-watch/venv/bin/python -m playwright install chrome
/opt/cours-watch/venv/bin/pip freeze > /opt/cours-watch/requirements.lock
chmod 0644 /opt/cours-watch/requirements.lock
printf '%s\n' 'Runtime installed; no session or Discord secret has been imported.'
