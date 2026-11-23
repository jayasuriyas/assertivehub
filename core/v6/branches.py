"""Multi-branch management: branches, employee assignment history, transfers, departments, designations, rooms."""
import datetime
import re

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from core import views as V
from core.models import (Branch, BranchDepartment, Department, Designation, EmployeeBranchAssignment as A, MeetingRoom, Record,
                         Transfer, UserBranchScope, Meeting)
from . import audit, jobs
from .base import action, err, invalid, need, can, text, int_in, parse_date, hm_to_min, now, iso, paged, emp_map, bump_rev, tz
from .scope import scope_for, reset as reset_scope

CODE_RE = re.compile(r'^[A-Z0-9-]{2,10}$')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ---------- service helpers ----------
def head_office():
    b = Branch.objects.filter(is_head_office=True).first()
    if b:
        return b
    s = V.cget('settings', {}) or {}
    days = s.get('workDays') if isinstance(s.get('workDays'), list) else [1, 2, 3, 4, 5]
    code = 'HO'
    while Branch.objects.filter(code=code).exists():
        code = code + 'X'
    return Branch.objects.create(code=code, name='Head Office', is_head_office=True, timezone=settings.TIME_ZONE,
                                 work_start=s.get('workStart') or '09:00', work_end=s.get('workEnd') or '18:00',
                                 break_min=int(s.get('breakMin') if s.get('breakMin') is not None else 60), work_days=days)


def branch_by_id(bid):
    if str(bid or '').isdigit():
        return Branch.objects.filter(pk=int(bid)).first()
    return None


def branch_of_emp(eid, data=None):
    d = data if data is not None else ((Record.objects.filter(coll='employees', rid=eid).first() or Record(data={})).data or {})
    return branch_by_id(d.get('branchId')) or head_office()


def branch_on(eid, day):
    """Primary branch id (str) in effect for this employee on this date (BR-B7)."""
    a = A.objects.filter(emp_id=eid, kind='primary', effective_from__lte=day).exclude(status='cancelled').filter(
        Q(effective_to__isnull=True) | Q(effective_to__gte=day)).order_by('-effective_from').first()
    if a:
        return str(a.branch_id)
    e = Record.objects.filter(coll='employees', rid=eid).first()
    return str((e.data or {}).get('branchId') or '') if e else ''


def multi_branch():
    return Branch.objects.filter(status='active').count() > 1


def plan_branch_limit():
    if not settings.SAAS:
        return None
    from saas import state, plans
    i = state.current()
    if not i:
        return None
    return plans.PLANS.get(i['plan'], {}).get('branches')


def _set_emp_fields(eid, **kw):
    r = Record.objects.filter(coll='employees', rid=eid).first()
    if not r:
        return
    d = dict(r.data)
    for k, v in kw.items():
        if v is not None and v != '':
            d[k] = v
    if d != r.data:
        r.data = d
        r.save(update_fields=['data'])


def initial_assignment(eid, data, branch=None):
    """Called when an employee record is created (or by the migration). Starts the primary assignment."""
    if A.objects.filter(emp_id=eid, kind='primary').exclude(status='cancelled').exists():
        return
    b = branch or branch_by_id((data or {}).get('branchId')) or head_office()
    start = parse_date((data or {}).get('joined')) or datetime.date.today()
    A.objects.create(emp_id=eid, branch=b, kind='primary', effective_from=start, dept=str((data or {}).get('dept', ''))[:80],
                     designation=str((data or {}).get('designation', ''))[:80], reports_to=str((data or {}).get('reportsTo', ''))[:60], reason='Initial assignment')
    if (data or {}).get('branchId') != str(b.pk):
        _set_emp_fields(eid, branchId=str(b.pk))


