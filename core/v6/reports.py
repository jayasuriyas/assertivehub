"""Reports (docs/release-v6/04-specifications.md section 6). Server-side, scoped to the viewer, paged, sortable,
CSV export with formula-injection protection. Task time is always reported separately from attendance hours."""
import csv
import datetime
import io

from django.http import HttpResponse
from django.db.models import Q

from core import views as V
from core.models import (Attendance, Branch, EmployeeBranchAssignment as A, Meeting, MeetingActionItem, NotificationDelivery as D, Record, TimeAdjustment,
                         WorkSession, WorkSessionEvent)
from .base import action, err, invalid, need, can, parse_date, now, iso, local, tz, emp_map, ALL_ADMIN
from .scope import scope_for
from .worktime import live, metrics, totals, COUNTED

CATALOG = {
    'R-01': ('Employee-wise work time', 'time.view_scope', True), 'R-02': ('Task-wise work time', 'time.view_scope', False),
    'R-03': ('Department-wise work time', 'time.view_scope', False), 'R-04': ('Branch-wise work time', 'report.consolidated', False),
    'R-05': ('Daily work time', 'time.view_scope', True), 'R-06': ('Weekly work time', 'time.view_scope', True), 'R-07': ('Monthly work time', 'time.view_scope', True),
    'R-08': ('Estimated vs actual', 'time.view_scope', False), 'R-09': ('Overdue tasks', 'time.view_scope', False),
    'R-10': ('Tasks exceeding estimate', 'time.view_scope', False), 'R-11': ('Manual time adjustments', 'time.view_scope', False),
    'R-12': ('Branch comparison', 'report.consolidated', False), 'R-13': ('Meeting summary', 'meeting.view_scope', False),
    'R-14': ('Meeting attendance', 'meeting.view_scope', False), 'R-15': ('Meeting action items', 'meeting.view_scope', False),
    'R-16': ('Notification delivery', 'notify.history', False), 'R-18': ('Interruption reasons', 'time.view_scope', False),
}
MODULE = {k: ('meetings' if k in ('R-13', 'R-14', 'R-15') else 'notify' if k == 'R-16' else 'branches' if k == 'R-12' else 'timetrack') for k in CATALOG}


def safe(v):
    s = '' if v is None else str(v)
    return "'" + s if s[:1] in ('=', '+', '-', '@', '\t', '\r') else s


