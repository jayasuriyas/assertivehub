"""Long-running worker: runs hub_tick every 20 seconds. Run it as a systemd service:

[Service]
WorkingDirectory=/srv/assertive-hub
ExecStart=/srv/assertive-hub/.venv/bin/python manage.py hub_worker
Restart=always
"""
import logging
import signal
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections


class Command(BaseCommand):
    help = 'Process v6 jobs continuously (reminders, WhatsApp/SMS sending, calendar sync, timer auto-stop).'

    def add_arguments(self, p):
        p.add_argument('--interval', type=int, default=20)

    def handle(self, *a, **k):
        from core.v6.system import each_company, tick
        stop = {'v': False}
        signal.signal(signal.SIGTERM, lambda *x: stop.update(v=True))
        self.stdout.write('hub_worker started')
        while not stop['v']:
            t = time.time()
            try:
                each_company(tick)
            except Exception:
                logging.getLogger('hub.worker').exception('tick failed')
            close_old_connections()
            time.sleep(max(1, k['interval'] - (time.time() - t)))
        self.stdout.write('hub_worker stopped')