def apply_transfer(t, actor=None):
    """Make a transfer effective: close the old primary assignment, open the new one, update the employee record."""
    if t.status != 'scheduled':
        return
    cur = A.objects.filter(emp_id=t.emp_id, kind='primary', status='active', effective_to__isnull=True).first()
    e = Record.objects.filter(coll='employees', rid=t.emp_id).first()
    ed = (e.data if e else {}) or {}
    if cur:
        if t.effective_date <= cur.effective_from:
            cur.status = 'cancelled'  # EC-B02: replaces the initial assignment, no history for the old branch
        else:
            cur.effective_to = t.effective_date - datetime.timedelta(days=1)
            cur.status = 'ended'
        cur.save()
    A.objects.create(emp_id=t.emp_id, branch=t.to_branch, kind='primary', effective_from=t.effective_date,
                     dept=t.new_dept or ed.get('dept', ''), designation=t.new_designation or ed.get('designation', ''),
                     reports_to=t.new_reports_to or ed.get('reportsTo', ''), reason=t.reason, transfer=t)
    _set_emp_fields(t.emp_id, branchId=str(t.to_branch_id), dept=t.new_dept or None, designation=t.new_designation or None, reportsTo=t.new_reports_to or None)
    from . import worktime
    for r in (t.side_effects or {}).get('reassign', []):
        tr = Record.objects.filter(coll='tasks', rid=str(r.get('taskId', ''))).first()
        if tr and tr.data.get('eid') == t.emp_id and str(r.get('toEmpId', '')):
            d = dict(tr.data)
            d['eid'] = str(r['toEmpId'])
            tr.data = d
            tr.save(update_fields=['data'])
            worktime.stop_open_for_task(tr.rid, reason='reassigned', except_emp=d['eid'])
    t.status = 'applied'
    t.applied_at = now()
    t.save()
    bump_rev()
    audit.record(actor, 'transfer.applied', 'employee', t.emp_id, after={'to': t.to_branch.code, 'from': t.from_branch.code, 'effective': t.effective_date.isoformat()}, branch_id=t.to_branch_id)
    from . import notify
    notify.emit('employee.transferred', 'transfer', str(t.pk), 'applied', [t.emp_id], {'branch_id': t.to_branch_id, 'effective_date': t.effective_date.isoformat()})


@jobs.handler('branches.apply_transfers')
def _apply_due(payload, job):
    today = datetime.date.today()
    for t in Transfer.objects.filter(status='scheduled', effective_date__lte=today).select_related('to_branch', 'from_branch'):
        apply_transfer(t)


# ---------- serialisers ----------
def bdict(b, counts=None, full=False):
    d = {'id': str(b.pk), 'code': b.code, 'name': b.name, 'city': b.city, 'state': b.state, 'country': b.country, 'status': b.status,
         'tz': b.timezone, 'ho': b.is_head_office, 'manager': b.manager_emp, 'workStart': b.work_start, 'workEnd': b.work_end,
         'breakMin': b.break_min, 'workDays': b.work_days}
    if counts is not None:
        d['headcount'] = counts.get(str(b.pk), 0)
    if full:
        d.update(address1=b.address1, address2=b.address2, postal=b.postal_code, phone=b.phone, email=b.email, reason=b.deactivated_reason,
                 deactivatedAt=iso(b.deactivated_at), used=branch_used(b))
    return d


def branch_used(b):
    return A.objects.filter(branch=b).exists() or Meeting.objects.filter(branch=b).exists()


def headcounts():
    c = {}
    for r in Record.objects.filter(coll='employees'):
        if r.data.get('status', 'Active') == 'Active':
            k = str(r.data.get('branchId') or '')
            c[k] = c.get(k, 0) + 1
    return c


def load_payload(u):
    """Small, read-only branch data added to `load` (all users need the list for names and filters)."""
    s = scope_for(u)
    out = []
    for b in Branch.objects.all():
        if s.all or str(b.pk) in s.branches or V.rank_of(u) <= 2:
            out.append({'id': str(b.pk), 'code': b.code, 'name': b.name, 'status': b.status, 'tz': b.timezone, 'ho': b.is_head_office,
                        'workStart': b.work_start, 'workEnd': b.work_end, 'workDays': b.work_days})
    return out


# ---------- branches ----------
@action('branches.list', module='branches', read=True)
def a_list(request, u, b):
    need(u, 'branch.view')
    s = scope_for(u)
    q = Branch.objects.all()
    if not s.all:
        q = q.filter(pk__in=[int(x) for x in s.branches if x.isdigit()])
    if str(b.get('status') or '') in ('active', 'inactive'):
        q = q.filter(status=b['status'])
    c = headcounts()
    return {'items': [bdict(x, c) for x in q], 'limit': plan_branch_limit()}


def _get_scoped(u, bid):
    br = branch_by_id(bid)
    if not br or not scope_for(u).sees_branch(br.pk):
        err('NOT_FOUND', 'Branch not found.')
    return br


