"""Assertive Hub API for Django. Same JSON actions as the PHP version, so the screens are unchanged."""
import json
import re
import time
import datetime
from pathlib import Path

from django.conf import settings
from django.contrib.auth import authenticate, login as dj_login, logout as dj_logout
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

try:
    from saas import hooks as saas_hooks, tenancy as saas_tenancy
except Exception:  # pragma: no cover
    saas_hooks = saas_tenancy = None
from .models import Profile, Record, Attendance, Config, LoginFail

COLS = ['employees', 'tasks', 'clients', 'testimonials', 'events', 'notes', 'reviews', 'agenda', 'leaves', 'ratings', 'attreqs', 'notices', 'documents', 'assets', 'candidates', 'invoices', 'payslips', 'checklists', 'rota']
# optional modules: w = lowest role rank that may change records, r = lowest rank that sees every record, own = others see only their own (eid)
GEN = {
    'notices': (2, 1, False), 'documents': (3, 3, True), 'assets': (3, 3, True), 'candidates': (3, 3, False),
    'invoices': (2, 2, False), 'payslips': (3, 3, True), 'checklists': (3, 3, True), 'rota': (2, 2, True),
}
THEME_IDS = ['indigo', 'emerald', 'sunset', 'rose', 'ocean', 'graphite']
RANK = {'superadmin': 4, 'admin': 3, 'hradmin': 3, 'branchadmin': 3, 'branchmgr': 2, 'manager': 2, 'employee': 1}
MGR_ROLES = ('manager', 'branchmgr')
APP_HTML = Path(__file__).resolve().parent / 'app' / 'index.html'


class ApiError(Exception):
    def __init__(self, msg, code=400):
        self.msg, self.code = msg, code


def fail(msg, code=400):
    raise ApiError(msg, code)


# ---------- people ----------
def prof(u):
    p = Profile.objects.filter(user=u).first()
    if p is None:
        p = Profile.objects.create(user=u, role='superadmin' if u.is_superuser else 'employee')
    elif u.is_superuser and p.role != 'superadmin':
        p.role = 'superadmin'  # a Django superuser (made with createsuperuser) is always super admin here
        p.emp_id = ''
        p.save()
    return p


def role_of(u):
    return prof(u).role


def rank_of(u):
    return RANK.get(role_of(u), 1)


def me_view(u):
    p = prof(u)
    return {'id': str(u.pk), 'user': u.username, 'role': p.role, 'empId': p.emp_id, 'prefs': p.prefs or {}}


def user_pub(u):
    p = prof(u)
    from .models import UserBranchScope
    return {'id': str(u.pk), 'user': u.username, 'role': p.role, 'empId': p.emp_id, 'active': u.is_active,
            'created': u.date_joined.strftime('%Y-%m-%d'),
            'branches': [str(x) for x in UserBranchScope.objects.filter(user=u).values_list('branch_id', flat=True)]}


def timezone_local(dt):
    from django.utils import timezone as _tz
    return _tz.localtime(dt).strftime('%Y-%m-%d %H:%M:%S')


def _short(d):
    if not isinstance(d, dict):
        return ''
    return ', '.join('%s=%s' % (k, str(v)[:30]) for k, v in list(d.items())[:4])


def active_supers(exclude_pk=None, count_deleted=False):
    q = User.objects.filter(is_active=True, profile__role='superadmin')
    if exclude_pk is not None:
        q = q.exclude(pk=exclude_pk)
    return q.count()


def revoke_sessions(pk):
    for s in Session.objects.all():
        try:
            if s.get_decoded().get('_auth_user_id') == str(pk):
                s.delete()
        except Exception:
            s.delete()


def need_auth(request):
    u = request.user
    if not u.is_authenticated or not u.is_active:
        fail('Not signed in.', 401)
    return u


def need_role(request, r):
    u = need_auth(request)
    if rank_of(u) < r:
        fail('You do not have permission for this.', 403)
    return u


# ---------- data ----------
def cget(key, default):
    c = Config.objects.filter(key=key).first()
    return c.value if c else default


def cset(key, value):
    Config.objects.update_or_create(key=key, defaults={'value': value})


def rev_get():
    return int(cget('rev', 0))


def full_db():
    db = {c: [] for c in COLS}
    for r in Record.objects.all():
        if r.coll in db:
            db[r.coll].append(r.data)
    att = {}
    for a in Attendance.objects.all():
        att.setdefault(a.day, {})[a.eid] = a.data
    db['attendance'] = att
    db['company'] = cget('company', 'My Company')
    db['settings'] = cget('settings', {})
    db['v'] = 2
    return db


def team_of(me_emp):
    t = {}
    if me_emp:
        for r in Record.objects.filter(coll='employees'):
            if r.data.get('reportsTo') == me_emp:
                t[r.rid] = True
    return t


