"""Branch-level data isolation. Every legacy and v6 read/write path asks this module who the user may see.

Scope.all      -> company-wide (super admin, company admin)
Scope.primary  -> employee ids whose PRIMARY branch is in the user's branches (attendance, leave, payroll...)
Scope.emps     -> primary + employees with SECONDARY access to those branches (tasks, meetings, pickers)
Scope.branches -> branch ids (strings) the user may act in
"""
import datetime

from django.db.models import Q

from core import views as V


class Scope:
    def __init__(self, all_=False, branches=(), primary=(), secondary=(), me=''):
        self.all = all_
        self.branches = set(str(b) for b in branches)
        self.primary = set(primary)
        self.emps = set(primary) | set(secondary)
        self.me = me

    def sees(self, eid, primary_only=False):
        if self.all:
            return True
        return eid in (self.primary if primary_only else self.emps)

    def sees_branch(self, bid):
        return self.all or (bid not in (None, '') and str(bid) in self.branches)


def emps_with_primary(branch_ids):
    from core.models import Record
    ids = [str(b) for b in branch_ids]
    if not ids:
        return set()
    return {r.rid for r in Record.objects.filter(coll='employees', data__branchId__in=ids)}


def emps_with_secondary(branch_ids, on=None):
    from core.models import EmployeeBranchAssignment as A
    on = on or datetime.date.today()
    ids = [int(b) for b in branch_ids if str(b).isdigit()]
    if not ids:
        return set()
    q = A.objects.filter(kind='secondary', status='active', branch_id__in=ids, effective_from__lte=on).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on))
    return set(q.values_list('emp_id', flat=True))


def user_branches(u):
    from core.models import UserBranchScope
    return set(str(x) for x in UserBranchScope.objects.filter(user=u).values_list('branch_id', flat=True))


def own_branches(me):
    """An employee's primary branch plus active secondary branches."""
    from core.models import EmployeeBranchAssignment as A
    out = set()
    e = V.Record.objects.filter(coll='employees', rid=me).first() if me else None
    if e and e.data.get('branchId'):
        out.add(str(e.data['branchId']))
    if me:
        t = datetime.date.today()
        for b in A.objects.filter(emp_id=me, kind='secondary', status='active', effective_from__lte=t).filter(Q(effective_to__isnull=True) | Q(effective_to__gte=t)).values_list('branch_id', flat=True):
            out.add(str(b))
    return out


def scope_for(u):
    cached = getattr(u, '_v6_scope', None)
    if cached is not None:
        return cached
    role = V.role_of(u)
    me = V.prof(u).emp_id
    if role in ('superadmin', 'admin'):
        s = Scope(all_=True, me=me)
    elif role in ('hradmin', 'branchadmin'):
        b = user_branches(u)
        s = Scope(branches=b, primary=emps_with_primary(b), secondary=emps_with_secondary(b), me=me)
        if me:
            s.primary.add(me)
            s.emps.add(me)
    elif role == 'branchmgr':
        b = user_branches(u)
        team = set(V.team_of(me))
        s = Scope(branches=b, primary=emps_with_primary(b) | team | ({me} if me else set()), secondary=emps_with_secondary(b), me=me)
    elif role == 'manager':
        team = set(V.team_of(me))
        s = Scope(branches=own_branches(me), primary=team | ({me} if me else set()), me=me)
    else:
        s = Scope(branches=own_branches(me), primary={me} if me else set(), me=me)
    try:
        u._v6_scope = s
    except Exception:
        pass
    return s


def reset(u):
    if hasattr(u, '_v6_scope'):
        del u._v6_scope


def picker_emps(u):
    """Employees this user may invite to meetings / assign. Employees: anyone in their own branches."""
    s = scope_for(u)
    if s.all:
        return None  # everyone
    if V.role_of(u) == 'employee' or (V.role_of(u) == 'manager'):
        b = own_branches(s.me)
        return emps_with_primary(b) | emps_with_secondary(b) | s.emps
    return s.emps
