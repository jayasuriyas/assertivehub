"""Move the existing single-company installation into SaaS mode as one company (its own subdomain).

Run ONCE, with HUB_SAAS=1 and the same HUB_DATA_DIR the single-company site used:

    HUB_SAAS=1 HUB_BASE_DOMAIN=assertivehub.in python manage.py adopt_company \
        --slug mycompany --name "My Company" --email me@example.com --plan pro --paid-until 2099-12-31

It COPIES DATA_DIR/hub.sqlite3, selfies/, files/ and backups/ into DATA_DIR/tenants/<slug>/ and registers the company.
The original files are left exactly as they were, so going back is just switching HUB_SAAS off again.
"""
import datetime
import shutil
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from saas import plans, tenancy
from saas.models import Tenant


class Command(BaseCommand):
    help = 'Copy the single-company data into SaaS mode as one company.'

    def add_arguments(self, p):
        p.add_argument('--slug', required=True, help='subdomain, e.g. acme for acme.%s' % settings.BASE_DOMAIN)
        p.add_argument('--name', required=True)
        p.add_argument('--email', required=True)
        p.add_argument('--owner', default='')
        p.add_argument('--phone', default='')
        p.add_argument('--state', default='')
        p.add_argument('--plan', default='pro', choices=list(plans.PLANS))
        p.add_argument('--paid-until', default='', help='YYYY-MM-DD; leave empty to start a normal trial')
        p.add_argument('--dry-run', action='store_true')

    def handle(self, *a, **o):
        if not settings.SAAS:
            raise CommandError('Run with HUB_SAAS=1.')
        slug = o['slug'].strip().lower()
        if not tenancy.valid_slug(slug):
            raise CommandError('"%s" cannot be used as a web address (3-30 letters/numbers/hyphens, not a reserved word).' % slug)
        if Tenant.objects.filter(slug=slug).exists():
            raise CommandError('A company with the address "%s" already exists.' % slug)
        src = Path(settings.DATA_DIR)
        db = src / 'hub.sqlite3'
        if not db.exists():
            raise CommandError('No single-company database at %s' % db)
        dst = tenancy.tenant_dir(slug)
        if dst.exists() and any(dst.iterdir()):
            raise CommandError('%s already exists and is not empty.' % dst)
        paid = None
        if o['paid_until']:
            try:
                paid = datetime.date.fromisoformat(o['paid_until'])
            except ValueError:
                raise CommandError('--paid-until must be YYYY-MM-DD')
        with sqlite3.connect('file:%s?mode=ro' % db, uri=True) as c:
            n_emp = c.execute("select count(*) from core_record where coll='employees'").fetchone()[0]
            n_users = c.execute('select count(*) from auth_user').fetchone()[0]
        self.stdout.write('Source: %s (%d employees, %d logins)' % (db, n_emp, n_users))
        self.stdout.write('Target: %s  ->  https://%s.%s' % (dst, slug, settings.BASE_DOMAIN))
        if o['dry_run']:
            self.stdout.write('Dry run: nothing copied.')
            return
        dst.mkdir(parents=True, exist_ok=True)
        # consistent copy of a live SQLite file (safe even if the old site is still running)
        with sqlite3.connect('file:%s?mode=ro' % db, uri=True) as s, sqlite3.connect(dst / 'hub.sqlite3') as d:
            s.backup(d)
        for sub in ('selfies', 'files', 'backups'):
            if (src / sub).is_dir():
                shutil.copytree(src / sub, dst / sub)
                self.stdout.write('Copied %s/' % sub)
        t = Tenant.objects.create(slug=slug, name=o['name'][:80], owner_name=(o['owner'] or o['name'])[:80], owner_email=o['email'][:120],
                                  owner_phone=o['phone'][:15], state=o['state'][:60], plan=o['plan'], paid_until=paid,
                                  trial_ends=datetime.date.today() + datetime.timedelta(days=plans.TRIAL_DAYS))
        try:
            tenancy.provision(slug)  # brings the copied database up to the latest migrations
        except Exception:
            t.delete()
            raise
        self.stdout.write(self.style.SUCCESS('Done. %s now lives at https://%s.%s - the same usernames and passwords work there.' % (t.name, slug, settings.BASE_DOMAIN)))
