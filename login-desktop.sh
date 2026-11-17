#!/usr/bin/env bash
set -euo pipefail
umask 077
export DISPLAY=:91
export PLAYWRIGHT_BROWSERS_PATH=/var/lib/cours-watch/browsers
touch /var/lib/cours-watch/login.active
cleanup() {
  kill "${web_pid:-}" "${vnc_pid:-}" "${wm_pid:-}" "${x_pid:-}" 2>/dev/null || true
  rm -f /var/lib/cours-watch/login.active
}
trap cleanup EXIT
if [[ ! -f /var/lib/cours-watch/vnc-password.txt ]]; then
  /opt/cours-watch/venv/bin/python -c 'import secrets,string; from pathlib import Path; p=Path("/var/lib/cours-watch/vnc-password.txt"); p.write_text("".join(secrets.choice(string.ascii_letters+string.digits) for _ in range(8))+"\n"); p.chmod(0o600)'
fi
Xvfb :91 -screen 0 1440x1000x24 -nolisten tcp &
x_pid=$!
sleep 1
openbox --display :91 >/dev/null 2>&1 &
wm_pid=$!
x11vnc -display :91 -localhost -rfbport 5901 -forever -shared -passwdfile /var/lib/cours-watch/vnc-password.txt >/dev/null 2>&1 &
vnc_pid=$!
websockify --web=/usr/share/novnc/ 127.0.0.1:6081 127.0.0.1:5901 >/dev/null 2>&1 &
web_pid=$!
/opt/cours-watch/venv/bin/python -m courswatch.runtime login
