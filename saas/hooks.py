"""Small helpers the core API calls when HUB_SAAS=1."""
from . import plans, state, tenancy


def payload():
    i = state.current()
    return i


def block(a, post):
    """Return (code, message) when the request must be refused, else None."""
    i = state.current()
    if not i:
        return None
    if i['state'] == 'suspended' and a != 'status':
        return 403, 'This account is suspended. Please contact support.'
    if i['state'] == 'expired' and post and a not in ('login', 'logout', 'forgot', 'status'):
        return 402, 'Your subscription has ended. Renew from Settings > Plan and billing to continue.'
    return None


def collection_ok(c):
    i = state.current()
    if not i:
        return True
    m = plans.COLL_MODULE.get(c)
    return m is None or m in i['modules']


def seat_ok(active_count):
    i = state.current()
    return True if not i else active_count < i['emp']


def module_ok(name):
    i = state.current()
    return True if not i else name in i['modules']
