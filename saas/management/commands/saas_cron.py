import datetime

from django.core.management.base import BaseCommand

from saas import mail, plans, state
from saas.models import Tenant


class Command(BaseCommand):
    help = 'Run once a day: emails owners about trials and plans ending. Add to cron.'

    def handle(self, *a, **k):
        n = 0
        for t in Tenant.objects.filter(status='active'):
            i = state.info(t)
            key = None
            if i['state'] in ('trial', 'active') and i['daysLeft'] in (3, 1):
                key = '%s-%s-%d' % (i['state'], i['trialEnds'] if i['state'] == 'trial' else i['paidUntil'], i['daysLeft'])
                subj = 'Your %s %s ends in %d day%s' % (plans.PRODUCT, 'trial' if i['state'] == 'trial' else 'plan', i['daysLeft'], '' if i['daysLeft'] == 1 else 's')
            elif i['state'] == 'expired' and i['daysLeft'] in (-1, -7):
                key = 'expired-%d-%s' % (i['daysLeft'], i['paidUntil'] or i['trialEnds'])
                subj = 'Your %s workspace is read-only until you renew' % plans.PRODUCT
            if key and t.last_notice != key:
                body = 'Hi %s,\n\n%s.\nRenew any time from Settings > Plan and billing inside your workspace.\n' % (t.owner_name, subj)
                if mail.send(t.owner_email, subj, body):
                    t.last_notice = key
                    t.save(update_fields=['last_notice'])
                    n += 1
        self.stdout.write('sent %d notices' % n)