def view_for(u):
    db = full_db()
    rank = rank_of(u)
    me = prof(u).emp_id
    uid = str(u.pk)
    db['peers'] = [{'id': e.get('id'), 'name': e.get('name', ''), 'designation': e.get('designation', ''), 'dept': e.get('dept', ''), 'empId': e.get('empId', ''), 'branchId': e.get('branchId', '')} for e in db['employees'] if e.get('status', 'Active') == 'Active' and not e.get('core') and e.get('id')]
    db['notes'] = [n for n in db['notes'] if str(n.get('uid', first_super_id())) == uid]
    for gc, (gw, gr, gown) in GEN.items():
        if rank < gr:
            db[gc] = [r for r in db[gc] if me and r.get('eid') == me] if gown else []
    if rank < 3:
        db['payslips'] = [p for p in db['payslips'] if p.get('status') == 'Paid']  # drafts stay private to admins
        for e in db['employees']:
            if e.get('id') != me:
                e.pop('ctc', None)
    db['caps'] = {'punch': True}
    if settings.SAAS:
        db['plan'] = saas_hooks.payload()
        for gc in GEN:
            if not saas_hooks.collection_ok(gc):
                db[gc] = []
    sc = _scope(u)
    if rank >= 3 and sc.all:
        return _v6_extend(db, u, sc)
    if rank >= 3:  # HR admin / branch admin: only their branches (enforced here, not in the screens)
        emps, prim = sc.emps, sc.primary
        db['employees'] = [e for e in db['employees'] if e.get('id') in emps]
        if role_of(u) == 'branchadmin':
            for e in db['employees']:
                if e.get('id') != me:
                    e.pop('ctc', None)
        db['tasks'] = [t for t in db['tasks'] if t.get('eid') in emps]
        for k in ('reviews', 'leaves', 'attreqs'):
            db[k] = [r for r in db[k] if r.get('eid') in prim]
        db['ratings'] = [r for r in db['ratings'] if r.get('from') in prim or r.get('to') in prim]
        for gc, (gw, gr, gown) in GEN.items():
            if gown:
                db[gc] = [r for r in db[gc] if r.get('eid') in prim]
        db['events'] = [x for x in db['events'] if not x.get('branchIds') or set(map(str, x['branchIds'])) & sc.branches]
        db['notices'] = [x for x in db['notices'] if not (x.get('audience') or {}).get('branches') or set(map(str, x['audience']['branches'])) & sc.branches]
        db['attendance'] = {d: {e: r for e, r in m.items() if e in prim} for d, m in db['attendance'].items() if any(e in prim for e in m)}
        return _v6_extend(db, u, sc)
    if isinstance(db.get('settings'), dict):
        db['settings'] = {k: v for k, v in db['settings'].items() if k not in ('devices', 'lastBackup')}
    if rank == 2:
        scope = {k: True for k in sc.emps}
        if me:
            scope[me] = True
        prim = set(sc.primary) | ({me} if me else set())
    else:
        scope = {me: True} if me else {}
        prim = set(scope)
    db['employees'] = [e for e in db['employees'] if e.get('id') in scope]
    db['tasks'] = [t for t in db['tasks'] if t.get('eid') in scope]
    db['reviews'] = [r for r in db['reviews'] if r.get('eid') in prim]
    db['leaves'] = [r for r in db['leaves'] if r.get('eid') in prim]
    db['attreqs'] = [r for r in db['attreqs'] if r.get('eid') in prim]
    mb = _scope_own_branches(me)
    db['events'] = [x for x in db['events'] if not x.get('branchIds') or set(map(str, x['branchIds'])) & (mb | sc.branches)]
    db['notices'] = [x for x in db['notices'] if not (x.get('audience') or {}).get('branches') or set(map(str, x['audience']['branches'])) & (mb | sc.branches)]
    db['ratings'] = [r for r in db['ratings'] if r.get('from') == me or r.get('to') in scope] if rank == 2 else [r for r in db['ratings'] if r.get('from') == me]
    if rank == 2 and (db.get('settings') or {}).get('ratingsAnon'):
        db['ratings'] = [dict(r, **{'from': '', 'id': 'rt_anon_%d_%s' % (i, r.get('month', ''))}) if r.get('from') != me else r for i, r in enumerate(db['ratings'])]
    if rank == 1:
        cids = {t.get('cid') for t in db['tasks'] if t.get('cid')}
        db['clients'] = [{'id': c['id'], 'name': c.get('name', '')} for c in db['clients'] if c.get('id') in cids]
        db['testimonials'] = []
        db['agenda'] = []
    att = {}
    for d, m in db['attendance'].items():
        row = {e: r for e, r in m.items() if e in prim}
        if row:
            att[d] = row
    db['attendance'] = att
    return _v6_extend(db, u, sc)


def _scope(u):
    from .v6.scope import scope_for
    return scope_for(u)


def _scope_own_branches(me):
    from .v6.scope import own_branches
    return own_branches(me)


def _v6_extend(db, u, sc):
    """Small read-only v6 data for the screens. Heavy v6 data has its own endpoints (never in `load`)."""
    from .v6 import base as v6base, branches as v6b, worktime as v6w
    db['branches'] = v6b.load_payload(u)
    db['myScope'] = {'all': sc.all, 'branches': sorted(sc.branches), 'me': sc.me}
    db['v6'] = {m: v6base.module_on(m) for m in v6base.V6_MODULES}
    db['v6caps'] = sorted(k for k in v6base.CAPS if v6base.can(u, k))
    db['taskTime'] = v6w.load_payload([t.get('id') for t in db['tasks'] if t.get('id')]) if db['v6'].get('timetrack') else {}
    return db


def first_super_id():
    u = User.objects.filter(profile__role='superadmin').order_by('pk').first()
    return str(u.pk) if u else ''


