"""Shared plumbing for v6 actions: errors, capabilities, module switches, time helpers, dispatch."""
import datetime
import re
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from core import views as V  # partially initialised while views imports us; only used at call time

V6_MODULES = ('branches', 'notify', 'meetings', 'timetrack', 'calsync')

# Role -> capabilities (docs/release-v6/03-SRS.md section 6). Scope narrows the data further.
ALL_ADMIN = {'superadmin', 'admin'}
SCOPED_ADMIN = {'hradmin', 'branchadmin'}
CAPS = {
    'branch.manage': {'superadmin'},
    'branch.view': ALL_ADMIN | SCOPED_ADMIN | {'branchmgr'},
    'branch.admins': {'superadmin'},
    'emp.edit': ALL_ADMIN | SCOPED_ADMIN,
    'emp.transfer': ALL_ADMIN | {'hradmin'},
    'org.master': ALL_ADMIN,
    'room.manage': ALL_ADMIN | SCOPED_ADMIN | {'branchmgr'},
    'notify.config': {'superadmin'},
    'notify.templates': ALL_ADMIN,
    'notify.templates.view': ALL_ADMIN | SCOPED_ADMIN,
    'notify.send': ALL_ADMIN | SCOPED_ADMIN | {'branchmgr'},
    'notify.bulk': ALL_ADMIN | SCOPED_ADMIN,
    'notify.history': ALL_ADMIN | SCOPED_ADMIN,
    'meeting.view_scope': ALL_ADMIN | SCOPED_ADMIN | {'branchmgr', 'manager'},
    'meeting.manage_scope': ALL_ADMIN | SCOPED_ADMIN | {'branchmgr'},
    'time.view_scope': ALL_ADMIN | SCOPED_ADMIN | {'branchmgr', 'manager'},
    'time.approve': ALL_ADMIN | {'branchadmin', 'branchmgr', 'manager'},
    'time.policy': ALL_ADMIN | {'branchadmin'},
    'time.void': ALL_ADMIN,
    'report.consolidated': ALL_ADMIN | {'hradmin'},
    'system.health': {'superadmin'},
}


class V6Error(V.ApiError):
    def __init__(self, code, msg, http=400, details=None):
        super().__init__(msg, http)
        self.err = code
        self.details = details or {}


def err(code, msg, http=None, **details):
    raise V6Error(code, msg, http or HTTP.get(code, 400), details)


HTTP = {'VALIDATION': 400, 'FORBIDDEN': 403, 'MODULE_OFF': 403, 'NOT_FOUND': 404, 'PLAN_LIMIT': 402, 'RATE_LIMITED': 429,
        'PROVIDER_ERROR': 502, 'SERVER_CONFIG': 500}
for _c in ('INVALID_STATE', 'TIMER_ACTIVE', 'TASK_DONE', 'ON_LEAVE', 'NOT_CHECKED_IN', 'OVERLAP', 'CONFLICT_ROOM', 'IMMUTABLE', 'LOCKED',
           'BRANCH_IN_USE', 'TRANSFER_EXISTS', 'TEMPLATE_NOT_APPROVED', 'CAP_REACHED', 'CONSENT_REQUIRED', 'CONFLICTS'):
    HTTP[_c] = 409


def invalid(**fields):
    first = next(iter(fields.values())) if fields else 'Please check the form.'
    err('VALIDATION', first, fields=fields)


# ---------- roles, modules ----------
def role(u):
    return V.role_of(u)


def can(u, cap):
    return role(u) in CAPS.get(cap, set())


def need(u, cap):
    if not can(u, cap):
        err('FORBIDDEN', 'You do not have permission for this.')


def settings_obj():
    s = V.cget('settings', {})
    return s if isinstance(s, dict) else {}


def module_on(mid):
    if settings.SAAS:
        from saas import hooks
        if not hooks.module_ok(mid):
            return False
    return bool((settings_obj().get('modules') or {}).get(mid))


def need_module(mid):
    if mid and not module_on(mid):
        err('MODULE_OFF', 'This feature is switched off. A super admin can switch it on under Settings > Modules.')


# ---------- time ----------
def now():
    return timezone.now()


def iso(dt):
    return dt.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if dt else None