@action('branches.get', module='branches', read=True)
def a_get(request, u, b):
    need(u, 'branch.view')
    br = _get_scoped(u, b.get('id'))
    d = bdict(br, headcounts(), full=True)
    d['departments'] = [{'id': str(x.department_id), 'name': x.department.name, 'head': x.head_emp} for x in br.departments.select_related('department')]
    d['rooms'] = [room_dict(r) for r in br.rooms.all()]
    d['admins'] = [{'id': str(x.user_id), 'user': x.user.username, 'role': V.role_of(x.user)} for x in br.user_scopes.select_related('user')]
    return {'branch': d}


@action('branches.save', module='branches')
def a_save(request, u, b):
    need(u, 'branch.manage')
    bid = b.get('id')
    br = branch_by_id(bid) if bid else None
    if bid and not br:
        err('NOT_FOUND', 'Branch not found.')
    name = text(b, 'name', 80, req=True, mn=2, label='Branch name')
    code = text(b, 'code', 10, req=True, label='Branch code').upper()
    if not CODE_RE.match(code):
        invalid(code='Use 2 to 10 capital letters, numbers or hyphens, for example BLR.')
    if br and code != br.code and branch_used(br):
        err('IMMUTABLE', 'The branch code cannot change once employees or meetings use this branch.')
    if Branch.objects.filter(code=code).exclude(pk=getattr(br, 'pk', None)).exists():
        invalid(code='Branch code already used (codes are never reused, even for inactive branches).')
    if Branch.objects.filter(name__iexact=name, status='active').exclude(pk=getattr(br, 'pk', None)).exists():
        invalid(name='Another active branch has this name.')
    email = text(b, 'email', 120)
    if email and not EMAIL_RE.match(email):
        invalid(email='Enter a valid e-mail address.')
    phone = text(b, 'phone', 20)
    if phone and not re.match(r'^\+?[0-9 ()-]{6,20}$', phone):
        invalid(phone='Enter a valid phone number.')
    ws, we = text(b, 'workStart', 5) or '09:00', text(b, 'workEnd', 5) or '18:00'
    if hm_to_min(ws) is None or hm_to_min(we) is None or hm_to_min(we) <= hm_to_min(ws):
        invalid(workEnd='End of the working day must be after the start.')
    days = b.get('workDays')
    if not isinstance(days, list) or not days or any(not isinstance(x, int) or x < 0 or x > 6 for x in days):
        invalid(workDays='Choose at least one working day.')
    tzname = text(b, 'tz', 40) or settings.TIME_ZONE
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tzname)
    except Exception:
        invalid(tz='Unknown time zone.')
    mgr = text(b, 'manager', 60)
    if mgr and not Record.objects.filter(coll='employees', rid=mgr).exists():
        invalid(manager='That employee does not exist.')
    if not br:
        lim = plan_branch_limit()
        if lim is not None and Branch.objects.filter(status='active').count() >= lim:
            err('PLAN_LIMIT', 'Your plan allows %d branch%s. Upgrade to add more.' % (lim, '' if lim == 1 else 'es'))
    before = bdict(br, full=True) if br else None
    br = br or Branch()
    br.name, br.code, br.email, br.phone, br.timezone, br.manager_emp = name, code, email, phone, tzname, mgr
    br.address1, br.address2 = text(b, 'address1', 120), text(b, 'address2', 120)
    br.city, br.state, br.postal_code = text(b, 'city', 60), text(b, 'state', 60), text(b, 'postal', 10)
    br.country = (text(b, 'country', 2) or 'IN').upper()
    br.work_start, br.work_end, br.work_days = ws, we, sorted(set(days))
    br.break_min = int_in(b, 'breakMin', 0, 240, 60, 'Break')
    if not Branch.objects.filter(is_head_office=True).exclude(pk=br.pk).exists():
        br.is_head_office = True
    br.save()
    audit.record(u, 'branch.update' if before else 'branch.create', 'branch', br.pk, before=before, after=bdict(br, full=True), branch_id=br.pk, request=request)
    return {'branch': bdict(br, headcounts(), full=True)}


def deactivation_blockers(br):
    bl = {}
    n = sum(1 for r in Record.objects.filter(coll='employees', data__branchId=str(br.pk)) if r.data.get('status', 'Active') == 'Active')
    if n:
        bl['employees'] = n
    n = Transfer.objects.filter(to_branch=br, status='scheduled').count()
    if n:
        bl['scheduledTransfers'] = n
    n = Meeting.objects.filter(room__branch=br, status='scheduled', start_at__gte=now()).count()
    if n:
        bl['futureRoomBookings'] = n
    return bl