def can_set(c, new, old, u, scope, team):
    if c in GEN:
        if c == 'rota' and rank_of(u) == 2 and new.get('eid') not in scope:
            return None
        return new if rank_of(u) >= GEN[c][0] else None
    role = role_of(u)
    admin = RANK[role] >= 3
    me = prof(u).emp_id
    sc = _scope(u)
    if admin and not sc.all and not _admin_scope_ok(c, new, old, sc):
        return None
    if c == 'employees':
        if admin:
            if role == 'branchadmin' and old is not None:
                new['ctc'] = old.get('ctc', 0)
            return new
        if role in ('employee', 'manager') and old and me and old.get('id') == me:
            f = dict(old)
            for k in ('phone', 'photo', 'blood', 'education', 'skills', 'experience', 'expYears'):
                if k in new:
                    f[k] = new[k]
            return f
        return None
    if c == 'tasks':
        if admin:
            return new
        if role in MGR_ROLES:
            if new.get('eid') not in scope:
                return None
            if old and old.get('eid') not in scope:
                return None
            return new
        if role == 'employee':
            if not old or not me or old.get('eid') != me or old.get('status') == 'done':
                return None
            st = new.get('status')
            if st not in ('todo', 'doing', 'done'):
                return None
            f = dict(old)
            f['status'] = st
            if st == 'done':
                f['doneOn'] = datetime.date.today().isoformat()
            return f
        return None
    if c == 'reviews':
        if admin:
            return new
        if role in MGR_ROLES and new.get('eid') in team and (not old or old.get('eid') in team):
            return new
        return None
    if c == 'ratings':
        import re as _re
        frm, to, mo = str(new.get('from', '')), str(new.get('to', '')), str(new.get('month', ''))
        if not me or frm != me or not to or to == me or not _re.match(r'^\d{4}-\d{2}$', mo):
            return None
        today = datetime.date.today()
        last = (today.replace(day=1) - datetime.timedelta(days=1)).strftime('%Y-%m')
        if mo not in (today.strftime('%Y-%m'), last):
            return None
        if new.get('id') != 'rt_%s_%s_%s' % (frm, to, mo):
            return None
        try:
            r = int(new.get('rating', 0))
        except (TypeError, ValueError):
            return None
        tr = Record.objects.filter(coll='employees', rid=to).first()
        if r < 1 or r > 5 or not tr or (tr.data or {}).get('core'):
            return None
        if old and old.get('from') != me:
            return None
        return {'id': new['id'], 'from': frm, 'to': to, 'month': mo, 'rating': r, 'remarks': str(new.get('remarks', ''))[:500], 'created': (old or {}).get('created', today.isoformat())}
    if c == 'attreqs':
        import re as _re
        eid, st_new = str(new.get('eid', '')), new.get('status')
        if not _re.match(r'^\d{4}-\d{2}-\d{2}$', str(new.get('date', ''))):
            return None
        if me and eid == me:
            if old and old.get('eid') != me:
                return None
            f = dict(new)
            f['note'] = str(new.get('note', ''))[:300]
            f['text'] = str(new.get('text', ''))[:500]
            if not old:
                if st_new != 'Pending':
                    return None
                f.update(by='', decidedOn='', reply='')
                return f
            if old.get('status') != 'Pending' or st_new not in ('Pending', 'Cancelled'):
                return None
            for k in ('by', 'decidedOn', 'reply'):
                f[k] = old.get(k, '')
            return f
        if not admin or not old or old.get('status') != 'Pending' or st_new not in ('Resolved', 'Rejected'):
            return None
        f = dict(old)
        f.update(status=st_new, by=u.username, decidedOn=datetime.date.today().isoformat(), reply=str(new.get('reply', ''))[:300])
        return f
    if c == 'leaves':
        eid, st_new = str(new.get('eid', '')), new.get('status')
        if me and eid == me:  # your own request: create, change while pending, or cancel; never approve
            if old and old.get('eid') != me:
                return None
            f = dict(new)
            if not old:
                if st_new != 'Pending':
                    return None
                f.update(by='', decidedOn='', note='')
                return f
            if old.get('status') != 'Pending' or st_new not in ('Pending', 'Cancelled'):
                return None
            for k in ('by', 'decidedOn', 'note'):
                f[k] = old.get(k, '')
            return f
        if not old or old.get('status') != 'Pending' or old.get('eid') != eid or st_new not in ('Approved', 'Rejected'):
            return None
        if not (admin or (role in MGR_ROLES and eid in team)):
            return None
        f = dict(old)
        f.update(status=st_new, by=u.username, decidedOn=datetime.date.today().isoformat(), note=str(new.get('note', ''))[:300])
        return f
    if c in ('clients', 'testimonials', 'agenda'):
        return new if (admin or role in MGR_ROLES) else None
    if c == 'events':
        if admin:
            return new
        if role in MGR_ROLES and new.get('type') != 'holiday' and (old or {}).get('type') != 'holiday':
            return new
        return None
    if c == 'notes':
        if old and str(old.get('uid', first_super_id())) != str(u.pk):
            return None
        new['uid'] = str(u.pk)
        return new
    return None


def _admin_scope_ok(c, new, old, sc):
    """HR admins and branch admins may only touch records of employees in their branches."""
    if c == 'employees':
        return old is None or old.get('id') in sc.primary
    if c == 'tasks':
        return (new or {}).get('eid') in sc.emps and (old is None or old.get('eid') in sc.emps)
    if c in ('reviews', 'leaves', 'attreqs') or (c in GEN and GEN[c][2]):
        return (new or old or {}).get('eid') in sc.primary and (old is None or old.get('eid') in sc.primary)
    if c == 'events':
        bs = [str(x) for x in ((new or {}).get('branchIds') or [])]
        ob = [str(x) for x in ((old or {}).get('branchIds') or [])]
        return bool(bs) and set(bs) <= sc.branches and (old is None or (bool(ob) and set(ob) <= sc.branches))
    if c == 'notices':
        bs = [str(x) for x in (((new or old) or {}).get('audience') or {}).get('branches') or []]
        return bool(bs) and set(bs) <= sc.branches
    return True


def can_del(c, old, u, scope, team):
    if rank_of(u) >= 3 and not _scope(u).all and not _admin_scope_ok(c, None, old, _scope(u)):
        return False
    if c in GEN:
        if c == 'rota' and rank_of(u) == 2 and old.get('eid') not in scope:
            return False
        return rank_of(u) >= GEN[c][0]
    role = role_of(u)
    admin = RANK[role] >= 3
    if c in ('employees', 'clients', 'testimonials', 'agenda'):
        return admin
    if c == 'tasks':
        if _task_has_time(old.get('id')):
            return False  # EC-T04: tasks with logged time are kept for reports
        return admin or (role in MGR_ROLES and old.get('eid') in scope)
    if c == 'reviews':
        return admin or (role in MGR_ROLES and old.get('eid') in team)
    if c == 'attreqs':
        me = prof(u).emp_id
        return admin or bool(me and old.get('eid') == me and old.get('status') in ('Pending', 'Cancelled'))
    if c == 'ratings':
        me = prof(u).emp_id
        return admin or bool(me and old.get('from') == me)
    if c == 'leaves':
        me = prof(u).emp_id
        return admin or bool(me and old.get('eid') == me and old.get('status') in ('Pending', 'Cancelled'))
    if c == 'events':
        return admin or (role in MGR_ROLES and old.get('type') != 'holiday')
    if c == 'notes':
        return str(old.get('uid', first_super_id())) == str(u.pk)
    return False


import re as _re
IMG_RE = _re.compile(r'^data:image/(png|jpe?g|webp|gif);base64,[A-Za-z0-9+/=]+$')
DOC_RE = _re.compile(r'^data:[a-z0-9.+-]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+$', _re.I)
DOC_BAD = _re.compile(r'^data:(text/html|application/xhtml|image/svg|text/xml|application/xml|application/javascript|text/javascript)', _re.I)


def clean_media(c, new):
    """Pictures must be real base64 images and documents real base64 files, never script or links."""
    for k in ('photo', 'logo'):
        if k in new and new[k] not in ('', None):
            if not isinstance(new[k], str) or not IMG_RE.match(new[k]):
                new[k] = ''
    for k in ('eid', 'cid', 'reportsTo'):
        v = new.get(k)
        if v not in ('', None) and (not isinstance(v, str) or not ID_RE.match(v)):
            return False
    for k in ('qty', 'expYears', 'rating', 'ctc'):
        v = new.get(k)
        if v not in ('', None) and not (isinstance(v, (int, float)) and not isinstance(v, bool)) and not (isinstance(v, str) and re.match(r'^-?\d{1,9}(\.\d{1,4})?$', v)):
            return False
    if c == 'documents' and 'data' in new:
        v = new['data']
        if not isinstance(v, str) or not DOC_RE.match(v) or DOC_BAD.match(v):
            return False
    return True


