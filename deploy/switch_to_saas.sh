#!/usr/bin/env bash
# One-time switch of the live server (Hostinger VPS) from the single-company version to SaaS mode:
#   assertivehub.in             -> public website + owner panel
#   <yourcompany>.assertivehub.in -> your company's workspace (same logins)
# Needs the backup + export made earlier in /root/ah-backup (data.json, settings.py.server).
# Safe to run again if it stops half-way. Undo with: bash deploy/rollback_saas.sh
set -euo pipefail

APP=/var/www/assertivehub
DATA=/var/lib/assertivehub
ENVF=/etc/assertivehub.env
BK=/root/ah-backup
DOMAIN=assertivehub.in
PY="$APP/venv/bin/python"
NGX=/etc/nginx/sites-available/assertivehub

say()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m   OK\033[0m %s\n' "$*"; }
stop() { printf '\n\033[1;31mSTOPPED:\033[0m %s\nNothing after this point was changed. Your site is still running.\n' "$*"; exit 1; }

# ---------- checks (change nothing) ----------
say "Checking before we start"
[ "$(id -u)" = 0 ] || stop "Run this as root."
[ -d "$APP/.git" ] || stop "App folder $APP not found."
[ -s "$BK/data.json" ] || stop "Export $BK/data.json is missing. Run the backup step first."
[ -s "$BK/settings.py.server" ] || stop "Backup $BK/settings.py.server is missing. Run the backup step first."
[ -s "$APP/data/secret.key" ] || stop "$APP/data/secret.key is missing."
grep -q "\*\.$DOMAIN" "$NGX" || stop "Nginx does not list *.$DOMAIN in server_name ($NGX). Send this message to Claude."
ok "backups, secret key and Nginx look right"

# ---------- three questions ----------
say "Three quick questions"
while true; do
  read -r -p "1) Web address for your company (the part before .$DOMAIN), e.g. assertiveinnovations: " SLUG
  SLUG=$(echo "$SLUG" | tr 'A-Z' 'a-z' | tr -d ' ')
  if [[ "$SLUG" =~ ^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$ ]] && [[ "$SLUG" != *--* ]] && \
     ! [[ " www app api admin owner mail smtp ftp static billing support help blog status login signup demo test staging dev platform assertive hub " == *" $SLUG "* ]]; then break; fi
  echo "   Use 3-30 lowercase letters, numbers or hyphens (not www, demo, hub, assertive, admin, login...). Try again."
done
while true; do
  read -r -p "2) E-mail for website leads and owner notices: " EMAIL
  [[ "$EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] && break
  echo "   That does not look like an e-mail address. Try again."
done
while true; do
  read -r -s -p "3) Choose an OWNER PANEL password (12+ characters, letters/numbers/symbols, no quotes, \\ \$ \` or spaces): " PW; echo
  if [ ${#PW} -lt 12 ] || [[ "$PW" == *[\'\"\\\$\`\ ]* ]]; then echo "   Not allowed. Try again."; continue; fi
  read -r -s -p "   Type it again: " PW2; echo
  [ "$PW" = "$PW2" ] && break
  echo "   They did not match. Try again."
done
echo
echo "   Company address : https://$SLUG.$DOMAIN"
echo "   Lead e-mails to : $EMAIL"
read -r -p "Start the switch now? The site will be offline for about a minute. Type yes: " GO
[ "$GO" = "yes" ] || stop "You did not type yes."

# ---------- 1. new code ----------
say "1/6 Getting the new code from GitHub"
cd "$APP"
git config --global --add safe.directory "$APP" || true
git checkout -- config/settings.py          # your hand-edited copy is saved in $BK/settings.py.server
git fetch -q origin
git checkout -q main
git pull -q --ff-only origin main
[ -f saas/site.py ] || stop "The new website code is not on GitHub main yet. Merge the website pull request first, then run this again."
ok "code is at $(git log --oneline -1)"
"$APP/venv/bin/pip" install -q -r requirements.txt
ok "packages installed"

# ---------- 2. company database from the export ----------
say "2/6 Building your company database from the export"
mkdir -p "$DATA" && chmod 700 "$DATA"
cp -n "$APP/data/secret.key" "$DATA/secret.key"
if [ ! -f "$DATA/hub.sqlite3" ]; then
  env -u HUB_SAAS HUB_DATA_DIR="$DATA" "$PY" manage.py migrate auth -v0
  env -u HUB_SAAS HUB_DATA_DIR="$DATA" "$PY" manage.py migrate core 0001 -v0
  env -u HUB_SAAS HUB_DATA_DIR="$DATA" "$PY" manage.py loaddata "$BK/data.json" -v0
  env -u HUB_SAAS HUB_DATA_DIR="$DATA" "$PY" manage.py migrate -v0
  ok "built $DATA/hub.sqlite3"
else
  ok "already built earlier, keeping it"
fi

# ---------- 3. settings file ----------
say "3/6 Writing the settings file $ENVF"
umask 077
cat > "$ENVF" <<EOF
HUB_SAAS=1
HUB_BASE_DOMAIN=$DOMAIN
HUB_DATA_DIR=$DATA
DJANGO_HTTPS=1
HUB_TRUST_PROXY=1
HUB_OWNER_EMAIL=$EMAIL
HUB_CONTACT_EMAIL=$EMAIL
HUB_OWNER_PASSWORD='$PW'
EOF
umask 022
ok "settings saved (only root can read it)"

# ---------- 4. SaaS platform + move the company ----------
say "4/6 Setting up the platform and moving your company to $SLUG.$DOMAIN"
set -a; . "$ENVF"; set +a
"$PY" manage.py migrate -v0
if [ ! -d "$DATA/tenants/$SLUG" ]; then
  "$PY" manage.py adopt_company --slug "$SLUG" --name "Assertive Innovations" --email "$EMAIL" --plan pro --paid-until 2099-12-31
else
  ok "company folder already exists, keeping it"
fi
"$PY" manage.py collectstatic --noinput -v0
ok "platform ready"

# ---------- 5. services ----------
say "5/6 Services: web app, background worker, daily reminders"
mkdir -p /etc/systemd/system/gunicorn.service.d
printf '[Service]\nEnvironmentFile=%s\n' "$ENVF" > /etc/systemd/system/gunicorn.service.d/assertivehub.conf
cat > /etc/systemd/system/hub-worker.service <<EOF
[Unit]
Description=Assertive Hub background worker (reminders, WhatsApp/SMS, calendar sync)
After=network.target

[Service]
User=root
WorkingDirectory=$APP
EnvironmentFile=$ENVF
ExecStart=$PY manage.py hub_worker
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/cron.d/assertivehub <<EOF
0 9 * * * root cd $APP && set -a && . $ENVF && $PY manage.py saas_cron >/dev/null 2>&1
EOF
systemctl daemon-reload
systemctl restart gunicorn
systemctl enable -q --now hub-worker
ok "services restarted"

# ---------- 6. check ----------
say "6/6 Checking"
sleep 4
code() { curl -s -o /dev/null -w '%{http_code}' -H "Host: $1" "http://127.0.0.1:8000$2"; }
W=$(code "$DOMAIN" /); P=$(code "$DOMAIN" /pricing); C=$(code "$SLUG.$DOMAIN" /)
echo "   Website home     : $W"; echo "   Pricing page     : $P"; echo "   Company app      : $C"
echo "   Worker           : $(systemctl is-active hub-worker)"
if [ "$W" = 200 ] && [ "$P" = 200 ] && [ "$C" = 200 ]; then
  printf '\n\033[1;32mDONE.\033[0m\n'
  echo "   Website      : https://$DOMAIN"
  echo "   Your company : https://$SLUG.$DOMAIN   (same username and password as before)"
  echo "   Owner panel  : https://$DOMAIN/owner/   (the password you just chose)"
else
  printf '\n\033[1;31mSomething is not answering.\033[0m Send this screen to Claude. To go back to the old site: bash deploy/rollback_saas.sh\n'
  journalctl -u gunicorn -n 30 --no-pager | tail -30
fi
