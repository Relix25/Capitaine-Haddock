#!/usr/bin/env bash
set -euo pipefail
umask 022
source_dir=${1:?source directory required}
install -o root -g root -m 0644 "$source_dir/cours_watch.py" "$source_dir/browser_smoke.py" "$source_dir/test_state.py" /opt/cours-watch/
install -o root -g root -m 0644 "$source_dir/course_reminders.py" "$source_dir/test_reminders.py" /opt/cours-watch/
install -o root -g root -m 0644 "$source_dir/discord_presentation.py" "$source_dir/test_discord_presentation.py" /opt/cours-watch/
install -o root -g root -m 0644 "$source_dir/course_health.py" "$source_dir/test_course_health.py" "$source_dir/preview_status.py" /opt/cours-watch/
install -o root -g root -m 0644 "$source_dir/haddock_emojis.py" "$source_dir/test_haddock_emojis.py" /opt/cours-watch/
install -o root -g root -m 0644 "$source_dir/course_translation.py" "$source_dir/translate_worker.py" "$source_dir/test_course_translation.py" /opt/cours-watch/
install -o root -g root -m 0644 "$source_dir/haddock_easter_egg.py" "$source_dir/test_haddock_easter_egg.py" /opt/cours-watch/
install -o root -g root -m 0644 "$source_dir/haddock_voice.py" "$source_dir/test_haddock_voice.py" /opt/cours-watch/
install -o root -g root -m 0644 "$source_dir/luna_brain.py" "$source_dir/luna_chat.py" "$source_dir/test_luna_brain.py" "$source_dir/test_luna_chat.py" /opt/cours-watch/
install -o root -g root -m 0755 "$source_dir/login-desktop.sh" /opt/cours-watch/login-desktop.sh
if [[ ! -f /etc/cours-watch/config.json ]]; then
  install -o root -g courswatch -m 0640 "$source_dir/config.json" /etc/cours-watch/config.json
fi
install -o root -g root -m 0644 "$source_dir/cours-watch.service" "$source_dir/cours-watch.timer" "$source_dir/cours-watch-login.service" "$source_dir/cours-watch-session.service" "$source_dir/cours-watch-session.timer" /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/cours-watch.service /etc/systemd/system/cours-watch.timer /etc/systemd/system/cours-watch-login.service
sudo -u courswatch env PLAYWRIGHT_BROWSERS_PATH=/var/lib/cours-watch/browsers /opt/cours-watch/venv/bin/python /opt/cours-watch/browser_smoke.py
cd /opt/cours-watch
sudo -u courswatch /opt/cours-watch/venv/bin/python -m unittest -v test_state.py test_reminders.py test_discord_presentation.py test_course_health.py test_haddock_emojis.py test_course_translation.py test_haddock_easter_egg.py test_haddock_voice.py
systemctl daemon-reload
systemctl enable --now cours-watch.timer cours-watch-session.timer
printf '%s\n' 'Installed. Activation gate remains closed until authentication and Discord delivery are validated.'
systemctl list-timers cours-watch.timer --no-pager