def op_rec(c, op, kind, u, scope, team):
    if kind == 'del':
        r = Record.objects.filter(coll=c, rid=str(op.get('id', ''))).first()
        if not r:
            return True
        if not can_del(c, r.data, u, scope, team):
            return False
        r.delete()
        return True
    if kind != 'set':
        return False
    new = op.get('rec')
    if not isinstance(new, dict) or not isinstance(new.get('id'), str) or not ID_RE.match(new['id']) or len(json.dumps(new)) > (1300000 if c == 'documents' else 400000):
        return False
    if not clean_media(c, new):
        return False
    r = Record.objects.filter(coll=c, rid=new['id']).first()
    old = r.data if r else None
    if not _v6_prepare(c, new, old, u):
        return False
    if settings.SAAS:
        if not saas_hooks.collection_ok(c):
            return False
        if c == 'employees' and new.get('status', 'Active') == 'Active' and (not old or old.get('status', 'Active') != 'Active'):
            n = sum(1 for x in Record.objects.filter(coll='employees') if x.data.get('status', 'Active') == 'Active' and x.rid != new['id'])
            if not saas_hooks.seat_ok(n):
                return False
    final = can_set(c, new, old, u, scope, team)
    if final is None:
        return False
    if r:
        r.data = final
        r.save()
    else:
        Record.objects.create(coll=c, rid=new['id'], data=final)
    if c in ('leaves', 'attreqs'):
        st = str(final.get('status', ''))
        if not old:
            EVT.append((c, 'new', final))
        elif old.get('status') == 'Pending' and st not in ('Pending', 'Cancelled'):
            EVT.append((c, 'decided', final))
    _v6_after(c, final, old, u)
    return True


SERVER_FIELDS = {'employees': ('branchId',), 'tasks': ('createdBy', 'createdAt', 'branchId', 'src', 'att'), 'leaves': ('branchId',)}


def _task_has_time(tid):
    from .models import WorkSession
    return bool(tid) and WorkSession.objects.filter(task_rid=tid).exclude(status='void').exists()


def _v6_prepare(c, new, old, u):
    """Validate v6 fields and keep server-owned fields (FR-PLT-12): an old browser tab or a crafted request cannot change them."""
    for k in SERVER_FIELDS.get(c, ()):
        if old is not None:
            if k in old:
                new[k] = old[k]
            else:
                new.pop(k, None)
        elif not (c == 'employees' and k == 'branchId'):
            new.pop(k, None)
    if c == 'tasks':
        for k, lo, hi in (('est', 5, 59999), ('dailyMin', 15, 1440)):
            v = new.get(k)
            if v in ('', None):
                new.pop(k, None)
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= v <= hi:
                return False
            new[k] = int(v)
        if new.get('start') not in (None, '') and not re.match(r'^\d{4}-\d{2}-\d{2}$', str(new['start'])):
            return False
        if new.get('start') and new.get('due') and str(new['start']) > str(new['due']):
            return False
        for k in ('timeRequired', 'allowManual'):
            if k in new and not isinstance(new[k], bool):
                return False
        if 'dept' in new:
            new['dept'] = str(new.get('dept') or '')[:80]
    return True


def _v6_after(c, final, old, u):
    """Branch assignment, timers and notifications that follow a legacy save. Never breaks the save itself."""
    import logging
    try:
        with transaction.atomic():
            from .v6 import branches as v6b, notify as v6n, worktime as v6w
            rid = final.get('id')
            if c == 'employees':
                r = Record.objects.get(coll='employees', rid=rid)
                if old is None:
                    sc = _scope(u)
                    want = str(final.get('branchId') or '')
                    br = v6b.branch_by_id(want) if want and (sc.all or want in sc.branches) else None
                    if br is None or br.status != 'active':
                        br = (v6b.branch_by_id(sorted(sc.branches)[0]) if (not sc.all and sc.branches) else None) or v6b.head_office()
                    r.data = dict(r.data, branchId=str(br.pk))
                    r.save(update_fields=['data'])
                    v6b.initial_assignment(rid, r.data, br)
                    if final.get('status', 'Active') == 'Active':
                        v6n.emit('employee.welcome', 'employee', rid, 'welcome', [rid], {})
                else:
                    if str(old.get('phone', '')) != str(final.get('phone', '')):
                        v6n.phone_changed(rid, final.get('phone'))
                    if old.get('status', 'Active') == 'Active' and final.get('status', 'Active') != 'Active':
                        v6w.stop_all_for_emp(rid, 'inactive')
            elif c == 'tasks':
                r = Record.objects.get(coll='tasks', rid=rid)
                d = dict(r.data)
                changed = False
                if old is None:
                    d.update(createdBy=str(u.pk), createdAt=time.strftime('%Y-%m-%dT%H:%M:%S'))
                    changed = True
                if old is None or old.get('eid') != d.get('eid'):
                    e = Record.objects.filter(coll='employees', rid=d.get('eid', '')).first()
                    ed = e.data if e else {}
                    d['branchId'] = str(ed.get('branchId') or v6b.head_office().pk)
                    if not d.get('dept'):
                        d['dept'] = str(ed.get('dept', ''))[:80]
                    changed = True
                if changed:
                    r.data = d
                    r.save(update_fields=['data'])
                if old is not None and old.get('eid') != d.get('eid'):
                    v6w.stop_open_for_task(rid, 'reassigned', except_emp=d.get('eid'))
                if old is None or old.get('eid') != d.get('eid'):
                    v6n.task_assigned(d, rid)
                if d.get('status') == 'done' and (old is None or old.get('status') != 'done'):
                    v6w.stop_open_for_task(rid, 'completed')
                    v6n.task_completed(d, rid)
            elif c == 'leaves':
                if old is None:
                    e = Record.objects.filter(coll='employees', rid=final.get('eid', '')).first()
                    r = Record.objects.get(coll='leaves', rid=rid)
                    r.data = dict(r.data, branchId=str(((e.data if e else {}) or {}).get('branchId') or ''))
                    r.save(update_fields=['data'])
                    v6n.leave_event('new', final)
                elif old.get('status') == 'Pending' and final.get('status') in ('Approved', 'Rejected'):
                    v6n.leave_event('decided', final)
                elif final.get('status') == 'Cancelled':
                    v6n.cancel_jobs('leave.submitted', rid)
            elif c == 'notices' and old is None and (final.get('sendVia') or (final.get('audience') or {}).get('branches')):
                v6n.notice_published(final, u)
    except Exception:
        logging.getLogger('hub.v6').exception('v6 follow-up for %s failed', c)


