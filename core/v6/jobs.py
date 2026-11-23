"""A small DB-backed job queue, one per company database. Run by `manage.py hub_tick` (cron, every minute) or
`manage.py hub_worker` (long-running). Business code calls enqueue() inside its own transaction (outbox pattern),
so a job exists only if the change it belongs to was committed. No external call ever runs inside a web request.
"""
import datetime
import logging
import traceback

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import Job

log = logging.getLogger('hub.jobs')
HANDLERS = {}
BACKOFF = [60, 300, 900, 3600, 3 * 3600, 6 * 3600]


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(kind, payload=None, run_at=None, unique_key=None, max_attempts=5):
    run_at = run_at or timezone.now()
    if unique_key:
        ex = Job.objects.filter(unique_key=unique_key).first()
        if ex:
            if ex.status in ('queued',) and ex.run_at != run_at:
                ex.run_at = run_at
                ex.payload = payload or {}
                ex.save(update_fields=['run_at', 'payload'])
            return ex
        try:
            with transaction.atomic():
                return Job.objects.create(kind=kind, payload=payload or {}, run_at=run_at, unique_key=unique_key, max_attempts=max_attempts)
        except IntegrityError:
            return Job.objects.filter(unique_key=unique_key).first()
    return Job.objects.create(kind=kind, payload=payload or {}, run_at=run_at, max_attempts=max_attempts)


def cancel(unique_key_prefix):
    return Job.objects.filter(unique_key__startswith=unique_key_prefix, status='queued').update(status='cancelled')


def run_due(limit=100):
    """Claim and run due jobs. Returns (done, failed)."""
    from core import v6
    v6.load_all()
    now = timezone.now()
    Job.objects.filter(status='running', locked_until__lt=now).update(status='queued')  # crashed worker
    ids = list(Job.objects.filter(status='queued', run_at__lte=now).order_by('run_at', 'id').values_list('id', flat=True)[:limit])
    done = failed = 0
    for jid in ids:
        claimed = Job.objects.filter(id=jid, status='queued').update(status='running', locked_until=now + datetime.timedelta(minutes=5))
        if not claimed:
            continue
        j = Job.objects.get(id=jid)
        fn = HANDLERS.get(j.kind)
        try:
            if not fn:
                raise RuntimeError('No handler for job kind %s' % j.kind)
            with transaction.atomic():
                fn(j.payload or {}, j)
            Job.objects.filter(id=jid).update(status='done', locked_until=None, attempts=j.attempts + 1)
            done += 1
        except Exception as e:  # retry with back-off
            a = j.attempts + 1
            tb = traceback.format_exc()[-2000:]
            log.warning('job %s %s failed: %s', jid, j.kind, e)
            if a >= j.max_attempts:
                Job.objects.filter(id=jid).update(status='failed', attempts=a, last_error=tb, locked_until=None)
                failed += 1
            else:
                Job.objects.filter(id=jid).update(status='queued', attempts=a, last_error=tb, locked_until=None,
                                                  run_at=timezone.now() + datetime.timedelta(seconds=BACKOFF[min(a - 1, len(BACKOFF) - 1)]))
    return done, failed