@action('branches.deactivate', module='branches')
def a_deactivate(request, u, b):
    need(u, 'branch.manage')
    br = _get_scoped(u, b.get('id'))
    reason = text(b, 'reason', 300, req=True, mn=3, label='Reason')
    if br.is_head_office:
        err('BRANCH_IN_USE', 'The head office cannot be deactivated. Make another branch the head office first.', blockers={'headOffice': 1})
    bl = deactivation_blockers(br)
    if bl:
        err('BRANCH_IN_USE', 'Move these first: ' + ', '.join('%s %s' % (v, k) for k, v in bl.items()) + '.', blockers=bl)
    br.status, br.deactivated_at, br.deactivated_reason = 'inactive', now(), reason
    br.save()
    audit.record(u, 'branch.deactivate', 'branch', br.pk, after={'reason': reason}, branch_id=br.pk, request=request)
    return {'branch': bdict(br, full=True)}


@action('branches.reactivate', module='branches')
def a_reactivate(request, u, b):
    need(u, 'branch.manage')
    br = _get_scoped(u, b.get('id'))
    lim = plan_branch_limit()
    if lim is not None and Branch.objects.filter(status='active').count() >= lim:
        err('PLAN_LIMIT', 'Your plan allows %d branches.' % lim)
    br.status, br.deactivated_at, br.deactivated_reason = 'active', None, ''
    br.save()
    audit.record(u, 'branch.reactivate', 'branch', br.pk, branch_id=br.pk, request=request)
    return {'branch': bdict(br, full=True)}


@action('branches.setHeadOffice', module='branches')
def a_set_ho(request, u, b):
    need(u, 'branch.manage')
    br = _get_scoped(u, b.get('id'))
    if br.status != 'active':
        invalid(id='Only an active branch can be the head office.')
    Branch.objects.filter(is_head_office=True).update(is_head_office=False)
    br.is_head_office = True
    br.save()
    audit.record(u, 'branch.head_office', 'branch', br.pk, branch_id=br.pk, request=request)
    return {'branch': bdict(br, full=True)}


@action('branches.admins.set', module='branches')
def a_admins(request, u, b):
    """Give users branch scope. Their role must already be HR admin, Branch admin or Branch manager (set on the Users page)."""
    need(u, 'branch.admins')
    br = _get_scoped(u, b.get('branchId'))
    ids = [str(x) for x in (b.get('userIds') or []) if str(x).isdigit()]
    from django.contrib.auth.models import User
    users = list(User.objects.filter(pk__in=ids))
    for x in users:
        if V.role_of(x) not in ('hradmin', 'branchadmin', 'branchmgr'):
            invalid(userIds='%s must have the HR admin, Branch admin or Branch manager role first (Users page).' % x.username)
    before = sorted(UserBranchScope.objects.filter(branch=br).values_list('user_id', flat=True))
    UserBranchScope.objects.filter(branch=br).exclude(user__in=users).delete()
    for x in users:
        UserBranchScope.objects.get_or_create(user=x, branch=br)
    for pk in set(before) ^ set(int(i) for i in ids):
        V.revoke_sessions(pk)
    audit.record(u, 'branch.admins', 'branch', br.pk, before={'users': before}, after={'users': sorted(int(i) for i in ids)}, branch_id=br.pk, request=request)
    return {'ok': True}


def set_user_branches(actor, target, branch_ids):
    """Used by user_save for scoped roles. The actor may only grant branches inside their own scope."""
    s = scope_for(actor)
    ids = [str(x) for x in branch_ids if str(x).isdigit()]
    if not ids:
        V.fail('Choose at least one branch for this role.')
    for i in ids:
        br = branch_by_id(i)
        if not br or br.status != 'active' or not s.sees_branch(i):
            V.fail('You cannot give access to that branch.', 403)
    UserBranchScope.objects.filter(user=target).exclude(branch_id__in=[int(i) for i in ids]).delete()
    for i in ids:
        UserBranchScope.objects.get_or_create(user=target, branch_id=int(i))
    reset_scope(target)


@action('branches.manager.set', module='branches')
def a_manager(request, u, b):
    need(u, 'emp.edit')
    br = _get_scoped(u, b.get('branchId'))
    eid = text(b, 'empId', 60)
    if eid and not Record.objects.filter(coll='employees', rid=eid).exists():
        invalid(empId='That employee does not exist.')
    old = br.manager_emp
    br.manager_emp = eid
    br.save(update_fields=['manager_emp'])
    granted = False
    if eid and b.get('grantRole'):
        from core.models import Profile
        p = Profile.objects.filter(emp_id=eid).select_related('user').first()
        if p and p.role in ('employee', 'manager', 'branchmgr'):
            p.role = 'branchmgr'
            p.save(update_fields=['role'])
            UserBranchScope.objects.get_or_create(user=p.user, branch=br)
            V.revoke_sessions(p.user_id)
            granted = True
    audit.record(u, 'branch.manager', 'branch', br.pk, before={'manager': old}, after={'manager': eid, 'roleGranted': granted}, branch_id=br.pk, request=request)
    return {'granted': granted}