def op_att(op, kind, u, team):
    d, e = str(op.get('date', '')), str(op.get('eid', ''))
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', d) or not ID_RE.match(e):
        return False
    if rank_of(u) < 3 and not (role_of(u) in MGR_ROLES and e in team):
        return False
    if rank_of(u) >= 3 and not _scope(u).sees(e, primary_only=True):
        return False
    er = Record.objects.filter(coll='employees', rid=e).first()
    if er and (er.data or {}).get('core') and kind != 'del':
        return False
    ex = Attendance.objects.filter(day=d, eid=e).first()
    exd = ex.data if ex else None
    if role_of(u) in MGR_ROLES:
        if exd and exd.get('br') not in ('self', 'device') and not (d == datetime.date.today().isoformat() and exd.get('br') == 'manager'):
            return False
        if kind == 'del':
            return False
    if kind == 'del':
        Attendance.objects.filter(day=d, eid=e).delete()
        return True
    r = op.get('rec')
    if kind != 'set' or not isinstance(r, dict) or r.get('s') not in ('P', 'L', 'A', 'V', 'W'):
        return False
    p = [[str(x[0]), str(x[1])] for x in (r.get('p') or []) if isinstance(x, list) and len(x) >= 2]
    from .punch import flat
    rec = {'s': r['s'], 'p': p, 'tl': flat(p), 'by': u.username, 'br': 'manager' if role_of(u) in MGR_ROLES else ('admin' if rank_of(u) == 3 else role_of(u))}
    if er and (er.data or {}).get('branchId'):
        rec['b'] = str(er.data['branchId'])
    Attendance.objects.update_or_create(day=d, eid=e, defaults={'data': rec})
    return True


class _TLList:
    """A list that is private to the current request thread, so two requests never share queued audit rows or e-mails."""
    import threading as _th
    _tl = _th.local()

    def __init__(self, name):
        self.name = name

    def _l(self):
        v = getattr(self._tl, self.name, None)
        if v is None:
            v = []
            setattr(self._tl, self.name, v)
        return v

    def append(self, x):
        self._l().append(x)

    def extend(self, x):
        self._l().extend(x)

    def __getitem__(self, i):
        return self._l()[i]

    def __delitem__(self, i):
        del self._l()[i]

    def __len__(self):
        return len(self._l())

    def __bool__(self):
        return bool(self._l())

    def __iter__(self):
        return iter(self._l())

    def __radd__(self, other):
        return list(other) + self._l()


EVT = _TLList('evt')
AUDIT = _TLList('audit')
PENDING_FAILS = _TLList('fails')
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def client_ip(request):
    """The visitor's address. Behind a proxy set HUB_TRUST_PROXY=1: the last X-Forwarded-For entry is the one your proxy added."""
    import os
    if os.environ.get('HUB_TRUST_PROXY') == '1':
        xf = [x.strip() for x in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',') if x.strip()]
        if xf:
            return xf[-1][:45]
    return request.META.get('REMOTE_ADDR', '')[:45]


def audit_flush():
    if not AUDIT:
        return
    l = cget('audit', [])
    cset('audit', (l + AUDIT)[-3000:])
    del AUDIT[:]


def audit_note(u, c, what):
    AUDIT.append([time.strftime('%Y-%m-%d %H:%M:%S'), u.username, c, 'set', what])
    audit_flush()


def emp_email(db, eid):
    for e in db['employees']:
        if e.get('id') == eid:
            return str(e.get('email', '')).strip()
    return ''


def flush_mail():
    ev = EVT[:20]
    del EVT[:]
    if not ev:
        return
    db = full_db()
    if not (db.get('settings') or {}).get('emailAlerts'):
        return
    from django.core.mail import EmailMessage, get_connection
    import os
    kw = {}
    if os.environ.get('HUB_EMAIL_HOST'):
        kw = dict(backend='django.core.mail.backends.smtp.EmailBackend', host=os.environ['HUB_EMAIL_HOST'], port=int(os.environ.get('HUB_EMAIL_PORT', '587')),
                  username=os.environ.get('HUB_EMAIL_USER', ''), password=os.environ.get('HUB_EMAIL_PASSWORD', ''), use_tls=os.environ.get('HUB_EMAIL_TLS', '1') == '1')
    else:
        kw = dict(backend='django.core.mail.backends.console.EmailBackend')
    sender = os.environ.get('HUB_EMAIL_FROM', 'no-reply@localhost')
    adm = []
    for p in Profile.objects.filter(role__in=('admin', 'superadmin'), user__is_active=True):
        if p.emp_id:
            adm.append(emp_email(db, p.emp_id))
    co = db.get('company', 'Assertive Hub')
    for c, k, r in ev:
        eid = str(r.get('eid', ''))
        e = next((x for x in db['employees'] if x.get('id') == eid), {})
        who, mgr = e.get('name', ''), e.get('reportsTo', '')
        what = 'leave request' if c == 'leaves' else 'attendance correction request'
        if k == 'new':
            to = adm + ([emp_email(db, mgr)] if mgr and c == 'leaves' else [])
            subj, body = '%s: new %s from %s' % (co, what, who), '%s sent a %s. Open the app to review it.' % (who, what)
        else:
            to = [emp_email(db, eid)]
            subj, body = '%s: your %s was %s' % (co, what, str(r.get('status', '')).lower()), 'Your %s is now %s. %s' % (what, r.get('status', ''), r.get('note') or r.get('reply') or '')
        for addr in sorted(set(x for x in to if x and '@' in x)):
            try:
                EmailMessage(subj, body, sender, [addr], connection=get_connection(fail_silently=True, **kw)).send()
            except Exception:
                pass


