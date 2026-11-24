#!/usr/bin/env bash
# Undo deploy/switch_to_saas.sh: back to the old single-company site on PostgreSQL ("first deploy" code + your old settings).
# Nothing is deleted: the new files in /var/lib/assertivehub and /etc/assertivehub.env stay, so you can switch again later.
set -euo pipefail
APP=/var/www/assertivehub
BK=/root/ah-backup
[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
[ -s "$BK/settings.py.server" ] || { echo "Missing $BK/settings.py.server - cannot roll back automatically."; exit 1; }
cd "$APP"
systemctl disable -q --now hub-worker 2>/dev/null || true
rm -f /etc/systemd/system/gunicorn.service.d/assertivehub.conf /etc/cron.d/assertivehub
git checkout -q ccb05e3
cp "$BK/settings.py.server" config/settings.py
systemctl daemon-reload
systemctl restart gunicorn
sleep 3
echo "Old site answers: $(curl -s -o /dev/null -w '%{http_code}' -H 'Host: assertivehub.in' http://127.0.0.1:8000/)"
echo "Rolled back to the old version (code ccb05e3, PostgreSQL)."