@action('branches.overview', module='branches', read=True)
def a_overview(request, u, b):
    need(u, 'report.consolidated')
    s = scope_for(u)
    today = datetime.date.today().isoformat()
    emps = emp_map()
    att = {a.eid: a.data for a in V.Attendance.objects.filter(day=today)}
    from core.models import NotificationDelivery
    since = now() - datetime.timedelta(hours=24)
    out = []
    for br in Branch.objects.filter(status='active'):
        if not s.sees_branch(br.pk):
            continue
        ids = [k for k, e in emps.items() if str(e.get('branchId')) == str(br.pk) and e.get('status', 'Active') == 'Active' and not e.get('core')]
        st = [att.get(i, {}).get('s') for i in ids]
        open_tasks = sum(1 for r in Record.objects.filter(coll='tasks') if r.data.get('eid') in ids and r.data.get('status') != 'done')
        out.append({'id': str(br.pk), 'code': br.code, 'name': br.name, 'headcount': len(ids), 'present': sum(1 for x in st if x in ('P', 'L', 'W')),
                    'late': st.count('L'), 'onLeave': st.count('V'), 'openTasks': open_tasks,
                    'meetingsToday': Meeting.objects.filter(branch=br, status__in=['scheduled', 'completed'], start_at__date=datetime.date.today()).count(),
                    'notifyFailed': NotificationDelivery.objects.filter(branch=br, status__in=['failed', 'failed_perm'], queued_at__gte=since).count()})
    return {'items': out}


# ---------- employee assignments ----------
def adict(a):
    return {'id': str(a.pk), 'branchId': str(a.branch_id), 'branch': a.branch.name, 'code': a.branch.code, 'kind': a.kind,
            'from': a.effective_from.isoformat(), 'to': a.effective_to.isoformat() if a.effective_to else '', 'dept': a.dept,
            'designation': a.designation, 'reportsTo': a.reports_to, 'reason': a.reason, 'status': a.status}


def _emp_in_scope(u, eid, primary=True):
    if not eid or not Record.objects.filter(coll='employees', rid=eid).exists() or not scope_for(u).sees(eid, primary_only=primary):
        err('NOT_FOUND', 'Employee not found.')


@action('emp.assignments', module='branches', read=True)
def a_assignments(request, u, b):
    eid = str(b.get('empId') or '')
    if not (scope_for(u).me == eid or can(u, 'branch.view')):
        err('FORBIDDEN', 'You do not have permission for this.')
    _emp_in_scope(u, eid, primary=False)
    rows = A.objects.filter(emp_id=eid).exclude(status='cancelled').select_related('branch').order_by('-effective_from', '-id')
    tr = Transfer.objects.filter(emp_id=eid).select_related('to_branch', 'from_branch').order_by('-created_at')
    return {'items': [adict(a) for a in rows],
            'transfers': [{'id': str(t.pk), 'from': t.from_branch.name, 'to': t.to_branch.name, 'effective': t.effective_date.isoformat(), 'status': t.status,
                           'reason': t.reason, 'by': t.created_by.username if t.created_by else ''} for t in tr]}


@action('emp.secondary.save', module='branches')
def a_secondary(request, u, b):
    need(u, 'emp.edit')
    eid = str(b.get('empId') or '')
    _emp_in_scope(u, eid)
    br = branch_by_id(b.get('branchId'))
    if not br or br.status != 'active' or not scope_for(u).sees_branch(br.pk):
        invalid(branchId='Choose an active branch you manage.')
    if str(br.pk) == str(branch_of_emp(eid).pk):
        invalid(branchId='This is already the employee\'s main branch.')
    f, t = parse_date(b.get('from')) or datetime.date.today(), parse_date(b.get('to'))
    if t and t < f:
        invalid(to='The end date must be after the start date.')
    reason = text(b, 'reason', 300, req=True, mn=3, label='Reason')
    if A.objects.filter(emp_id=eid, kind='secondary', status='active', branch=br).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=f)).exists():
        err('OVERLAP', 'This employee already has access to that branch for these dates.')
    a = A.objects.create(emp_id=eid, branch=br, kind='secondary', effective_from=f, effective_to=t, reason=reason)
    audit.record(u, 'emp.secondary.add', 'employee', eid, after=adict(a), branch_id=br.pk, request=request)
    return {'assignment': adict(a)}


