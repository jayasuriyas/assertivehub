"""Run all scheduled v6 work once: reminders, auto-stop of forgotten timers, transfers, retries, sending.
Add to cron:   * * * * *  cd /path/to/app && .venv/bin/python manage.py hub_tick
(or run `manage.py hub_worker` as a service instead)."""
import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run scheduled v6 work once for every company (safe to run every minute).'

    def handle(self, *a, **k):
        from core.v6.system import each_company, tick
        res = each_company(tick)
        self.stdout.write(json.dumps(res, default=str))
