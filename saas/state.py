import datetime

from . import plans, tenancy


def today():
    return datetime.date.today()


def info(t):
    """What a company may do right now."""
    d = today()
    if t.status != 'active':
        st = 'suspended'
    elif t.paid_until and t.paid_until >= d:
        st = 'active'
    elif t.trial_ends >= d and not t.paid_until:
        st = 'trial'
    elif t.trial_ends >= d and t.paid_until and t.paid_until < d:
        st = 'expired'
    else:
        st = 'expired'
    eff = plans.TRIAL_PLAN if st == 'trial' else t.plan
    p = plans.PLANS.get(eff, plans.PLANS['starter'])
    end = t.paid_until if t.paid_until and st in ('active', 'expired') else t.trial_ends
    return {'state': st, 'plan': eff, 'planName': p['name'], 'paidPlan': t.plan, 'emp': p['emp'], 'modules': p['modules'],
            'trialEnds': t.trial_ends.isoformat(), 'paidUntil': t.paid_until.isoformat() if t.paid_until else '',
            'daysLeft': (end - d).days, 'company': t.name}


def current():
    t = tenancy.tenant()
    return info(t) if t else None