def apply_ops(ops, u):
    me = prof(u).emp_id
    team = team_of(me)
    scope = dict(team)
    if role_of(u) == 'branchmgr':
        scope.update({k: True for k in _scope(u).emps})
    if me:
        scope[me] = True
    ap = rj = 0
    for op in ops:
        ok = False
        if isinstance(op, dict):
            c, kind = str(op.get('c', '')), str(op.get('op', ''))
            if c == 'settings':
                v = op.get('val')
                if rank_of(u) >= 4 and kind == 'set' and isinstance(v, dict) and len(json.dumps(v)) <= 900000:
                    cset('settings', v)
                    ok = True
            elif c == 'company':
                v = op.get('val')
                if rank_of(u) >= 4 and kind == 'set' and isinstance(v, str) and v.strip():
                    cset('company', v.strip()[:80])
                    ok = True
            elif c == 'attendance':
                ok = op_att(op, kind, u, team)
            elif c in COLS:
                ok = op_rec(c, op, kind, u, scope, team)
        if ok:
            ap += 1
            rec = op.get('rec') if isinstance(op.get('rec'), dict) else {}
            AUDIT.append([time.strftime('%Y-%m-%d %H:%M:%S'), u.username, c, str(op.get('op', '')), str(rec.get('id') or op.get('id') or (str(op.get('date', '')) + ' ' + str(op.get('eid', ''))))])
        else:
            rj += 1
    return ap, rj


def write_backup():
    d = (saas_tenancy.data_dir() if settings.SAAS else Path(settings.DATA_DIR)) / 'backups'
    d.mkdir(parents=True, exist_ok=True)
    text = json.dumps(full_db())
    (d / ('daily-%s.json' % datetime.date.today().isoformat())).write_text(text)
    (d / ('monthly-%s.json' % datetime.date.today().strftime('%Y-%m'))).write_text(text)
    cut = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    for f in d.glob('daily-*.json'):
        if f.stem[6:] < cut:
            f.unlink()


def restore_db(d):
    if settings.SAAS:
        act = sum(1 for r in (d.get('employees') or []) if isinstance(r, dict) and r.get('status', 'Active') == 'Active')
        if not saas_hooks.seat_ok(act - 1):
            fail('This backup has more active people than your plan allows. Upgrade first, or mark people inactive.', 402)
    Record.objects.all().delete()
    Attendance.objects.all().delete()
    for c in COLS:
        if settings.SAAS and not saas_hooks.collection_ok(c):
            continue
        for r in d.get(c) or []:
            if isinstance(r, dict) and isinstance(r.get('id'), str) and ID_RE.match(r['id']) and clean_media(c, r):
                Record.objects.update_or_create(coll=c, rid=r['id'], defaults={'data': r})
    att = d.get('attendance') or {}
    if isinstance(att, dict):
        for day, m in att.items():
            if isinstance(m, dict) and re.match(r'^\d{4}-\d{2}-\d{2}$', str(day)):
                for e, r in m.items():
                    if isinstance(r, dict) and ID_RE.match(str(e)):
                        r = {k: v for k, v in r.items() if k != 'sf'}  # selfie file names are never taken from a backup
                        Attendance.objects.update_or_create(day=day, eid=e, defaults={'data': r})
    cset('company', str(d.get('company') or cget('company', 'My Company'))[:80])
    st = d.get('settings')
    cset('settings', st if isinstance(st, dict) else {})


# ---------- request ----------
def body_json(request):
    try:
        b = json.loads(request.body or b'{}')
        return b if isinstance(b, dict) else {}
    except ValueError:
        return {}


def check_name(user, ex_pk=None):
    if len(user) < 3 or len(user) > 40:
        fail('Username must be 3 to 40 characters.')
    q = User.objects.filter(username__iexact=user)
    if ex_pk is not None:
        q = q.exclude(pk=ex_pk)
    if q.exists():
        fail('That username is already taken.')


@csrf_exempt
def api(request):
    if request.headers.get('X-Requested-With') != 'staffroom':
        return JsonResponse({'ok': False, 'error': 'Bad request.'}, status=400)
    a = request.GET.get('a', '')
    post = request.method == 'POST'
    del EVT[:]
    del AUDIT[:]
    del PENDING_FAILS[:]
    try:
        if a != 'status' and a != 'load' and a != 'users' and a != 'audit' and not post and not ('.' in a and _v6_is_read(a)):
            fail('POST required.', 405)
        if settings.SAAS:
            blk = saas_hooks.block(a, post)
            if blk:
                fail(blk[1], blk[0])
        with transaction.atomic(using=saas_tenancy.alias() if settings.SAAS else 'default'):
            out = handle(request, a)
        return out if isinstance(out, HttpResponse) else JsonResponse(out)
    except ApiError as e:
        o = {'ok': False, 'error': e.msg}
        if getattr(e, 'err', None):
            o['code'] = e.err
            if e.details:
                o['details'] = e.details
        return JsonResponse(o, status=e.code)
    finally:
        if PENDING_FAILS:
            now = time.time()
            try:
                LoginFail.objects.bulk_create([LoginFail(name=k, at=now) for k in PENDING_FAILS])
            except Exception:
                pass
        del EVT[:]
        del AUDIT[:]
        del PENDING_FAILS[:]


def _v6_is_read(a):
    from .v6 import base as v6base
    return v6base.is_read(a)


def punch_actions(request, a, b):
    from . import punch
    u = need_auth(request)
    if settings.SAAS and not saas_hooks.module_ok('checkin' if a in ('punch', 'qr') else ('biometric' if a == 'devinfo' else 'checkin')):
        fail('Not in your plan.', 403)
    if a == 'punch':
        try:
            out = punch.check_in(u, prof(u).emp_id, b)
        except punch.PunchError as e:
            fail(str(e), 400)
        cset('rev', rev_get() + 1)
        return dict(out, ok=True)
    if rank_of(u) < 3 and not (a == 'selfie' and rank_of(u) == 2):
        fail('Admins only.', 403)
    if a == 'qr':
        url, svg = punch.qr_svg(request)
        return {'ok': True, 'url': url, 'svg': svg}
    if a == 'devinfo':
        if rank_of(u) < 4:
            fail('Super admin only.', 403)
        base = request.build_absolute_uri('/')
        return {'ok': True, 'key': punch.dev_key(), 'host': request.get_host().split(':')[0], 'seen': cget('devseen', {}), 'url': base + 'device/punch'}
    if a == 'selfie':
        if rank_of(u) == 2 and str(b.get('eid', '')) not in team_of(prof(u).emp_id):
            fail('Not your team.', 403)
        if rank_of(u) == 3 and not _scope(u).sees(str(b.get('eid', '')), primary_only=True):
            fail('No picture.', 404)
        img = punch.selfie_data(str(b.get('date', '')), str(b.get('eid', '')), str(b.get('time', '')))
        if not img:
            fail('No picture.', 404)
        return {'ok': True, 'img': img}
    fail('Unknown action.', 404)