def tz(name):
    try:
        return ZoneInfo(name or settings.TIME_ZONE)
    except Exception:
        return ZoneInfo(settings.TIME_ZONE)


def local(dt, tzname):
    return dt.astimezone(tz(tzname))


def parse_dt(s, tzname=None):
    """'2026-12-03T15:00' (local to tzname) or ISO with Z/offset -> aware UTC datetime."""
    if not isinstance(s, str) or not s:
        return None
    s = s.strip().replace('Z', '+00:00')
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz(tzname))
    return d.astimezone(datetime.timezone.utc)


def parse_date(s):
    if isinstance(s, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        try:
            return datetime.date.fromisoformat(s)
        except ValueError:
            return None
    return None


def hm_to_min(s):
    m = re.match(r'^(\d{1,2}):(\d{2})$', str(s or ''))
    if not m or int(m.group(2)) > 59:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def text(b, k, mx, req=False, mn=0, label=None):
    v = b.get(k)
    v = '' if v is None else str(v).strip()
    if req and not v:
        invalid(**{k: '%s is required.' % (label or k.capitalize())})
    if v and len(v) < mn:
        invalid(**{k: '%s must be at least %d characters.' % (label or k.capitalize(), mn)})
    if len(v) > mx:
        invalid(**{k: '%s can be at most %d characters.' % (label or k.capitalize(), mx)})
    return v


def int_in(b, k, lo, hi, default=None, label=None):
    v = b.get(k, default)
    if v in (None, ''):
        return default
    try:
        v = int(v)
    except (TypeError, ValueError):
        invalid(**{k: '%s must be a number.' % (label or k)})
    if v < lo or v > hi:
        invalid(**{k: '%s must be between %d and %d.' % (label or k, lo, hi)})
    return v


def page_args(b):
    try:
        p = max(1, int(b.get('page') or 1))
        s = min(200, max(1, int(b.get('size') or 50)))
    except (TypeError, ValueError):
        p, s = 1, 50
    return p, s


def paged(qs_or_list, b, fn):
    p, s = page_args(b)
    if isinstance(qs_or_list, list):
        total = len(qs_or_list)
        items = qs_or_list[(p - 1) * s:p * s]
    else:
        total = qs_or_list.count()
        items = list(qs_or_list[(p - 1) * s:p * s])
    return {'items': [fn(x) for x in items], 'page': p, 'size': s, 'total': total}


# ---------- employees (Record JSON) ----------
def emp_rec(eid):
    from core.models import Record
    if not eid:
        return None
    r = Record.objects.filter(coll='employees', rid=str(eid)).first()
    return r.data if r else None


def emp_map():
    from core.models import Record
    return {r.rid: r.data for r in Record.objects.filter(coll='employees')}


def emp_name(eid, cache=None):
    e = (cache or {}).get(eid) if cache is not None else emp_rec(eid)
    return (e or {}).get('name', '') if e else ''


def user_for_emp(eid):
    from core.models import Profile
    p = Profile.objects.filter(emp_id=eid, user__is_active=True).select_related('user').first()
    return p.user if p else None


def bump_rev():
    V.cset('rev', V.rev_get() + 1)


# ---------- dispatch ----------
ACTIONS = {}  # name -> (fn, module, read)


def action(name, module=None, read=False):
    def deco(fn):
        ACTIONS[name] = (fn, module, read)
        return fn
    return deco


def is_read(a):
    from . import load_all
    load_all()
    x = ACTIONS.get(a)
    return bool(x and x[2])


def dispatch(request, a, b):
    from . import load_all
    load_all()
    x = ACTIONS.get(a)
    if not x:
        V.fail('Unknown action.', 404)
    fn, mod, read = x
    if not read and request.method != 'POST':
        V.fail('POST required.', 405)
    u = V.need_auth(request)
    need_module(mod)
    if request.method == 'GET':
        b = {k: (request.GET.getlist(k) if k.endswith('[]') else request.GET.get(k)) for k in request.GET if k != 'a'}
        b = {k.rstrip('[]'): v for k, v in b.items()}
    out = fn(request, u, b) or {}
    if isinstance(out, dict):
        out.setdefault('ok', True)
        out['now'] = iso(now())
    return out
