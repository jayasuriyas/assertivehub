"""Periodic work (one tick per minute per company) and the system health panel."""
import datetime
import logging

from django.db import transaction

from core import views as V
from core.models import CalendarEventLink, Job, NotificationDelivery, NotificationProvider, WebhookLog
from . import jobs
from .base import action, need, now, iso, module_on

log = logging.getLogger('hub.tick')


def _due(name, every):
    """True at most once per `every` (timedelta) per company, remembered in Config."""
    st = V.cget('v6tick', {}) or {}
    last = st.get(name)
    t = now()
    if last:
        try:
            if t - datetime.datetime.fromisoformat(last) < every:
                return False
        except ValueError:
            pass
    st[name] = t.isoformat()
    V.cset('v6tick', st)
    return True


def tick():
    """Everything that runs on a schedule. Safe to run as often as you like (all steps are idempotent)."""
    from . import load_all, worktime, notify, branches, calsync
    load_all()
    out = {}
    steps = [
        ('autostop', lambda: worktime.auto_stop_due()),
        ('overrun', lambda: [worktime.check_overrun(t) for t in set(worktime.WorkSession.objects.filter(status='running').values_list('task_rid', flat=True))]),
        ('transfers', lambda: branches._apply_due({}, None)),
        ('scheduled', lambda: notify.scheduled_checks()),
    ]
    for name, fn in steps:
        try:
            with transaction.atomic():
                out[name] = fn()
        except Exception as e:  # one failing step must not stop the others
            log.exception('tick step %s failed', name)
            out[name] = 'error: %s' % e
    if module_on('calsync') and _due('calpoll', datetime.timedelta(minutes=15)):
        try:
            out['calpoll'] = calsync.poll_rsvps()
        except Exception as e:
            log.exception('calendar poll failed')
            out['calpoll'] = 'error: %s' % e
    if _due('purge', datetime.timedelta(hours=24)):
        with transaction.atomic():
            notify.purge_old()
            Job.objects.filter(status__in=['done', 'cancelled'], created_at__lt=now() - datetime.timedelta(days=14)).delete()
    out['jobs'] = jobs.run_due()
    out['sent'] = notify.send_due()
    V.cset('v6lastTick', now().isoformat())
    return out


def each_company(fn):
    """Run fn() once for the single company, or once per company database in SaaS mode (skipping paused ones)."""
    from django.conf import settings
    if not settings.SAAS:
        return {'default': fn()}
    from saas import tenancy, state
    from saas.models import Tenant
    res = {}
    for t in Tenant.objects.all():
        try:
            if state.info(t)['state'] not in ('trial', 'active'):
                res[t.slug] = 'skipped (%s)' % state.info(t)['state']
                continue
            with tenancy.ctx(t.slug, t):
                res[t.slug] = fn()
        except Exception as e:
            log.exception('tick failed for %s', t.slug)
            res[t.slug] = 'error: %s' % e
    return res


@action('system.health', read=True)
def a_health(request, u, b):
    need(u, 'system.health')
    t = now()
    since = t - datetime.timedelta(hours=24)
    last = V.cget('v6lastTick', '')
    try:
        age = int((t - datetime.datetime.fromisoformat(last)).total_seconds()) if last else None
    except ValueError:
        age = None
    return {'worker': {'lastTick': last, 'secondsAgo': age, 'ok': age is not None and age < 180},
            'jobs': {'queued': Job.objects.filter(status='queued', run_at__lte=t).count(), 'failed24h': Job.objects.filter(status='failed', created_at__gte=since).count(),
                     'recentFailures': [{'kind': j.kind, 'error': j.last_error.strip().splitlines()[-1][:200] if j.last_error else '', 'at': iso(j.created_at)}
                                        for j in Job.objects.filter(status='failed').order_by('-id')[:5]]},
            'notifications': {'failed24h': NotificationDelivery.objects.filter(status__in=['failed', 'failed_perm'], queued_at__gte=since).count(),
                              'queued': NotificationDelivery.objects.filter(status='queued').exclude(channel='inapp').count(),
                              'providers': [{'channel': p.channel, 'provider': p.provider, 'enabled': p.enabled, 'status': p.status, 'error': p.last_error} for p in NotificationProvider.objects.all()],
                              'lastWebhook': iso(WebhookLog.objects.order_by('-received_at').values_list('received_at', flat=True).first())},
            'calendar': {'errors': CalendarEventLink.objects.filter(status__in=['error', 'conflict', 'deleted_externally']).count()}}
