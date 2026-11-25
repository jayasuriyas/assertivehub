#!/usr/bin/env bash
# Put the latest code from GitHub (main) live on the server. Run on the server:  bash deploy/update.sh
# Backs up every database first; stops on any error (the running site keeps working until the restart at the end).
set -euo pipefail
APP=/var/www/assertivehub
ENVF=/etc/assertivehub.env
PY="$APP/venv/bin/python"
[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
[ -s "$ENVF" ] || { echo "Settings file $ENVF missing - run deploy/switch_to_saas.sh first."; exit 1; }
cd "$APP"
set -a; . "$ENVF"; set +a
STAMP=$(date +%Y%m%d-%H%M%S)
echo "== 1/5 Backup of all databases -> /root/ah-backup/update-$STAMP.tgz"
mkdir -p /root/ah-backup
tar czf "/root/ah-backup/update-$STAMP.tgz" -C "$HUB_DATA_DIR" --exclude='backups' --exclude='*/backups' .
PREV=$(git rev-parse --short HEAD)
echo "== 2/5 Getting the new code (now at $PREV)"
git config --global --add safe.directory "$APP" >/dev/null 2>&1 || true
git fetch -q origin && git checkout -q main && git pull -q --ff-only origin main
echo "   now at $(git log --oneline -1)"
echo "== 3/5 Packages and database updates"
"$APP/venv/bin/pip" install -q -r requirements.txt
"$PY" manage.py migrate -v0
"$PY" manage.py migrate_tenants
"$PY" manage.py collectstatic --noinput -v0
echo "== 4/5 Restarting"
systemctl restart gunicorn
systemctl restart hub-worker 2>/dev/null || true
sleep 4
echo "== 5/5 Checking"
W=$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HUB_BASE_DOMAIN" http://127.0.0.1:8000/)
echo "   Website: $W   Worker: $(systemctl is-active hub-worker 2>/dev/null || echo off)"
if [ "$W" = 200 ]; then echo "DONE. Live at https://$HUB_BASE_DOMAIN (previous version was $PREV)."
else echo "PROBLEM: website answered $W. Send this screen to Claude. To go back: git checkout $PREV && systemctl restart gunicorn"; journalctl -u gunicorn -n 25 --no-pager; fi