@action('emp.secondary.end', module='branches')
def a_secondary_end(request, u, b):
    need(u, 'emp.edit')
    a = A.objects.filter(pk=int(b.get('id')) if str(b.get('id') or '').isdigit() else 0, kind='secondary').select_related('branch').first()
    if not a or not scope_for(u).sees_branch(a.branch_id):
        err('NOT_FOUND', 'Not found.')
    a.effective_to = parse_date(b.get('to')) or datetime.date.today()
    a.status = 'ended' if a.effective_to <= datetime.date.today() else 'active'
    a.save()
    audit.record(u, 'emp.secondary.end', 'employee', a.emp_id, after=adict(a), branch_id=a.branch_id, request=request)
    return {'assignment': adict(a)}


# ---------- transfers ----------
def _transfer_input(u, b):
    eid = str(b.get('empId') or '')
    _emp_in_scope(u, eid)
    e = Record.objects.get(coll='employees', rid=eid).data
    if e.get('status', 'Active') != 'Active':
        invalid(empId='Only active employees can be transferred.')
    to = branch_by_id(b.get('toBranchId'))
    if not to:
        invalid(toBranchId='Choose the new branch.')
    if to.status != 'active':
        err('VALIDATION', 'That branch is not active.')
    frm = branch_of_emp(eid, e)
    s = scope_for(u)
    if not (s.sees_branch(frm.pk) and s.sees_branch(to.pk)):
        err('FORBIDDEN', 'You can only transfer between branches you are responsible for. Ask a super admin.')
    if to.pk == frm.pk:
        invalid(toBranchId='The employee already works in this branch.')
    eff = parse_date(b.get('effectiveDate'))
    today = datetime.date.today()
    if not eff:
        invalid(effectiveDate='Choose the date the transfer starts.')
    if eff < today.replace(day=1):
        invalid(effectiveDate='A transfer can be back-dated only to the start of this month.')
    if eff > today + datetime.timedelta(days=180):
        invalid(effectiveDate='A transfer can be scheduled at most 180 days ahead.')
    cur = A.objects.filter(emp_id=eid, kind='primary', status='active', effective_to__isnull=True).first()
    if cur and eff <= cur.effective_from and cur.reason != 'Initial assignment':
        invalid(effectiveDate='The date must be after the employee\'s previous transfer (%s).' % cur.effective_from.isoformat())
    return eid, e, frm, to, eff


@action('transfers.preview', module='branches')
def a_tpreview(request, u, b):
    need(u, 'emp.transfer')
    eid, e, frm, to, eff = _transfer_input(u, b)
    tasks = [{'id': r.rid, 'title': r.data.get('title', ''), 'due': r.data.get('due', '')} for r in Record.objects.filter(coll='tasks') if r.data.get('eid') == eid and r.data.get('status') != 'done']
    future = Meeting.objects.filter(participants__emp_id=eid, status='scheduled', start_at__gte=now()).distinct().count()
    leaves = sum(1 for r in Record.objects.filter(coll='leaves') if r.data.get('eid') == eid and r.data.get('status') == 'Pending')
    from core.models import WorkSession
    return {'from': bdict(frm), 'to': bdict(to), 'openTasks': tasks, 'futureMeetings': future, 'pendingLeave': leaves,
            'pendingTime': WorkSession.objects.filter(emp_id=eid, approval='pending').count(),
            'runningTimers': WorkSession.objects.filter(emp_id=eid, status__in=['running', 'paused']).count(),
            'applyNow': eff <= datetime.date.today()}