def handle(request, a):
    b = body_json(request) if request.method == 'POST' else {}
    if a == 'status':
        u = request.user if request.user.is_authenticated and request.user.is_active else None
        import re as _re
        bg = str((cget('settings', {}) or {}).get('loginBg') or '')
        brand = {'company': str(cget('company', '') or ''), 'bg': bg if _re.match(r'^https://[^\s"\'<>()]+$', bg) else ''}
        return {'ok': True, 'setup': not User.objects.exists(), 'authed': bool(u), 'me': me_view(u) if u else None, 'brand': brand}

    if a == 'setup':
        if User.objects.exists():
            fail('An account already exists.', 403)
        user, pw = str(b.get('user', '')).strip(), str(b.get('pass', ''))
        check_name(user)
        if len(pw) < 8:
            fail('Password must be at least 8 characters.')
        u = User.objects.create_user(user, password=pw)
        Profile.objects.create(user=u, role='superadmin')
        cset('company', str(b.get('company', '')).strip()[:80] or 'My Company')
        cset('settings', {})
        cset('rev', 1)
        dj_login(request, u)
        request.session.set_expiry(settings.SESSION_COOKIE_AGE)
        return {'ok': True, 'me': me_view(u)}

    if a == 'login':
        name = str(b.get('user', '')).strip().lower()[:40]
        ip = client_ip(request)
        key, ipkey = (name + '|' + ip)[:150], ('ip|' + ip)[:150]
        now = time.time()
        LoginFail.objects.filter(at__lt=now - 900).delete()
        if LoginFail.objects.filter(name=key).count() >= 5 or LoginFail.objects.filter(name=ipkey).count() >= 30:
            fail('Too many attempts. Try again in 15 minutes.', 429)
        found = User.objects.filter(username__iexact=name).first()
        ok_pw = bool(found and found.check_password(str(b.get('pass', ''))))
        if not found or not ok_pw or not found.is_active:
            PENDING_FAILS.extend([key, ipkey])  # saved after the request transaction, so the rollback cannot erase them
            time.sleep(0.4)
            fail('This login has been switched off. Ask your admin.' if (found and ok_pw and not found.is_active)
                 else 'Username or password is incorrect.', 401)
        LoginFail.objects.filter(name=key).delete()
        dj_login(request, found, backend='django.contrib.auth.backends.ModelBackend')
        request.session.set_expiry(settings.SESSION_COOKIE_AGE if b.get('remember', True) else 0)
        return {'ok': True, 'me': me_view(found)}

    if a == 'logout':
        if request.user.is_authenticated:
            try:
                with transaction.atomic():
                    from .v6 import worktime as v6w
                    v6w.on_logout(request.user)
            except Exception:
                pass
        dj_logout(request)
        return {'ok': True}

    if a in ('punch', 'qr', 'devinfo', 'selfie'):
        return punch_actions(request, a, b)

    if a == 'load':
        u = need_auth(request)
        return {'ok': True, 'rev': rev_get(), 'me': me_view(u), 'data': view_for(u)}

    if a == 'save':
        u = need_auth(request)
        ops = b.get('ops')
        if not isinstance(ops, list) or len(ops) > 5000:
            fail('Invalid data.')
        try:
            base = int(request.headers.get('X-Base-Rev', -1))
        except ValueError:
            base = -1
        rev = rev_get()
        ap, rj = apply_ops(ops, u)
        new = rev
        if ap > 0:
            new = rev + 1
            cset('rev', new)
            write_backup()
            audit_flush()
            flush_mail()
        o = {'ok': True, 'rev': new, 'applied': ap, 'rejected': rj}
        if rj > 0 or base != rev:
            o['data'] = view_for(u)
        return o

    if a == 'restore':
        need_role(request, 4)
        if not isinstance(b.get('employees'), list) or not isinstance(b.get('tasks'), list) or not isinstance(b.get('reviews'), list):
            fail('That is not a valid backup.')
        restore_db(b)
        cset('rev', rev_get() + 1)
        write_backup()
        return {'ok': True, 'rev': rev_get()}

    if a == 'forgot':
        nm = str(b.get('user', '')).strip().lower()
        if nm and len(nm) <= 40:
            x = User.objects.filter(username__iexact=nm, is_active=True).first()
            f = cget('forgot', {})
            if x and time.time() - float(f.get(str(x.pk), 0)) > 600:
                f[str(x.pk)] = time.time()
                cset('forgot', f)
        return {'ok': True}

    if a == 'audit':
        u = need_role(request, 3)
        sc = _scope(u)
        from .models import AuditEvent
        q = AuditEvent.objects.order_by('-at')
        if not sc.all:
            q = q.filter(branch_id__in=[int(x) for x in sc.branches if x.isdigit()])
        v6 = [[timezone_local(e.at), e.actor_name, e.entity_type, e.action, (e.entity_id + ' ' + _short(e.after))[:200]] for e in q[:400]]
        legacy = list(reversed(cget('audit', [])[-400:])) if sc.all else []
        return {'ok': True, 'log': sorted(v6 + legacy, key=lambda r: r[0], reverse=True)[:600]}

    if a == 'users':
        u = need_role(request, 3)
        f = cget('forgot', {})
        out, fg = [], []
        sc = _scope(u)
        for x in User.objects.order_by('pk'):
            if rank_of(u) >= 4 or (rank_of(x) <= 2 and (sc.all or prof(x).emp_id in sc.primary)):
                out.append(user_pub(x))
                if str(x.pk) in f:
                    fg.append({'id': str(x.pk), 'user': x.username, 'at': time.strftime('%Y-%m-%d %H:%M', time.localtime(float(f[str(x.pk)])))})
        return {'ok': True, 'users': out, 'forgot': fg}

    if a == 'user_save':
        me = need_role(request, 3)
        uid, role = str(b.get('id', '')), str(b.get('role', ''))
        if role not in RANK:
            fail('Choose a role.')
        mr = rank_of(me)
        if mr < 4 and RANK[role] > 2:
            fail('Admins can only create managers and employees.', 403)
        ex = User.objects.filter(pk=uid).first() if uid.isdigit() else None
        if uid and not ex:
            fail('User not found.', 404)
        if ex and mr < 4 and rank_of(ex) > 2:
            fail('You cannot change this user.', 403)
        msc = _scope(me)
        if ex and not msc.all and prof(ex).emp_id not in msc.primary:
            fail('You cannot change this user.', 403)
        user = str(b.get('user', '')).strip()
        check_name(user, ex.pk if ex else None)
        emp = str(b.get('empId', ''))
        if RANK[role] <= 2 and not emp:
            fail('Link this login to an employee record.')
        if emp:
            if not Record.objects.filter(coll='employees', rid=emp).exists():
                fail('That employee record does not exist.')
            if not msc.all and emp not in msc.primary:
                fail('That employee is not in your branches.', 403)
            if Profile.objects.filter(emp_id=emp).exclude(user=ex).exists():
                fail('That employee already has a login.')
        pw = str(b.get('pass', ''))
        if not ex and len(pw) < 8:
            fail('Password must be at least 8 characters.')
        if pw and len(pw) < 8:
            fail('Password must be at least 8 characters.')
        active = bool(b.get('active'))
        if ex and ex.pk == me.pk and (role != role_of(ex) or not active):
            fail('You cannot change your own role or switch yourself off.')
        role_changed = bool(ex and role_of(ex) != role)
        x = ex or User(username=user)
        x.username, x.is_active = user, active
        if pw:
            x.set_password(pw)
        elif not ex:
            x.set_unusable_password()
        x.save()
        p = prof(x)
        p.role, p.emp_id = role, emp
        p.save()
        from .models import UserBranchScope
        if role in ('hradmin', 'branchadmin', 'branchmgr'):
            from .v6.branches import set_user_branches
            set_user_branches(me, x, b.get('branches') or [])
        else:
            UserBranchScope.objects.filter(user=x).delete()
        LoginFail.objects.filter(name=user.lower()).delete()
        if pw:
            f = cget('forgot', {})
            if f.pop(str(x.pk), None) is not None:
                cset('forgot', f)
        audit_note(me, 'users', ('edit ' if ex else 'add ') + user + ' as ' + role + (' (password set)' if pw else ''))
        if active_supers() < 1:
            transaction.set_rollback(True)
            fail('There must always be at least one active super admin.')
        if ex and (pw or not active or role_changed):
            revoke_sessions(x.pk)
        return {'ok': True, 'user': user_pub(x)}

    if a == 'user_del':
        me = need_role(request, 3)
        x = User.objects.filter(pk=str(b.get('id', ''))).first() if str(b.get('id', '')).isdigit() else None
        if not x:
            fail('User not found.', 404)
        if x.pk == me.pk:
            fail('You cannot delete your own login.')
        if rank_of(me) < 4 and rank_of(x) > 2:
            fail('You cannot delete this user.', 403)
        if not _scope(me).all and prof(x).emp_id not in _scope(me).primary:
            fail('You cannot delete this user.', 403)
        if active_supers(exclude_pk=x.pk) < 1:
            fail('There must always be at least one active super admin.')
        revoke_sessions(x.pk)
        x.delete()
        return {'ok': True}

    if a == 'user_signout':
        me = need_role(request, 3)
        x = User.objects.filter(pk=str(b.get('id', ''))).first() if str(b.get('id', '')).isdigit() else None
        if not x:
            fail('User not found.', 404)
        if rank_of(me) < 4 and rank_of(x) > 2:
            fail('You cannot change this user.', 403)
        if not _scope(me).all and prof(x).emp_id not in _scope(me).primary:
            fail('You cannot change this user.', 403)
        revoke_sessions(x.pk)
        return {'ok': True}

    if a == 'prefs':
        u = need_auth(request)
        p = prof(u)
        cur = dict(p.prefs or {})
        if 'theme' in b or 'mode' in b:
            theme, mode = str(b.get('theme', '')), str(b.get('mode', ''))
            if theme not in THEME_IDS + ['blue'] or mode not in ('auto', 'light', 'dark'):
                fail('Invalid choice.')
            cur.update(theme=theme, mode=mode)
        if 'dash' in b:
            dsh = b.get('dash') if isinstance(b.get('dash'), dict) else {}
            ids = lambda k: [str(x)[:20] for x in (dsh.get(k) or []) if re.match(r'^[a-z0-9_]{1,20}$', str(x))][:30]
            cur['dash'] = {'order': ids('order'), 'hidden': ids('hidden')}
        p.prefs = cur  # merged, so theme and dashboard layout never wipe each other (FR-PLT-11)
        p.save()
        return {'ok': True}

    if a == 'account':
        u = need_auth(request)
        if not u.check_password(str(b.get('current', ''))):
            fail('Current password is incorrect.', 403)
        user = str(b.get('user', '')).strip()
        if user:
            check_name(user, u.pk)
            u.username = user
        np_ = str(b.get('pass', ''))
        if np_:
            if len(np_) < 8:
                fail('New password must be at least 8 characters.')
            u.set_password(np_)
        u.save()
        if np_:
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, u)
        return {'ok': True, 'user': u.username, 'me': me_view(u)}

    if '.' in a:
        from .v6 import base as v6base
        return v6base.dispatch(request, a, b)

    fail('Unknown action.', 404)


