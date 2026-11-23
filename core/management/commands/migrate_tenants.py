"""Apply database migrations to every company database (SaaS). Run after each release:
    python manage.py migrate                       (platform database)
    python manage.py migrate_tenants --backup      (every company)
"""
import shutil
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Migrate every company database. Copies each database file first with --backup.'

    def add_arguments(self, p):
        p.add_argument('--backup', action='store_true', help='Copy each hub.sqlite3 to hub.sqlite3.pre-<time> first')
        p.add_argument('--only', help='Migrate one company (slug)')
        p.add_argument('--dry-run', action='store_true', help='Show what would be migrated')

    def handle(self, *a, **k):
        if not settings.SAAS:
            raise CommandError('Not a SaaS install (HUB_SAAS is not 1). Use "python manage.py migrate".')
        from saas import tenancy
        from saas.models import Tenant
        q = Tenant.objects.all().order_by('slug')
        if k.get('only'):
            q = q.filter(slug=k['only'])
        ok, bad = [], []
        stamp = time.strftime('%Y%m%d-%H%M%S')
        for t in q:
            db = tenancy.tenant_dir(t.slug) / 'hub.sqlite3'
            if k['dry_run']:
                self.stdout.write('would migrate %s (%s)' % (t.slug, db))
                continue
            try:
                if k['backup'] and db.exists():
                    shutil.copy2(db, db.with_name('hub.sqlite3.pre-%s' % stamp))
                al = tenancy.register(t.slug)
                with tenancy.ctx(t.slug, t):
                    call_command('migrate', database=al, verbosity=0, interactive=False)
                ok.append(t.slug)
                self.stdout.write(self.style.SUCCESS('migrated %s' % t.slug))
            except Exception as e:  # keep going, report at the end
                bad.append((t.slug, str(e)))
                self.stdout.write(self.style.ERROR('FAILED %s: %s' % (t.slug, e)))
        self.stdout.write('done: %d migrated, %d failed' % (len(ok), len(bad)))
        if bad:
            raise CommandError('Some companies failed: ' + ', '.join(s for s, _ in bad))