@action('transfers.create', module='branches')
def a_tcreate(request, u, b):
    need(u, 'emp.transfer')
    eid, e, frm, to, eff = _transfer_input(u, b)
    if Transfer.objects.filter(emp_id=eid, status='scheduled').exists():
        err('TRANSFER_EXISTS', 'This employee already has a scheduled transfer. Cancel it first.')
    reason = text(b, 'reason', 300, req=True, mn=3, label='Reason')
    rt = text(b, 'reportsTo', 60)
    if rt and not Record.objects.filter(coll='employees', rid=rt).exists():
        invalid(reportsTo='That manager does not exist.')
    reassign = []
    for r in b.get('reassign') or []:
        if isinstance(r, dict) and r.get('taskId') and r.get('toEmpId'):
            if not scope_for(u).sees(str(r['toEmpId'])):
                invalid(reassign='You cannot assign tasks to that person.')
            reassign.append({'taskId': str(r['taskId']), 'toEmpId': str(r['toEmpId'])})
    t = Transfer.objects.create(emp_id=eid, from_branch=frm, to_branch=to, effective_date=eff, new_dept=text(b, 'dept', 80),
                                new_designation=text(b, 'designation', 80), new_reports_to=rt, reason=reason, notes=text(b, 'notes', 500),
                                side_effects={'reassign': reassign}, created_by=u)
    audit.record(u, 'transfer.create', 'employee', eid, after={'to': to.code, 'from': frm.code, 'effective': eff.isoformat(), 'reason': reason}, branch_id=to.pk, request=request)
    if eff <= datetime.date.today():
        apply_transfer(t, u)
    else:
        jobs.enqueue('branches.apply_transfers', {}, run_at=datetime.datetime.combine(eff, datetime.time(0, 1), tzinfo=tz(to.timezone)),
                     unique_key='transfer:%s' % t.pk)
    t.refresh_from_db()
    return {'transfer': {'id': str(t.pk), 'status': t.status, 'effective': eff.isoformat()}}


@action('transfers.cancel', module='branches')
def a_tcancel(request, u, b):
    need(u, 'emp.transfer')
    t = Transfer.objects.filter(pk=int(b.get('id')) if str(b.get('id') or '').isdigit() else 0).first()
    if not t or not scope_for(u).sees_branch(t.to_branch_id):
        err('NOT_FOUND', 'Transfer not found.')
    if t.status != 'scheduled':
        err('INVALID_STATE', 'Only a scheduled transfer can be cancelled.')
    t.status = 'cancelled'
    t.save(update_fields=['status'])
    jobs.cancel('transfer:%s' % t.pk)
    audit.record(u, 'transfer.cancel', 'employee', t.emp_id, branch_id=t.to_branch_id, request=request)
    return {'ok': True}


@action('transfers.list', module='branches', read=True)
def a_tlist(request, u, b):
    need(u, 'emp.transfer')
    s = scope_for(u)
    q = Transfer.objects.select_related('to_branch', 'from_branch', 'created_by').order_by('-created_at')
    if not s.all:
        ids = [int(x) for x in s.branches if x.isdigit()]
        q = q.filter(Q(to_branch_id__in=ids) | Q(from_branch_id__in=ids))
    em = emp_map()
    return paged(q, b, lambda t: {'id': str(t.pk), 'empId': t.emp_id, 'name': (em.get(t.emp_id) or {}).get('name', ''), 'from': t.from_branch.name,
                                  'to': t.to_branch.name, 'effective': t.effective_date.isoformat(), 'status': t.status, 'reason': t.reason,
                                  'by': t.created_by.username if t.created_by else ''})


# ---------- departments / designations ----------
@action('org.departments.list', module='branches', read=True)
def a_depts(request, u, b):
    c = {}
    for r in Record.objects.filter(coll='employees'):
        k = str(r.data.get('dept') or '')
        c[k] = c.get(k, 0) + 1
    return {'items': [{'id': str(d.pk), 'name': d.name, 'code': d.code, 'status': d.status, 'employees': c.get(d.name, 0)} for d in Department.objects.all()]}


@action('org.departments.save', module='branches')
def a_dept_save(request, u, b):
    need(u, 'org.master')
    name = text(b, 'name', 80, req=True, mn=2, label='Department name')
    d = Department.objects.filter(pk=int(b['id'])).first() if str(b.get('id') or '').isdigit() else None
    if Department.objects.filter(name__iexact=name).exclude(pk=getattr(d, 'pk', None)).exists():
        invalid(name='A department with this name exists. Use Merge instead.')
    affected = 0
    with transaction.atomic():
        if d and d.name != name:
            for r in Record.objects.filter(coll='employees'):
                if r.data.get('dept') == d.name:
                    r.data = dict(r.data, dept=name)
                    r.save(update_fields=['data'])
                    affected += 1
            bump_rev()
        d = d or Department()
        d.name, d.code = name, text(b, 'code', 10)
        d.status = 'inactive' if b.get('status') == 'inactive' else 'active'
        d.save()
    audit.record(u, 'org.department', 'department', d.pk, after={'name': name, 'affected': affected}, request=request)
    return {'id': str(d.pk), 'affected': affected}