def index(request):
    html = APP_HTML.read_bytes()
    r = HttpResponse(html, content_type='text/html; charset=utf-8')
    r['Cache-Control'] = 'no-cache'
    from .secmw import page_csp
    r['Content-Security-Policy'] = page_csp(html)
    return r


ASSETS = {'sw.js': 'application/javascript', 'icon-192.png': 'image/png', 'icon-512.png': 'image/png', 'icon-maskable-512.png': 'image/png', 'icon-180.png': 'image/png', 'logo.png': 'image/png'}


def asset(request, name):
    if name not in ASSETS:
        return HttpResponse('Not found', status=404)
    r = HttpResponse((APP_HTML.parent / name).read_bytes(), content_type=ASSETS[name])
    r['Cache-Control'] = 'no-cache' if name == 'sw.js' else 'public, max-age=86400'
    if name == 'sw.js':
        r['Service-Worker-Allowed'] = '/'
    return r


def manifest(request):
    co = str(cget('company', '') or 'Assertive Hub')
    m = {'name': co, 'short_name': co[:12], 'start_url': './', 'scope': './', 'display': 'standalone', 'orientation': 'portrait',
         'background_color': '#ffffff', 'theme_color': '#0f1115',
         'icons': [{'src': 'icon-192.png', 'sizes': '192x192', 'type': 'image/png'}, {'src': 'icon-512.png', 'sizes': '512x512', 'type': 'image/png'},
                   {'src': 'icon-maskable-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'}]}
    return JsonResponse(m, content_type='application/manifest+json')