def csv_response(name, header, rows):
    buf = io.StringIO()
    buf.write('﻿')
    w = csv.writer(buf)
    w.writerow([safe(h) for h in header])
    for r in rows:
        w.writerow([safe(x) for x in r])
    resp = HttpResponse(buf.getvalue(), content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = 'attachment; filename="%s"' % name
    return resp


def hrs(sec):
    return round((sec or 0) / 3600, 2)


# ---------- day-split attribution (04 section 3.6) ----------
_END = ('pause', 'stop', 'auto_stop', 'complete', 'switch', 'reassigned_stop', 'logout_pause')


def intervals(s, at=None):
    """Work intervals of a session (pauses excluded)."""
    if s.kind == 'manual' or s.events.filter(type='adjust_applied').exists():
        w = live(s, at)[0]
        return [(s.started_at, s.started_at + datetime.timedelta(seconds=w))] if w else []
    out, cur = [], None
    for e in s.events.all():
        if e.type in ('start', 'resume'):
            cur = e.at if cur is None else cur
        elif e.type in _END and cur is not None:
            out.append((cur, e.at))
            cur = None
    if cur is not None and s.status == 'running':
        out.append((cur, at or now()))
    return out


def split_days(ivs, tzname):
    z = tz(tzname)
    res = {}
    for a, b in ivs:
        x = a
        while x < b:
            lx = x.astimezone(z)
            nxt = datetime.datetime.combine(lx.date() + datetime.timedelta(days=1), datetime.time(0), tzinfo=z).astimezone(datetime.timezone.utc)
            y = min(b, nxt)
            res[lx.date()] = res.get(lx.date(), 0) + int((y - x).total_seconds())
            x = y
    return res


def session_days(s, tzname):
    return split_days(intervals(s), tzname)


# ---------- helpers ----------
class Ctx:
    def __init__(self, u, b):
        self.u, self.b = u, b
        self.s = scope_for(u)
        today = datetime.date.today()
        self.d0 = parse_date(b.get('from')) or today.replace(day=1)
        self.d1 = parse_date(b.get('to')) or today
        if self.d1 < self.d0:
            invalid(to='The end date must be after the start date.')
        if (self.d1 - self.d0).days > 366:
            invalid(to='Choose at most one year.')
        self.em = emp_map()
        self.branch = str(b.get('branchId') or '')
        self.dept = str(b.get('dept') or '')
        self.emp = str(b.get('empId') or '')
        self.cat = str(b.get('category') or '')
        self.self_only = not can(u, CATALOG.get(b.get('report'), ('', 'time.view_scope'))[1])
        self.branches = {str(x.pk): x for x in Branch.objects.all()}

    def emp_ok(self, eid, primary=False):
        if self.self_only and eid != self.s.me:
            return False
        if not (self.s.sees(eid, primary_only=primary) or eid == self.s.me):
            return False
        e = self.em.get(eid) or {}
        if self.emp and eid != self.emp:
            return False
        if self.dept and e.get('dept') != self.dept:
            return False
        if self.branch and str(e.get('branchId', '')) != self.branch:
            return False
        return True

    def tzname(self, eid):
        b = self.branches.get(str((self.em.get(eid) or {}).get('branchId', '')))
        return b.timezone if b else None

    def name(self, eid):
        return (self.em.get(eid) or {}).get('name', eid)

    def sessions(self):
        q = WorkSession.objects.filter(COUNTED, work_date__gte=self.d0 - datetime.timedelta(days=1), work_date__lte=self.d1).prefetch_related('events')
        if self.branch:
            q = q.filter(branch_id=int(self.branch) if self.branch.isdigit() else 0)
        out = []
        for x in q:
            if not (self.s.sees(x.emp_id) or x.emp_id == self.s.me) or (self.self_only and x.emp_id != self.s.me):
                continue
            if self.emp and x.emp_id != self.emp:
                continue
            if self.dept and x.dept != self.dept:
                continue
            out.append(x)
        return out

    def in_range(self, days):
        return {d: v for d, v in days.items() if self.d0 <= d <= self.d1}


def tasks_in_scope(c, pred=None):
    out = []
    for r in Record.objects.filter(coll='tasks'):
        d = r.data
        if not c.emp_ok(d.get('eid', '')):
            continue
        if c.cat and d.get('cat') != c.cat:
            continue
        if pred and not pred(d):
            continue
        out.append(r)
    return out


def att_seconds(eid, d0, d1):
    from core.punch import mins
    tot = 0
    for a in Attendance.objects.filter(eid=eid, day__gte=d0.isoformat(), day__lte=d1.isoformat()):
        for p in a.data.get('p') or []:
            if len(p) > 1 and mins(p[0]) is not None and mins(p[1]) is not None:
                tot += max(0, mins(p[1]) - mins(p[0])) * 60
    return tot


# ---------- reports ----------
def r01(c):
    agg = {}
    for s in c.sessions():
        dd = c.in_range(session_days(s, c.tzname(s.emp_id)))
        if not dd:
            continue
        a = agg.setdefault(s.emp_id, {'tasks': set(), 'sessions': 0, 'timer': 0, 'manual': 0})
        a['tasks'].add(s.task_rid)
        sec = sum(dd.values())
        if s.kind == 'manual':
            a['manual'] += sec
        else:
            a['timer'] += sec
            a['sessions'] += 1 if sec >= 10 else 0
    pend = {}
    for s in WorkSession.objects.filter(approval='pending', work_date__gte=c.d0, work_date__lte=c.d1):
        pend[s.emp_id] = pend.get(s.emp_id, 0) + s.work_seconds
    ids = [k for k in c.em if c.emp_ok(k) and c.em[k].get('status', 'Active') == 'Active' and (k in agg or k in pend)]
    rows = []
    for k in ids:
        a = agg.get(k, {'tasks': set(), 'sessions': 0, 'timer': 0, 'manual': 0})
        tt = a['timer'] + a['manual']
        at = att_seconds(k, c.d0, c.d1)
        rows.append({'empId': k, 'employee': c.name(k), 'branch': (c.branches.get(str(c.em[k].get('branchId', ''))) or Branch(name='')).name, 'dept': c.em[k].get('dept', ''),
                     'tasks': len(a['tasks']), 'sessions': a['sessions'], 'taskHours': hrs(tt), 'manualHours': hrs(a['manual']), 'pendingHours': hrs(pend.get(k, 0)),
                     'attendanceHours': hrs(at), 'taskPctOfAttendance': round(tt / at * 100, 1) if at else None})
    cols = [('employee', 'Employee'), ('branch', 'Branch'), ('dept', 'Department'), ('tasks', 'Tasks worked'), ('sessions', 'Sessions'), ('taskHours', 'Task time (h)'),
            ('manualHours', 'Of which manual (h)'), ('pendingHours', 'Pending approval (h)'), ('attendanceHours', 'Attendance hours (h)'), ('taskPctOfAttendance', 'Task time / attendance %')]
    return cols, rows, 'taskHours'


def r02(c):
    ids = set(s.task_rid for s in c.sessions() if c.in_range(session_days(s, c.tzname(s.emp_id))))
    recs = [r for r in Record.objects.filter(coll='tasks', rid__in=list(ids)) if not c.cat or r.data.get('cat') == c.cat]
    tt = totals([r.rid for r in recs])
    first_last = {}
    for s in WorkSession.objects.filter(task_rid__in=[r.rid for r in recs]).filter(COUNTED):
        fl = first_last.setdefault(s.task_rid, [s.started_at, s.ended_at or now()])
        fl[0] = min(fl[0], s.started_at)
        fl[1] = max(fl[1], s.ended_at or now())
    rows = []
    for r in recs:
        d, t = r.data, tt.get(r.rid, {'actual': 0, 'pending': 0, 'sessions': 0})
        m = metrics(d.get('est'), t['actual'])
        fl = first_last.get(r.rid, [None, None])
        rows.append({'taskId': r.rid, 'task': d.get('title', ''), 'employee': c.name(d.get('eid', '')), 'category': d.get('cat', ''), 'status': d.get('status', ''),
                     'estHours': hrs(m['est']) if m['est'] else None, 'actualHours': hrs(t['actual']), 'pendingHours': hrs(t['pending']),
                     'varianceHours': hrs(m['variance']) if m['variance'] is not None else None, 'variancePct': m['variancePct'], 'efficiency': m['efficiency'],
                     'sessions': t['sessions'], 'firstStart': iso(fl[0]), 'lastStop': iso(fl[1])})
    cols = [('task', 'Task'), ('employee', 'Assignee'), ('category', 'Work type'), ('status', 'Status'), ('estHours', 'Estimated (h)'), ('actualHours', 'Actual (h)'),
            ('pendingHours', 'Pending (h)'), ('varianceHours', 'Variance (h)'), ('variancePct', 'Variance %'), ('efficiency', 'Efficiency %'), ('sessions', 'Sessions'),
            ('firstStart', 'First start'), ('lastStop', 'Last stop')]
    return cols, rows, 'actualHours'


def _group(c, keyfn, label):
    agg = {}
    for s in c.sessions():
        sec = sum(c.in_range(session_days(s, c.tzname(s.emp_id))).values())
        if not sec:
            continue
        k = keyfn(s)
        a = agg.setdefault(k, {'emps': set(), 'sec': 0})
        a['emps'].add(s.emp_id)
        a['sec'] += sec
    done = tasks_in_scope(c, lambda d: d.get('status') == 'done' and d.get('est') and c.d0.isoformat() <= str(d.get('doneOn', '')) <= c.d1.isoformat())
    tt = totals([r.rid for r in done])
    eff = {}
    for r in done:
        e = c.em.get(r.data.get('eid', '')) or {}
        k = keyfn(None, e, r.data)
        x = eff.setdefault(k, [0, 0])
        x[0] += int(r.data['est']) * 60
        x[1] += tt.get(r.rid, {}).get('actual', 0)
    rows = []
    for k in sorted(set(agg) | set(eff)):
        a = agg.get(k, {'emps': set(), 'sec': 0})
        e = eff.get(k, [0, 0])
        rows.append({'group': k or '(none)', 'headcount': len(a['emps']), 'taskHours': hrs(a['sec']), 'avgPerHead': hrs(a['sec'] / len(a['emps'])) if a['emps'] else 0,
                     'estDone': hrs(e[0]), 'actualDone': hrs(e[1]), 'efficiency': round(e[0] / e[1] * 100, 1) if e[1] else None})
    cols = [('group', label), ('headcount', 'People with task time'), ('taskHours', 'Task time (h)'), ('avgPerHead', 'Average per person (h)'),
            ('estDone', 'Estimated, completed tasks (h)'), ('actualDone', 'Actual, completed tasks (h)'), ('efficiency', 'Efficiency %')]
    return cols, rows, 'taskHours'


def r03(c):
    return _group(c, lambda s, e=None, d=None: s.dept if s else (e.get('dept', '') if e else ''), 'Department')


def r04(c):
    bn = lambda bid: (c.branches.get(str(bid)) or Branch(name='')).name
    return _group(c, lambda s, e=None, d=None: bn(s.branch_id) if s else bn(d.get('branchId') or (e or {}).get('branchId', '')), 'Branch')


def _buckets(c, kind):
    def key(d):
        if kind == 'day':
            return d.isoformat()
        if kind == 'week':
            y, w, _ = d.isocalendar()
            return '%d-W%02d' % (y, w)
        return d.strftime('%Y-%m')
    keys, d = [], c.d0
    while d <= c.d1:
        k = key(d)
        if k not in keys:
            keys.append(k)
        d += datetime.timedelta(days=1)
    agg = {}
    for s in c.sessions():
        for dd, sec in c.in_range(session_days(s, c.tzname(s.emp_id))).items():
            a = agg.setdefault(s.emp_id, {})
            a[key(dd)] = a.get(key(dd), 0) + sec
    rows = []
    for eid, a in agg.items():
        row = {'employee': c.name(eid), 'total': hrs(sum(a.values()))}
        for k in keys:
            row[k] = hrs(a.get(k, 0))
        rows.append(row)
    cols = [('employee', 'Employee')] + [(k, k) for k in keys] + [('total', 'Total (h)')]
    return cols, rows, 'total'


def r05(c):
    return _buckets(c, 'day')


def r06(c):
    return _buckets(c, 'week')


def r07(c):
    return _buckets(c, 'month')


def r08(c):
    done = tasks_in_scope(c, lambda d: d.get('status') == 'done' and c.d0.isoformat() <= str(d.get('doneOn', '')) <= c.d1.isoformat())
    tt = totals([r.rid for r in done])
    rows, se, sa, within, noest = [], 0, 0, 0, 0
    for r in done:
        d, a = r.data, tt.get(r.rid, {}).get('actual', 0)
        if not d.get('est'):
            noest += 1
            continue
        m = metrics(d['est'], a)
        if a:
            se += m['est']
            sa += a
            if abs(m['variancePct']) <= 10:
                within += 1
        rows.append({'task': d.get('title', ''), 'employee': c.name(d.get('eid', '')), 'doneOn': d.get('doneOn', ''), 'estHours': hrs(m['est']), 'actualHours': hrs(a),
                     'varianceHours': hrs(m['variance']), 'variancePct': m['variancePct'], 'efficiency': m['efficiency']})
    summary = {'estHours': hrs(se), 'actualHours': hrs(sa), 'efficiency': round(se / sa * 100, 1) if sa else None,
               'withinTenPct': round(within / len(rows) * 100, 1) if rows else None, 'noEstimate': noest, 'tasks': len(rows)}
    cols = [('task', 'Task'), ('employee', 'Assignee'), ('doneOn', 'Completed'), ('estHours', 'Estimated (h)'), ('actualHours', 'Actual (h)'), ('varianceHours', 'Variance (h)'),
            ('variancePct', 'Variance %'), ('efficiency', 'Efficiency %')]
    return cols, rows, 'variancePct', summary


def r09(c):
    today = datetime.date.today().isoformat()
    recs = tasks_in_scope(c, lambda d: d.get('status') != 'done' and d.get('due') and d['due'] < today)
    tt = totals([r.rid for r in recs])
    rows = []
    for r in recs:
        d, a = r.data, tt.get(r.rid, {}).get('actual', 0)
        m = metrics(d.get('est'), a)
        rows.append({'task': d.get('title', ''), 'employee': c.name(d.get('eid', '')), 'due': d['due'], 'daysOverdue': (datetime.date.today() - parse_date(d['due'])).days if parse_date(d['due']) else None,
                     'status': d.get('status', ''), 'estHours': hrs(m['est']) if m['est'] else None, 'actualHours': hrs(a), 'remainingHours': hrs(m['remaining']) if m['remaining'] is not None else None})
    cols = [('task', 'Task'), ('employee', 'Assignee'), ('due', 'Due'), ('daysOverdue', 'Days overdue'), ('status', 'Status'), ('estHours', 'Estimated (h)'), ('actualHours', 'Actual (h)'), ('remainingHours', 'Remaining (h)')]
    return cols, rows, 'daysOverdue'


def r10(c):
    recs = tasks_in_scope(c, lambda d: d.get('est'))
    tt = totals([r.rid for r in recs])
    rows = []
    for r in recs:
        a = tt.get(r.rid, {}).get('actual', 0)
        m = metrics(r.data['est'], a)
        if m['over']:
            rows.append({'task': r.data.get('title', ''), 'employee': c.name(r.data.get('eid', '')), 'status': r.data.get('status', ''), 'estHours': hrs(m['est']),
                         'actualHours': hrs(a), 'overHours': hrs(m['over']), 'overPct': m['variancePct']})
    cols = [('task', 'Task'), ('employee', 'Assignee'), ('status', 'Status'), ('estHours', 'Estimated (h)'), ('actualHours', 'Actual (h)'), ('overHours', 'Over by (h)'), ('overPct', 'Over %')]
    return cols, rows, 'overPct'


def r11(c):
    tasks = {r.rid: r.data for r in Record.objects.filter(coll='tasks')}
    rows = []
    for s in WorkSession.objects.filter(kind='manual', work_date__gte=c.d0, work_date__lte=c.d1).select_related('approver'):
        if c.emp_ok(s.emp_id):
            rows.append({'type': 'Manual entry', 'employee': c.name(s.emp_id), 'task': (tasks.get(s.task_rid) or {}).get('title', ''), 'date': s.work_date.isoformat(),
                         'hours': hrs(s.work_seconds), 'reason': s.reason, 'category': s.category, 'status': s.approval, 'approver': s.approver.username if s.approver_id else '',
                         'decided': iso(s.decided_at), 'comment': s.decision_note})
    for a in TimeAdjustment.objects.filter(session__work_date__gte=c.d0, session__work_date__lte=c.d1).select_related('session', 'approver'):
        s = a.session
        if c.emp_ok(s.emp_id):
            rows.append({'type': 'Correction', 'employee': c.name(s.emp_id), 'task': (tasks.get(s.task_rid) or {}).get('title', ''), 'date': s.work_date.isoformat(),
                         'hours': hrs(a.new_work_seconds - a.old_work_seconds), 'reason': a.reason, 'category': '', 'status': a.status, 'approver': a.approver.username if a.approver_id else '',
                         'decided': iso(a.decided_at), 'comment': a.decision_note})
    cols = [('type', 'Type'), ('employee', 'Employee'), ('task', 'Task'), ('date', 'Date'), ('hours', 'Hours (+/-)'), ('reason', 'Reason'), ('category', 'Category'),
            ('status', 'Status'), ('approver', 'Decided by'), ('decided', 'Decided at'), ('comment', 'Comment')]
    return cols, rows, 'date'


def _branch_periods():
    """emp -> [(from, to, branch_id)] primary periods (BR-B7 attribution)."""
    out = {}
    for a in A.objects.filter(kind='primary').exclude(status='cancelled'):
        out.setdefault(a.emp_id, []).append((a.effective_from, a.effective_to or datetime.date(9999, 1, 1), str(a.branch_id)))
    return out


def _branch_on(periods, eid, day, default=''):
    for f, t, b in periods.get(eid, []):
        if f <= day <= t:
            return b
    return default


def r12(c):
    per = _branch_periods()
    br = {k: v for k, v in c.branches.items() if c.s.sees_branch(k) and (v.status == 'active' or c.b.get('inactive'))}
    m = {k: {'att': 0, 'present': 0, 'late': 0, 'leaveDays': 0, 'done': 0, 'onTime': 0, 'est': 0, 'act': 0, 'mtg': 0, 'mtgMin': 0, 'mins': 0, 'sent': 0, 'ok': 0, 'heads': set()} for k in br}
    for a in Attendance.objects.filter(day__gte=c.d0.isoformat(), day__lte=c.d1.isoformat()):
        d = parse_date(a.day)
        b = str(a.data.get('b') or _branch_on(per, a.eid, d, str((c.em.get(a.eid) or {}).get('branchId', ''))))
        if b in m and not (c.em.get(a.eid) or {}).get('core'):
            x = m[b]
            x['heads'].add(a.eid)
            x['att'] += 1
            st = a.data.get('s')
            x['present'] += st in ('P', 'L', 'W')
            x['late'] += st == 'L'
            x['leaveDays'] += st == 'V'
    done = [r for r in Record.objects.filter(coll='tasks') if r.data.get('status') == 'done' and c.d0.isoformat() <= str(r.data.get('doneOn', '')) <= c.d1.isoformat()]
    tt = totals([r.rid for r in done])
    for r in done:
        d = r.data
        b = str(d.get('branchId') or _branch_on(per, d.get('eid', ''), parse_date(d.get('doneOn')) or c.d1, str((c.em.get(d.get('eid', '')) or {}).get('branchId', ''))))
        if b in m:
            m[b]['done'] += 1
            m[b]['onTime'] += bool(d.get('due') and str(d.get('doneOn')) <= d['due'])
            if d.get('est') and tt.get(r.rid, {}).get('actual'):
                m[b]['est'] += int(d['est']) * 60
                m[b]['act'] += tt[r.rid]['actual']
    z0 = datetime.datetime.combine(c.d0, datetime.time(0), tzinfo=tz(None))
    z1 = datetime.datetime.combine(c.d1 + datetime.timedelta(days=1), datetime.time(0), tzinfo=tz(None))
    for mt in Meeting.objects.filter(start_at__gte=z0, start_at__lt=z1).exclude(status='cancelled').filter(Q(status='completed') | Q(end_at__lt=now())):
        b = str(mt.branch_id or '')
        if b in m:
            m[b]['mtg'] += 1
            m[b]['mtgMin'] += int((mt.end_at - mt.start_at).total_seconds() // 60)
            m[b]['mins'] += hasattr(mt, 'minutes') and bool(getattr(mt, 'minutes', None))
    for d in D.objects.filter(queued_at__gte=z0, queued_at__lt=z1).exclude(channel='inapp').exclude(status__in=['skipped', 'cancelled']):
        b = str(d.branch_id or '')
        if b in m:
            m[b]['sent'] += 1
            m[b]['ok'] += d.status in ('delivered', 'read')
    rows = []
    for k, x in m.items():
        rows.append({'branch': br[k].name + ('' if br[k].status == 'active' else ' (inactive)'), 'code': br[k].code, 'headcount': len(x['heads']),
                     'attendancePct': round(x['present'] / x['att'] * 100, 1) if x['att'] else None, 'latePct': round(x['late'] / x['present'] * 100, 1) if x['present'] else None,
                     'leaveDays': x['leaveDays'], 'tasksDone': x['done'], 'onTimePct': round(x['onTime'] / x['done'] * 100, 1) if x['done'] else None,
                     'efficiency': round(x['est'] / x['act'] * 100, 1) if x['act'] else None, 'meetings': x['mtg'], 'meetingHours': round(x['mtgMin'] / 60, 1),
                     'minutesPct': round(x['mins'] / x['mtg'] * 100, 1) if x['mtg'] else None, 'deliveryPct': round(x['ok'] / x['sent'] * 100, 1) if x['sent'] else None})
    cols = [('branch', 'Branch'), ('code', 'Code'), ('headcount', 'People with attendance'), ('attendancePct', 'Attendance %'), ('latePct', 'Late %'), ('leaveDays', 'Leave days'),
            ('tasksDone', 'Tasks completed'), ('onTimePct', 'On time %'), ('efficiency', 'Efficiency %'), ('meetings', 'Meetings held'), ('meetingHours', 'Meeting hours'),
            ('minutesPct', 'Minutes recorded %'), ('deliveryPct', 'Alerts delivered %')]
    return cols, rows, 'branch'


def _meetings(c):
    from .meetings import can_view
    z0 = datetime.datetime.combine(c.d0, datetime.time(0), tzinfo=tz(None))
    z1 = datetime.datetime.combine(c.d1 + datetime.timedelta(days=1), datetime.time(0), tzinfo=tz(None))
    q = Meeting.objects.filter(start_at__gte=z0, start_at__lt=z1).prefetch_related('participants', 'action_items').select_related('branch')
    if c.branch.isdigit():
        q = q.filter(branch_id=int(c.branch))
    if c.cat:
        q = q.filter(category=c.cat)
    return [m for m in q if can_view(c.u, m) == 'full']


def r13(c):
    from core.models import MeetingMinutes
    rows = []
    for m in _meetings(c):
        ps = list(m.participants.all())
        internal = [p for p in ps if p.emp_id]
        att = [p for p in internal if p.attendance in ('present', 'late')]
        ai = list(m.action_items.all())
        tasks = {r.rid: r.data.get('status') for r in Record.objects.filter(coll='tasks', rid__in=[a.task_rid for a in ai if a.task_rid])}
        rows.append({'date': local(m.start_at, m.timezone).strftime('%Y-%m-%d %H:%M'), 'title': m.title, 'category': m.category, 'organizer': c.name(m.organizer_emp),
                     'branch': m.branch.name if m.branch_id else '', 'minutes': int((m.end_at - m.start_at).total_seconds() // 60), 'participants': len(ps), 'status': m.status,
                     'attendancePct': round(len(att) / len(internal) * 100, 1) if internal and any(p.attendance != 'unknown' for p in internal) else None,
                     'hasMinutes': 'Yes' if MeetingMinutes.objects.filter(meeting=m).exists() else 'No', 'actionsOpen': sum(1 for a in ai if tasks.get(a.task_rid) != 'done'),
                     'actionsDone': sum(1 for a in ai if tasks.get(a.task_rid) == 'done')})
    cols = [('date', 'Date'), ('title', 'Meeting'), ('category', 'Category'), ('organizer', 'Organizer'), ('branch', 'Branch'), ('minutes', 'Duration (min)'), ('participants', 'Participants'),
            ('status', 'Status'), ('attendancePct', 'Attendance %'), ('hasMinutes', 'Minutes'), ('actionsOpen', 'Action items open'), ('actionsDone', 'Action items done')]
    return cols, rows, 'date'


def r14(c):
    agg = {}
    for m in _meetings(c):
        if m.status == 'cancelled':
            continue
        for p in m.participants.all():
            if p.emp_id and c.emp_ok(p.emp_id):
                a = agg.setdefault(p.emp_id, {'invited': 0, 'accepted': 0, 'declined': 0, 'attended': 0, 'absent': 0})
                a['invited'] += 1
                a['accepted'] += p.rsvp == 'accepted'
                a['declined'] += p.rsvp == 'declined'
                a['attended'] += p.attendance in ('present', 'late')
                a['absent'] += p.attendance == 'absent'
    rows = [dict(employee=c.name(k), **v, attendancePct=round(v['attended'] / (v['attended'] + v['absent']) * 100, 1) if (v['attended'] + v['absent']) else None) for k, v in agg.items()]
    cols = [('employee', 'Employee'), ('invited', 'Invited'), ('accepted', 'Accepted'), ('declined', 'Declined'), ('attended', 'Attended'), ('absent', 'Absent'), ('attendancePct', 'Attendance %')]
    return cols, rows, 'invited'


def r15(c):
    rows = []
    today = datetime.date.today()
    for m in _meetings(c):
        for a in m.action_items.all():
            st = ''
            if a.task_rid:
                r = Record.objects.filter(coll='tasks', rid=a.task_rid).first()
                st = (r.data if r else {}).get('status', 'deleted')
            rows.append({'meeting': m.title, 'item': a.title, 'assignee': c.name(a.assignee_emp), 'due': a.due_date.isoformat() if a.due_date else '',
                         'task': 'Yes' if a.task_rid else 'No', 'status': st or 'not a task yet', 'overdue': 'Yes' if a.due_date and a.due_date < today and st != 'done' else ''})
    cols = [('meeting', 'Meeting'), ('item', 'Action item'), ('assignee', 'Assignee'), ('due', 'Due'), ('task', 'Task created'), ('status', 'Task status'), ('overdue', 'Overdue')]
    return cols, rows, 'due'


def r16(c):
    z0 = datetime.datetime.combine(c.d0, datetime.time(0), tzinfo=tz(None))
    z1 = datetime.datetime.combine(c.d1 + datetime.timedelta(days=1), datetime.time(0), tzinfo=tz(None))
    q = D.objects.filter(queued_at__gte=z0, queued_at__lt=z1).exclude(channel='inapp').select_related('job')
    if not c.s.all:
        q = q.filter(branch_id__in=[int(x) for x in c.s.branches if x.isdigit()])
    if c.branch.isdigit():
        q = q.filter(branch_id=int(c.branch))
    from .notify import EVENTS
    agg = {}
    for d in q:
        k = (d.job.event_key, d.channel)
        a = agg.setdefault(k, {'sent': 0, 'delivered': 0, 'read': 0, 'failed': 0, 'fallback': 0, 'skipped': 0})
        if d.status == 'skipped':
            a['skipped'] += 1
            continue
        if d.status == 'cancelled':
            continue
        a['sent'] += d.status in ('sent', 'delivered', 'read', 'failed', 'failed_perm')
        a['delivered'] += d.status in ('delivered', 'read')
        a['read'] += d.status == 'read'
        a['failed'] += d.status in ('failed', 'failed_perm')
        a['fallback'] += bool(d.fallback_of_id)
    rows = [dict(event=EVENTS.get(k[0], (k[0],))[0], channel=k[1], **v) for k, v in agg.items()]
    cols = [('event', 'Message'), ('channel', 'Channel'), ('sent', 'Sent'), ('delivered', 'Delivered'), ('read', 'Read'), ('failed', 'Failed'), ('fallback', 'Sent as SMS fallback'), ('skipped', 'Skipped')]
    return cols, rows, 'sent'


def r18(c):
    agg = {}
    for s in c.sessions():
        evs = list(s.events.all())
        for i, e in enumerate(evs):
            if e.type not in ('pause', 'logout_pause'):
                continue
            nxt = next((x for x in evs[i + 1:] if x.type in ('resume', 'stop', 'auto_stop', 'complete', 'switch', 'reassigned_stop')), None)
            sec = int(((nxt.at if nxt else (s.ended_at or now())) - e.at).total_seconds())
            if not (c.d0 <= local(e.at, c.tzname(s.emp_id)).date() <= c.d1):
                continue
            k = (s.emp_id, e.reason_code or 'other')
            a = agg.setdefault(k, [0, 0])
            a[0] += 1
            a[1] += max(0, sec)
    rows = [{'employee': c.name(k[0]), 'reason': k[1].replace('_', ' '), 'pauses': v[0], 'pauseHours': hrs(v[1])} for k, v in agg.items()]
    cols = [('employee', 'Employee'), ('reason', 'Reason'), ('pauses', 'Pauses'), ('pauseHours', 'Paused time (h)')]
    return cols, rows, 'pauses'


FN = {'R-01': r01, 'R-02': r02, 'R-03': r03, 'R-04': r04, 'R-05': r05, 'R-06': r06, 'R-07': r07, 'R-08': r08, 'R-09': r09, 'R-10': r10, 'R-11': r11,
      'R-12': r12, 'R-13': r13, 'R-14': r14, 'R-15': r15, 'R-16': r16, 'R-18': r18}


def run(u, b):
    rid = str(b.get('report') or '')
    if rid not in CATALOG:
        invalid(report='Unknown report.')
    from .base import need_module
    need_module(MODULE[rid])
    lab, cap, self_ok = CATALOG[rid]
    if not can(u, cap) and not (self_ok and V.prof(u).emp_id):
        err('FORBIDDEN', 'You do not have permission for this report.')
    c = Ctx(u, dict(b, report=rid))
    res = FN[rid](c)
    cols, rows, dsort = res[0], res[1], res[2]
    summary = res[3] if len(res) > 3 else None
    sk = str(b.get('sort') or '')
    desc = sk.startswith('-') or (not sk)
    key = sk.lstrip('-') or dsort
    if key in [k for k, _ in cols]:
        rows.sort(key=lambda r: (r.get(key) is None, r.get(key) if not isinstance(r.get(key), str) else r.get(key).lower()), reverse=desc)
        if desc:  # keep empty values last
            rows.sort(key=lambda r: r.get(key) is None)
    num = [k for k, _ in cols if rows and all(isinstance(r.get(k), (int, float)) or r.get(k) is None for r in rows) and k not in ('daysOverdue',) and not k.endswith('Pct') and k != 'efficiency']
    tot = {k: round(sum(r.get(k) or 0 for r in rows), 2) for k in num}
    return lab, cols, rows, tot, summary


@action('reports.run', read=True)
def a_run(request, u, b):
    lab, cols, rows, tot, summary = run(u, b)
    from .base import page_args
    p, s = page_args(b)
    return {'title': lab, 'columns': [{'key': k, 'label': l} for k, l in cols], 'rows': rows[(p - 1) * s:p * s], 'total': len(rows), 'page': p, 'size': s, 'totals': tot,
            'summary': summary, 'note': 'Task time is tracked per task and is separate from attendance hours and payroll.'}


@action('reports.export', read=True)
def a_export(request, u, b):
    lab, cols, rows, tot, summary = run(u, b)
    return csv_response('%s.csv' % lab.lower().replace(' ', '-').replace('/', '-'), [l for _, l in cols], [[r.get(k) for k, _ in cols] for r in rows])


@action('reports.catalog', read=True)
def a_catalog(request, u, b):
    from .base import module_on
    me = V.prof(u).emp_id
    return {'items': [{'id': k, 'title': v[0], 'self': not can(u, v[1])} for k, v in CATALOG.items() if module_on(MODULE[k]) and (can(u, v[1]) or (v[2] and me))]}