@action('org.departments.merge', module='branches')
def a_dept_merge(request, u, b):
    need(u, 'org.master')
    to = Department.objects.filter(pk=int(b.get('toId'))).first() if str(b.get('toId') or '').isdigit() else None
    frm = list(Department.objects.filter(pk__in=[int(x) for x in b.get('fromIds') or [] if str(x).isdigit()]).exclude(pk=getattr(to, 'pk', None)))
    if not to or not frm:
        invalid(toId='Choose the departments to merge.')
    names = {d.name for d in frm}
    n = 0
    for r in Record.objects.filter(coll='employees'):
        if r.data.get('dept') in names:
            r.data = dict(r.data, dept=to.name)
            r.save(update_fields=['data'])
            n += 1
    for d in frm:
        BranchDepartment.objects.filter(department=d).exclude(branch__departments__department=to).update(department=to)
        d.delete()
    bump_rev()
    audit.record(u, 'org.department.merge', 'department', to.pk, after={'merged': sorted(names), 'affected': n}, request=request)
    return {'affected': n}


@action('org.branchDepartments.set', module='branches')
def a_bdepts(request, u, b):
    need(u, 'org.master')
    br = _get_scoped(u, b.get('branchId'))
    rows = [x for x in (b.get('departments') or []) if isinstance(x, dict) and str(x.get('id') or '').isdigit()]
    BranchDepartment.objects.filter(branch=br).exclude(department_id__in=[int(x['id']) for x in rows]).delete()
    for x in rows:
        BranchDepartment.objects.update_or_create(branch=br, department_id=int(x['id']), defaults={'head_emp': str(x.get('head') or '')[:60]})
    return {'ok': True}


@action('org.designations.list', module='branches', read=True)
def a_desigs(request, u, b):
    return {'items': [{'id': str(d.pk), 'name': d.name, 'dept': d.department.name if d.department else '', 'status': d.status} for d in Designation.objects.select_related('department')]}


@action('org.designations.save', module='branches')
def a_desig_save(request, u, b):
    need(u, 'org.master')
    name = text(b, 'name', 80, req=True, mn=2, label='Designation')
    d = Designation.objects.filter(pk=int(b['id'])).first() if str(b.get('id') or '').isdigit() else None
    if Designation.objects.filter(name__iexact=name).exclude(pk=getattr(d, 'pk', None)).exists():
        invalid(name='This designation exists already.')
    d = d or Designation()
    d.name = name
    d.department = Department.objects.filter(pk=int(b['deptId'])).first() if str(b.get('deptId') or '').isdigit() else None
    d.status = 'inactive' if b.get('status') == 'inactive' else 'active'
    d.save()
    return {'id': str(d.pk)}


# ---------- rooms ----------
def room_dict(r):
    return {'id': str(r.pk), 'branchId': str(r.branch_id), 'name': r.name, 'capacity': r.capacity, 'facilities': r.facilities, 'status': r.status}


@action('rooms.list', read=True)
def a_rooms(request, u, b):
    q = MeetingRoom.objects.filter(branch__status='active')
    if b.get('branchId'):
        q = q.filter(branch_id=int(b['branchId']) if str(b['branchId']).isdigit() else 0)
    return {'items': [room_dict(r) for r in q]}


@action('rooms.save', module='meetings')
def a_room_save(request, u, b):
    need(u, 'room.manage')
    br = _get_scoped(u, b.get('branchId'))
    r = MeetingRoom.objects.filter(pk=int(b['id']), branch=br).first() if str(b.get('id') or '').isdigit() else None
    name = text(b, 'name', 60, req=True, label='Room name')
    if MeetingRoom.objects.filter(branch=br, name__iexact=name).exclude(pk=getattr(r, 'pk', None)).exists():
        invalid(name='This branch already has a room with this name.')
    st = 'inactive' if b.get('status') == 'inactive' else 'active'
    if r and st == 'inactive' and Meeting.objects.filter(room=r, status='scheduled', start_at__gte=now()).exists():
        err('BRANCH_IN_USE', 'Move the future meetings booked in this room first.')
    r = r or MeetingRoom(branch=br)
    r.name, r.capacity, r.status = name, int_in(b, 'capacity', 1, 500, 6, 'Capacity'), st
    r.facilities = [str(x)[:30] for x in (b.get('facilities') or [])][:10]
    r.save()
    return {'room': room_dict(r)}
