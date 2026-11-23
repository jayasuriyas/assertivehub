"""Task-level time tracking. All timestamps come from the server; durations are running totals updated at each
transition (docs/release-v6/04-specifications.md section 3). Task time is reported separately from attendance and payroll.
"""
import datetime

from django.db import IntegrityError, transaction
from django.db.models import Q

from core import views as V
from core.models import Record, TimeAdjustment, TimePolicy, WorkSession, WorkSessionEvent, Attendance
from . import audit, jobs
from .base import (action, err, invalid, need, can, text, parse_date, parse_dt, hm_to_min, now, iso, local, tz, paged, emp_map,
                   bump_rev, ALL_ADMIN)
from .scope import scope_for

DEFAULTS = {'pauseOnLogout': True, 'maxSessionHours': 10, 'autoStopGraceMin': 120, 'stopAtMidnight': True, 'allowManual': True,
            'manualMaxDaysBack': 7, 'requireApprovalForManual': True, 'requireCheckIn': False, 'blockOnLeave': True, 'overrunWarnPct': 80}
LIMITS = {'maxSessionHours': (1, 24), 'autoStopGraceMin': (0, 720), 'manualMaxDaysBack': (0, 60), 'overrunWarnPct': (50, 99)}
PAUSE_REASONS = ['break', 'meeting', 'client_call', 'waiting', 'system', 'switched', 'logout', 'other']
MANUAL_CATS = ['offline', 'client_site', 'forgot_timer', 'meeting', 'other']
OPEN = ('running', 'paused')
COUNTED = Q(approval__in=['none', 'approved']) & ~Q(status='void')


# ---------- policy ----------
def policy(branch=None):
    p = dict(DEFAULTS)
    c = TimePolicy.objects.filter(branch__isnull=True).first()
    if c:
        p.update({k: v for k, v in (c.values or {}).items() if k in DEFAULTS})
    if branch is not None:
        o = TimePolicy.objects.filter(branch=branch).first()
        if o:
            p.update({k: v for k, v in (o.values or {}).items() if k in DEFAULTS})
    return p


def _branch(eid):
    from .branches import branch_of_emp
    return branch_of_emp(eid)


# ---------- core state machine ----------
def live(s, at=None):
    """(work_seconds, pause_seconds) including the open interval."""
    w, p = s.work_seconds, s.pause_seconds
    if s.status in OPEN:
        el = max(0, int(((at or now()) - s.last_transition_at).total_seconds()))
        if s.status == 'running':
            w += el
        else:
            p += el
    return w, p


def transition(s, new_status, t, actor, etype, reason='', note='', req_id='', client_ts='', ip=''):
    t = max(t, s.last_transition_at)
    el = max(0, int((t - s.last_transition_at).total_seconds()))
    if s.status == 'running':
        s.work_seconds += el
    elif s.status == 'paused':
        s.pause_seconds += el
    s.last_transition_at = t
    s.status = new_status
    if new_status in ('stopped', 'auto_stopped'):
        s.ended_at = t
    s.save()
    WorkSessionEvent.objects.create(session=s, type=etype, at=t, actor=actor if getattr(actor, 'pk', None) else None, reason_code=reason[:30],
                                    note=note[:300], req_id=req_id[:40], client_ts=str(client_ts or '')[:40], ip=ip[:45])
    return s


def sdict(s, tasks=None, events=False):
    w, p = live(s)
    t = (tasks or {}).get(s.task_rid)
    if t is None and tasks is None:
        r = Record.objects.filter(coll='tasks', rid=s.task_rid).first()
        t = r.data if r else {}
    d = {'id': str(s.pk), 'taskId': s.task_rid, 'task': (t or {}).get('title', ''), 'empId': s.emp_id, 'kind': s.kind, 'status': s.status,
         'startedAt': iso(s.started_at), 'endedAt': iso(s.ended_at), 'lastAt': iso(s.last_transition_at), 'work': w, 'pause': p,
         'workDate': s.work_date.isoformat(), 'remarks': s.remarks, 'reason': s.reason, 'category': s.category, 'approval': s.approval,
         'needsReview': s.needs_review, 'branchId': str(s.branch_id or ''), 'decisionNote': s.decision_note,
         'approver': s.approver.username if s.approver_id else ''}
    if events:
        d['events'] = [{'type': e.type, 'at': iso(e.at), 'reason': e.reason_code, 'note': e.note, 'by': e.actor.username if e.actor_id else 'system'} for e in s.events.all()]
        d['adjustments'] = [adj_dict(a) for a in s.adjustments.all()]
    return d


def adj_dict(a):
    return {'id': str(a.pk), 'oldStart': iso(a.old_started_at), 'oldEnd': iso(a.old_ended_at), 'oldWork': a.old_work_seconds, 'newStart': iso(a.new_started_at),
            'newEnd': iso(a.new_ended_at), 'newWork': a.new_work_seconds, 'reason': a.reason, 'status': a.status, 'note': a.decision_note,
            'approver': a.approver.username if a.approver_id else ''}


def _me(u):
    me = V.prof(u).emp_id
    if not me:
        err('FORBIDDEN', 'Your login is not linked to an employee profile, so you cannot track time.')
    return me


def _my_task(u, tid):
    me = _me(u)
    r = Record.objects.filter(coll='tasks', rid=str(tid or '')).first()
    if not r or r.data.get('eid') != me:
        err('NOT_FOUND', 'Task not found.')
    return me, r


def _replay(me, req_id):
    if not req_id:
        return None
    ev = WorkSessionEvent.objects.filter(req_id=str(req_id)[:40], session__emp_id=me).select_related('session').first()
    return {'session': sdict(ev.session), 'replay': True} if ev else None


def on_leave(eid, day):
    ds = day.isoformat()
    for r in Record.objects.filter(coll='leaves'):
        l = r.data
        if l.get('eid') == eid and l.get('status') == 'Approved' and l.get('kind') == 'full' and str(l.get('from', '')) <= ds <= str(l.get('to') or l.get('from') or ''):
            return True
    return False


def checked_in(eid, day):
    a = Attendance.objects.filter(day=day.isoformat(), eid=eid).first()
    return bool(a and (a.data.get('tl') or a.data.get('p')))


def _set_task(r, **kw):
    d = dict(r.data)
    d.update(kw)
    r.data = d
    r.save(update_fields=['data'])
    bump_rev()


def _ip(request):
    return V.client_ip(request) if request is not None else ''


# ---------- actions ----------
@action('time.state', module='timetrack', read=True)
def a_state(request, u, b):
    me = V.prof(u).emp_id
    if not me:
        return {'open': [], 'running': None}
    q = list(WorkSession.objects.filter(emp_id=me, status__in=OPEN).order_by('-last_transition_at'))
    review = WorkSession.objects.filter(emp_id=me, needs_review=True, status='auto_stopped').count()
    return {'open': [sdict(s) for s in q], 'running': next((sdict(s) for s in q if s.status == 'running'), None), 'needsReview': review}


@action('time.start', module='timetrack')
def a_start(request, u, b):
    me, tr = _my_task(u, b.get('taskId'))
    rp = _replay(me, b.get('reqId'))
    if rp:
        return rp
    if tr.data.get('status') == 'done':
        err('TASK_DONE', 'This task is done. Ask your manager to reopen it to log more time.')
    e = V.Record.objects.filter(coll='employees', rid=me).first()
    if not e or e.data.get('status', 'Active') != 'Active':
        err('FORBIDDEN', 'Your employee profile is not active.')
    br = _branch(me)
    pol = policy(br)
    t = now()
    today = local(t, br.timezone).date()
    if pol['blockOnLeave'] and on_leave(me, today):
        err('ON_LEAVE', 'You are on approved leave today, so the timer cannot start.')
    if pol['requireCheckIn'] and not checked_in(me, today):
        err('NOT_CHECKED_IN', 'Check in for the day first, then start the timer.')
    req, cts, ip = str(b.get('reqId') or ''), b.get('clientTs'), _ip(request)
    same = WorkSession.objects.filter(emp_id=me, task_rid=tr.rid, status__in=OPEN).first()
    if same and same.status == 'running':
        return {'session': sdict(same)}
    stopped = None
    run = WorkSession.objects.filter(emp_id=me, status='running').exclude(pk=getattr(same, 'pk', None)).first()
    if run:
        if not b.get('switch'):
            err('TIMER_ACTIVE', 'You are already working on another task.', session=sdict(run))
        stopped = sdict(transition(run, 'stopped', t, u, 'switch', note='Switched to another task', ip=ip))
        check_overrun(run.task_rid)
    if same:  # paused on this task: Start means Resume
        s = transition(same, 'running', t, u, 'resume', req_id=req, client_ts=cts, ip=ip)
    else:
        try:
            with transaction.atomic():
                s = WorkSession.objects.create(task_rid=tr.rid, emp_id=me, user=u, branch=br, dept=str(e.data.get('dept', ''))[:80], status='running',
                                               started_at=t, last_transition_at=t, work_date=today)
                WorkSessionEvent.objects.create(session=s, type='start', at=t, actor=u, req_id=req[:40], client_ts=str(cts or '')[:40], ip=ip)
        except IntegrityError:
            err('TIMER_ACTIVE', 'A timer is already running for you (started on another device).')
    if tr.data.get('status') == 'todo':
        _set_task(tr, status='doing')
    return {'session': sdict(s), 'stopped': stopped, 'taskStatus': tr.data.get('status')}


def _my_session(u, sid, states):
    me = _me(u)
    s = WorkSession.objects.filter(pk=int(sid) if str(sid or '').isdigit() else 0, emp_id=me).first()
    if not s:
        err('NOT_FOUND', 'Session not found.')
    if s.status not in states:
        err('INVALID_STATE', 'This timer is %s.' % s.status.replace('_', ' '), state=s.status, session=sdict(s))
    return me, s


@action('time.pause', module='timetrack')
def a_pause(request, u, b):
    me = _me(u)
    rp = _replay(me, b.get('reqId'))
    if rp:
        return rp
    me, s = _my_session(u, b.get('sessionId'), ('running',))
    reason = str(b.get('reason') or '')
    if reason not in PAUSE_REASONS:
        invalid(reason='Choose why you are pausing.')
    note = text(b, 'note', 300)
    if reason == 'other' and len(note) < 3:
        invalid(note='Please say why you are pausing.')
    transition(s, 'paused', now(), u, 'pause', reason=reason, note=note, req_id=str(b.get('reqId') or ''), client_ts=b.get('clientTs'), ip=_ip(request))
    return {'session': sdict(s)}


@action('time.resume', module='timetrack')
def a_resume(request, u, b):
    me = _me(u)
    rp = _replay(me, b.get('reqId'))
    if rp:
        return rp
    me, s = _my_session(u, b.get('sessionId'), ('paused',))
    t = now()
    run = WorkSession.objects.filter(emp_id=me, status='running').exclude(pk=s.pk).first()
    stopped = None
    if run:
        if not b.get('switch'):
            err('TIMER_ACTIVE', 'You are already working on another task.', session=sdict(run))
        stopped = sdict(transition(run, 'stopped', t, u, 'switch', ip=_ip(request)))
    tr = Record.objects.filter(coll='tasks', rid=s.task_rid).first()
    if not tr or tr.data.get('eid') != me or tr.data.get('status') == 'done':
        err('TASK_DONE', 'This task is no longer open for you.')
    transition(s, 'running', t, u, 'resume', req_id=str(b.get('reqId') or ''), client_ts=b.get('clientTs'), ip=_ip(request))
    return {'session': sdict(s), 'stopped': stopped}


@action('time.stop', module='timetrack')
def a_stop(request, u, b):
    me = _me(u)
    rp = _replay(me, b.get('reqId'))
    if rp:
        return rp
    me, s = _my_session(u, b.get('sessionId'), OPEN)
    s.remarks = text(b, 'remarks', 500)
    transition(s, 'stopped', now(), u, 'stop', note=s.remarks[:300], req_id=str(b.get('reqId') or ''), client_ts=b.get('clientTs'), ip=_ip(request))
    check_overrun(s.task_rid)
    return {'session': sdict(s)}


def complete_task(tr, u, remarks='', ip=''):
    """Stop the open session on this task and mark it done (same rules as the board's 'Mark done')."""
    stopped = None
    for s in WorkSession.objects.filter(task_rid=tr.rid, status__in=OPEN):
        if remarks:
            s.remarks = remarks[:500]
        stopped = sdict(transition(s, 'stopped', now(), u, 'complete', note=remarks[:300], ip=ip))
    if tr.data.get('status') != 'done':
        _set_task(tr, status='done', doneOn=datetime.date.today().isoformat())
    check_overrun(tr.rid)
    return stopped


@action('time.complete', module='timetrack')
def a_complete(request, u, b):
    me, tr = _my_task(u, b.get('taskId'))
    if tr.data.get('status') == 'done':
        return {'taskStatus': 'done'}
    stopped = complete_task(tr, u, text(b, 'remarks', 500), _ip(request))
    from . import notify
    notify.task_completed(tr.data, tr.rid)
    return {'stopped': stopped, 'taskStatus': 'done'}


# ---------- manual entries and adjustments ----------
def _overlaps(me, start, end, exclude_pk=None):
    q = WorkSession.objects.filter(emp_id=me).exclude(status='void').exclude(approval__in=['rejected', 'withdrawn']).exclude(pk=exclude_pk)
    q = q.filter(started_at__lt=end).filter(Q(ended_at__gt=start) | Q(ended_at__isnull=True))
    return q.first()


def _day_total(me, day, exclude_pk=None):
    tot = 0
    for s in WorkSession.objects.filter(emp_id=me, work_date=day).exclude(status='void').exclude(approval__in=['rejected', 'withdrawn']).exclude(pk=exclude_pk):
        tot += live(s)[0]
    return tot


def approvers_for(eid):
    """Recipients (emp ids or 'u:<user id>') who may approve this employee's time: reporting manager and branch managers/admins."""
    from core.models import Profile, UserBranchScope
    out = set()
    e = V.Record.objects.filter(coll='employees', rid=eid).first()
    rt = (e.data if e else {}).get('reportsTo')
    if rt and rt != eid:
        out.add(rt)
    bid = (e.data if e else {}).get('branchId')
    if str(bid or '').isdigit():
        for sc in UserBranchScope.objects.filter(branch_id=int(bid), user__is_active=True).select_related('user'):
            p = V.prof(sc.user)
            if p.role in ('branchmgr', 'branchadmin') and p.emp_id != eid:
                out.add(p.emp_id or 'u:%s' % sc.user_id)
    return sorted(out)


@action('time.manual.create', module='timetrack')
def a_manual(request, u, b):
    me, tr = _my_task(u, b.get('taskId'))
    br = _branch(me)
    pol = policy(br)
    if not pol['allowManual'] or tr.data.get('allowManual') is False:
        err('FORBIDDEN', 'Manual time entries are switched off for this task.')
    rp = _replay(me, b.get('reqId'))
    if rp:
        return rp
    day = parse_date(b.get('date'))
    if not day:
        invalid(date='Choose the date you worked.')
    st = hm_to_min(b.get('start'))
    if st is None:
        invalid(start='Enter the start time.')
    if b.get('end'):
        en = hm_to_min(b.get('end'))
        if en is None or en <= st:
            invalid(end='End time must be after the start time.')
        mins = en - st
    else:
        try:
            mins = int(b.get('minutes'))
        except (TypeError, ValueError):
            invalid(minutes='Enter the end time or the duration.')
    if mins < 1 or mins > 16 * 60:
        invalid(minutes='Duration must be between 1 minute and 16 hours.')
    reason = text(b, 'reason', 300)
    if len(reason) < 10:
        invalid(reason='Please give a reason (at least 10 characters).')
    cat = str(b.get('category') or 'other')
    if cat not in MANUAL_CATS:
        cat = 'other'
    z = tz(br.timezone)
    start = datetime.datetime.combine(day, datetime.time(st // 60, st % 60), tzinfo=z).astimezone(datetime.timezone.utc)
    end = start + datetime.timedelta(minutes=mins)
    t = now()
    if end > t:
        invalid(end='Manual entries cannot be in the future.')
    if day < local(t, br.timezone).date() - datetime.timedelta(days=pol['manualMaxDaysBack']):
        invalid(date='You can add time up to %d days back. Ask your manager for older corrections.' % pol['manualMaxDaysBack'])
    o = _overlaps(me, start, end)
    if o:
        err('OVERLAP', 'This overlaps time you already logged (%s).' % local(o.started_at, br.timezone).strftime('%d %b %H:%M'), session=sdict(o))
    if _day_total(me, day) + mins * 60 > 16 * 3600:
        invalid(minutes='This would make more than 16 hours of task time on that day.')
    auto = not pol['requireApprovalForManual']
    e = V.Record.objects.get(coll='employees', rid=me).data
    s = WorkSession.objects.create(task_rid=tr.rid, emp_id=me, user=u, branch=br, dept=str(e.get('dept', ''))[:80], kind='manual', status='stopped',
                                   started_at=start, ended_at=end, last_transition_at=end, work_seconds=mins * 60, work_date=day, reason=reason,
                                   category=cat, approval='approved' if auto else 'pending')
    WorkSessionEvent.objects.create(session=s, type='manual', at=t, actor=u, note=reason, req_id=str(b.get('reqId') or '')[:40], ip=_ip(request))
    audit.record(u, 'time.manual', 'work_session', s.pk, after={'task': tr.rid, 'minutes': mins, 'date': day.isoformat(), 'auto': auto}, branch_id=br.pk, request=request)
    if auto:
        check_overrun(tr.rid)
    else:
        from . import notify
        notify.emit('time.manual_submitted', 'work_session', str(s.pk), 'submitted', approvers_for(me),
                    {'emp_id': me, 'task_id': tr.rid, 'duration_min': mins})
    return {'session': sdict(s)}


@action('time.manual.withdraw', module='timetrack')
def a_withdraw(request, u, b):
    me = _me(u)
    s = WorkSession.objects.filter(pk=int(b.get('sessionId')) if str(b.get('sessionId') or '').isdigit() else 0, emp_id=me, approval='pending').first()
    if not s:
        err('NOT_FOUND', 'Pending entry not found.')
    s.approval = 'withdrawn'
    s.save(update_fields=['approval'])
    return {'session': sdict(s)}


@action('time.adjust.create', module='timetrack')
def a_adjust(request, u, b):
    me, s = _my_session(u, b.get('sessionId'), ('stopped', 'auto_stopped'))
    if s.kind != 'timer':
        err('INVALID_STATE', 'Only timer sessions can be corrected. Withdraw a manual entry and add it again instead.')
    if s.adjustments.filter(status='pending').exists():
        err('INVALID_STATE', 'A correction for this session is already waiting for approval.')
    br = s.branch or _branch(me)
    ns, ne = parse_dt(b.get('newStart'), br.timezone), parse_dt(b.get('newEnd'), br.timezone)
    if not ns or not ne or ne <= ns:
        invalid(newEnd='End must be after start.')
    if ne > now():
        invalid(newEnd='The end cannot be in the future.')
    if (ne - ns) > datetime.timedelta(hours=16):
        invalid(newEnd='A session can be at most 16 hours.')
    reason = text(b, 'reason', 300)
    if len(reason) < 10:
        invalid(reason='Please give a reason (at least 10 characters).')
    o = _overlaps(me, ns, ne, exclude_pk=s.pk)
    if o:
        err('OVERLAP', 'The corrected time overlaps another session.', session=sdict(o))
    new_work = max(0, int((ne - ns).total_seconds()) - s.pause_seconds)
    a = TimeAdjustment.objects.create(session=s, requested_by=u, old_started_at=s.started_at, old_ended_at=s.ended_at, old_work_seconds=s.work_seconds,
                                      new_started_at=ns, new_ended_at=ne, new_work_seconds=new_work, reason=reason)
    from . import notify
    notify.emit('time.manual_submitted', 'time_adjustment', str(a.pk), 'submitted', approvers_for(me), {'emp_id': me, 'task_id': s.task_rid, 'duration_min': new_work // 60})
    return {'adjustment': adj_dict(a)}


def can_approve(u, eid):
    me = V.prof(u).emp_id
    if not eid or eid == me:
        return False
    r = V.role_of(u)
    if r in ALL_ADMIN:
        return True
    if r in ('branchadmin', 'branchmgr', 'manager'):
        return scope_for(u).sees(eid)
    return False


@action('time.approvals.list', module='timetrack', read=True)
def a_approvals(request, u, b):
    need(u, 'time.approve')
    em = emp_map()
    tasks = {r.rid: r.data for r in Record.objects.filter(coll='tasks')}
    out = []
    for s in WorkSession.objects.filter(approval='pending').order_by('created_at'):
        if can_approve(u, s.emp_id):
            out.append(dict(sdict(s, tasks), type='manual', key='m:%s' % s.pk, name=(em.get(s.emp_id) or {}).get('name', '')))
    for a in TimeAdjustment.objects.filter(status='pending').select_related('session').order_by('created_at'):
        if can_approve(u, a.session.emp_id):
            out.append(dict(sdict(a.session, tasks), type='adjustment', key='a:%s' % a.pk, adjustment=adj_dict(a), name=(em.get(a.session.emp_id) or {}).get('name', '')))
    return {'items': out}


@action('time.approvals.decide', module='timetrack')
def a_decide(request, u, b):
    need(u, 'time.approve')
    dec = b.get('decision')
    if dec not in ('approve', 'reject'):
        invalid(decision='Choose approve or reject.')
    note = text(b, 'comment', 300)
    if dec == 'reject' and len(note) < 3:
        invalid(comment='Please say why you are rejecting.')
    done, refused = [], []
    from . import notify
    for key in (b.get('ids') or [])[:200]:
        kind, _, pk = str(key).partition(':')
        if not pk.isdigit():
            refused.append({'id': key, 'reason': 'Not found'})
            continue
        if kind == 'm':
            s = WorkSession.objects.filter(pk=int(pk), approval='pending').first()
            if not s or not can_approve(u, s.emp_id):
                refused.append({'id': key, 'reason': 'Not found or not yours to approve'})
                continue
            if dec == 'approve' and _overlaps(s.emp_id, s.started_at, s.ended_at, exclude_pk=s.pk):
                refused.append({'id': key, 'reason': 'Overlaps another session now'})
                continue
            s.approval = 'approved' if dec == 'approve' else 'rejected'
            s.approver, s.decided_at, s.decision_note = u, now(), note
            s.save()
            emp, task = s.emp_id, s.task_rid
        elif kind == 'a':
            a = TimeAdjustment.objects.filter(pk=int(pk), status='pending').select_related('session').first()
            if not a or not can_approve(u, a.session.emp_id):
                refused.append({'id': key, 'reason': 'Not found or not yours to approve'})
                continue
            a.status = 'approved' if dec == 'approve' else 'rejected'
            a.approver, a.decided_at, a.decision_note = u, now(), note
            a.save()
            s = a.session
            if dec == 'approve':
                s.started_at, s.ended_at, s.work_seconds, s.needs_review = a.new_started_at, a.new_ended_at, a.new_work_seconds, False
                s.save()
                WorkSessionEvent.objects.create(session=s, type='adjust_applied', at=now(), actor=u, note=a.reason[:300])
            emp, task = s.emp_id, s.task_rid
        else:
            refused.append({'id': key, 'reason': 'Not found'})
            continue
        audit.record(u, 'time.%s' % dec, 'work_session', s.pk, after={'key': key, 'comment': note}, branch_id=s.branch_id, request=request)
        notify.emit('time.manual_decided', 'work_session', key, dec, [emp], {'emp_id': emp, 'task_id': task, 'decision': 'approved' if dec == 'approve' else 'rejected', 'comment': note})
        if dec == 'approve':
            check_overrun(task)
        done.append(key)
    return {'done': done, 'refused': refused}


@action('time.void', module='timetrack')
def a_void(request, u, b):
    need(u, 'time.void')
    s = WorkSession.objects.filter(pk=int(b.get('sessionId')) if str(b.get('sessionId') or '').isdigit() else 0).first()
    if not s or not scope_for(u).sees(s.emp_id):
        err('NOT_FOUND', 'Session not found.')
    reason = text(b, 'reason', 300)
    if len(reason) < 10:
        invalid(reason='Please give a reason (at least 10 characters).')
    if s.status in OPEN:
        transition(s, 'stopped', now(), u, 'stop', note='Stopped by admin before void')
    s.status, s.void_reason = 'void', reason
    s.save()
    WorkSessionEvent.objects.create(session=s, type='void', at=now(), actor=u, note=reason)
    audit.record(u, 'time.void', 'work_session', s.pk, after={'reason': reason}, branch_id=s.branch_id, request=request)
    return {'session': sdict(s)}


# ---------- read models ----------
def totals(task_ids=None, emp=None):
    """{task_id: {'actual': s, 'pending': s, 'open': status|None, 'sessions': n}}"""
    q = WorkSession.objects.exclude(status='void')
    if task_ids is not None:
        q = q.filter(task_rid__in=list(task_ids))
    if emp:
        q = q.filter(emp_id=emp)
    out = {}
    t = now()
    for s in q:
        o = out.setdefault(s.task_rid, {'actual': 0, 'pending': 0, 'open': None, 'sessions': 0})
        w = live(s, t)[0]
        if s.approval in ('none', 'approved'):
            o['actual'] += w
            if w >= 10:
                o['sessions'] += 1
        elif s.approval == 'pending':
            o['pending'] += w
        if s.status in OPEN:
            o['open'] = s.status
    return out


def metrics(est_min, actual_s):
    """Estimated/actual/remaining/variance/efficiency per 04-specifications 3.4 (seconds in, seconds and % out)."""
    if not est_min:
        return {'est': None, 'actual': actual_s, 'remaining': None, 'over': None, 'variance': None, 'variancePct': None, 'efficiency': None}
    e = int(est_min) * 60
    a = actual_s
    return {'est': e, 'actual': a, 'remaining': max(e - a, 0), 'over': max(a - e, 0), 'variance': a - e,
            'variancePct': round((a - e) / e * 100, 1), 'efficiency': round(e / a * 100, 1) if a > 0 else None}


def load_payload(task_ids):
    """Compact read-only map added to `load`: {task_id: [actual_s, pending_s, open_status]}."""
    return {k: [v['actual'], v['pending'], v['open']] for k, v in totals(task_ids).items()}


@action('time.summary', module='timetrack', read=True)
def a_summary(request, u, b):
    s = scope_for(u)
    ids = b.get('taskIds') or ([b['taskId']] if b.get('taskId') else [])
    if isinstance(ids, str):
        ids = [x for x in ids.split(',') if x]
    out = {}
    if ids:
        recs = {r.rid: r.data for r in Record.objects.filter(coll='tasks', rid__in=ids[:500])}
        tt = totals(recs.keys())
        for k, d in recs.items():
            if not s.sees(d.get('eid', '')):
                continue
            t = tt.get(k, {'actual': 0, 'pending': 0, 'open': None, 'sessions': 0})
            out[k] = dict(metrics(d.get('est'), t['actual']), pending=t['pending'], open=t['open'], sessions=t['sessions'])
        return {'tasks': out}
    # personal / team summary for the dashboard widget (W4)
    eid = str(b.get('empId') or s.me or '')
    if not s.sees(eid):
        err('NOT_FOUND', 'Employee not found.')
    br = _branch(eid)
    z = br.timezone
    today = local(now(), z).date()
    wk0 = today - datetime.timedelta(days=today.weekday())
    def task_secs(d0, d1):
        return sum(live(x)[0] for x in WorkSession.objects.filter(emp_id=eid, work_date__gte=d0, work_date__lte=d1).filter(COUNTED))
    def att_mins(d0, d1):
        from core.punch import mins as _m
        tot = 0
        for a in Attendance.objects.filter(eid=eid, day__gte=d0.isoformat(), day__lte=d1.isoformat()):
            for p in a.data.get('p') or []:
                if len(p) > 1 and p[0] and p[1] and _m(p[1]) is not None and _m(p[0]) is not None:
                    tot += max(0, _m(p[1]) - _m(p[0]))
        return tot
    done_wk = [r for r in Record.objects.filter(coll='tasks') if r.data.get('eid') == eid and r.data.get('status') == 'done' and str(r.data.get('doneOn', '')) >= wk0.isoformat()]
    tt = totals([r.rid for r in done_wk])
    est = sum(int(r.data.get('est') or 0) * 60 for r in done_wk if r.data.get('est'))
    act = sum(tt.get(r.rid, {}).get('actual', 0) for r in done_wk if r.data.get('est'))
    pend = 0
    if can(u, 'time.approve'):
        pend = sum(1 for x in WorkSession.objects.filter(approval='pending') if can_approve(u, x.emp_id)) + \
               sum(1 for x in TimeAdjustment.objects.filter(status='pending').select_related('session') if can_approve(u, x.session.emp_id))
    return {'empId': eid, 'today': {'task': task_secs(today, today), 'attendance': att_mins(today, today) * 60},
            'week': {'task': task_secs(wk0, today), 'attendance': att_mins(wk0, today) * 60},
            'doneThisWeek': {'tasks': len(done_wk), 'est': est, 'actual': act, 'efficiency': round(est / act * 100, 1) if act else None},
            'pendingApprovals': pend}


@action('time.logs', module='timetrack', read=True)
def a_logs(request, u, b):
    s = scope_for(u)
    q = WorkSession.objects.all().order_by('-started_at')
    if b.get('taskId'):
        r = Record.objects.filter(coll='tasks', rid=str(b['taskId'])).first()
        if not r or not (s.sees(r.data.get('eid', '')) or WorkSession.objects.filter(task_rid=r.rid, emp_id=s.me).exists()):
            err('NOT_FOUND', 'Task not found.')
        q = q.filter(task_rid=r.rid)
        if not can(u, 'time.view_scope'):
            q = q.filter(emp_id=s.me)
    else:
        eid = str(b.get('empId') or s.me or '')
        if eid != s.me:
            need(u, 'time.view_scope')
        if not s.sees(eid):
            err('NOT_FOUND', 'Employee not found.')
        q = q.filter(emp_id=eid)
    d0, d1 = parse_date(b.get('from')), parse_date(b.get('to'))
    if d0:
        q = q.filter(work_date__gte=d0)
    if d1:
        q = q.filter(work_date__lte=d1)
    tasks = {r.rid: r.data for r in Record.objects.filter(coll='tasks')}
    em = emp_map()
    return paged(q.prefetch_related('events', 'adjustments'), b, lambda x: dict(sdict(x, tasks, events=True), name=(em.get(x.emp_id) or {}).get('name', '')))


@action('time.policy.get', module='timetrack', read=True)
def a_policy_get(request, u, b):
    need(u, 'time.policy')
    from .branches import branch_by_id
    br = branch_by_id(b.get('branchId')) if b.get('branchId') else None
    if br and not scope_for(u).sees_branch(br.pk):
        err('NOT_FOUND', 'Branch not found.')
    own = TimePolicy.objects.filter(branch=br).first() if br else TimePolicy.objects.filter(branch__isnull=True).first()
    return {'effective': policy(br), 'own': (own.values if own else {}), 'defaults': DEFAULTS, 'branchId': str(br.pk) if br else ''}


@action('time.policy.save', module='timetrack')
def a_policy_save(request, u, b):
    need(u, 'time.policy')
    from .branches import branch_by_id
    br = branch_by_id(b.get('branchId')) if b.get('branchId') else None
    if br is None and V.role_of(u) not in ALL_ADMIN:
        err('FORBIDDEN', 'Only company admins can change the company-wide rules. You can set rules for your branch.')
    if br and not scope_for(u).sees_branch(br.pk):
        err('NOT_FOUND', 'Branch not found.')
    vals = {}
    for k, dv in DEFAULTS.items():
        if k not in (b.get('values') or {}):
            continue
        v = b['values'][k]
        if v is None:
            continue
        if isinstance(dv, bool):
            vals[k] = bool(v)
        else:
            try:
                v = int(v)
            except (TypeError, ValueError):
                invalid(**{k: 'Must be a number.'})
            lo, hi = LIMITS[k]
            if v < lo or v > hi:
                invalid(**{k: 'Must be between %d and %d.' % (lo, hi)})
            vals[k] = v
    obj = TimePolicy.objects.filter(branch=br).first() if br else TimePolicy.objects.filter(branch__isnull=True).first()
    before = dict(obj.values) if obj else {}
    obj = obj or TimePolicy(branch=br)
    obj.values = vals
    obj.save()
    audit.record(u, 'time.policy', 'time_policy', obj.pk, before=before, after=vals, branch_id=getattr(br, 'pk', None), request=request)
    return {'effective': policy(br)}


@action('time.workload', module='timetrack', read=True)
def a_workload(request, u, b):
    need(u, 'time.view_scope')
    s = scope_for(u)
    days = max(1, min(10, int(b.get('days') or 5)))
    em = emp_map()
    ids = [k for k, e in em.items() if e.get('status', 'Active') == 'Active' and not e.get('core') and s.sees(k)]
    open_tasks = [r for r in Record.objects.filter(coll='tasks') if r.data.get('eid') in ids and r.data.get('status') != 'done']
    tt = totals([r.rid for r in open_tasks])
    from .branches import branch_by_id, head_office
    from .meetings import busy_minutes
    out = []
    today = datetime.date.today()
    for eid in ids:
        br = branch_by_id(em[eid].get('branchId')) or head_office()
        day_min = max(0, (hm_to_min(br.work_end) or 1080) - (hm_to_min(br.work_start) or 540) - (br.break_min or 0))
        wd = []
        d = today
        while len(wd) < days:
            if d.isoweekday() % 7 in (br.work_days or [1, 2, 3, 4, 5]):
                wd.append(d)
            d += datetime.timedelta(days=1)
        cap = sum(0 if on_leave(eid, x) else max(0, day_min - busy_minutes(eid, x)) for x in wd)
        planned = 0
        for r in open_tasks:
            if r.data.get('eid') != eid or not r.data.get('est'):
                continue
            rem = max(0, int(r.data['est']) * 60 - tt.get(r.rid, {}).get('actual', 0)) // 60
            due = parse_date(r.data.get('due')) or wd[-1]
            in_window = [x for x in wd if x <= due] or wd[:1]
            per_day = int(r.data.get('dailyMin') or 0)
            planned += min(rem, per_day * len(in_window)) if per_day else (rem if due <= wd[-1] else rem * len(in_window) // max(1, (due - today).days + 1))
        out.append({'empId': eid, 'name': em[eid].get('name', ''), 'dept': em[eid].get('dept', ''), 'capacityMin': cap, 'plannedMin': planned,
                    'pct': round(planned / cap * 100) if cap else None})
    out.sort(key=lambda x: -(x['pct'] or 0))
    return {'items': out, 'days': days}


@action('time.atRisk', module='timetrack', read=True)
def a_at_risk(request, u, b):
    s = scope_for(u)
    recs = [r for r in Record.objects.filter(coll='tasks') if r.data.get('status') != 'done' and r.data.get('est') and s.sees(r.data.get('eid', ''))]
    return {'ids': sorted(at_risk(recs))}


# ---------- hooks used by legacy code and the scheduler ----------
def at_risk(recs):
    tt = totals([r.rid for r in recs])
    today = datetime.date.today()
    out = set()
    for r in recs:
        est = int(r.data.get('est') or 0) * 60
        act = tt.get(r.rid, {}).get('actual', 0)
        due = parse_date(r.data.get('due'))
        pol = policy(None)
        if est and act >= est * pol['overrunWarnPct'] / 100:
            out.add(r.rid)
            continue
        if est and due and r.data.get('dailyMin'):
            days_left = sum(1 for i in range((due - today).days + 1) if (today + datetime.timedelta(days=i)).isoweekday() % 7 in [1, 2, 3, 4, 5, 6])
            if est - act > int(r.data['dailyMin']) * 60 * max(0, days_left):
                out.add(r.rid)
    return out


def stop_open_for_task(task_rid, reason='reassigned', except_emp=None):
    n = 0
    for s in WorkSession.objects.filter(task_rid=task_rid, status__in=OPEN):
        if except_emp and s.emp_id == except_emp:
            continue
        transition(s, 'stopped', now(), None, 'reassigned_stop' if reason == 'reassigned' else 'stop', reason=reason)
        n += 1
    return n


def stop_all_for_emp(eid, reason='inactive'):
    for s in WorkSession.objects.filter(emp_id=eid, status__in=OPEN):
        transition(s, 'stopped', now(), None, 'stop', reason=reason)


def on_logout(u):
    me = V.prof(u).emp_id
    if not me:
        return
    for s in WorkSession.objects.filter(emp_id=me, status='running'):
        if policy(s.branch)['pauseOnLogout']:
            transition(s, 'paused', now(), u, 'logout_pause', reason='logout')


def cutoff_for(s):
    br = s.branch or _branch(s.emp_id)
    pol = policy(br)
    z = tz(br.timezone)
    st_local = s.started_at.astimezone(z)
    c = [s.started_at + datetime.timedelta(hours=pol['maxSessionHours'])]
    endm = hm_to_min(br.work_end)
    if endm is not None:
        day_end = datetime.datetime.combine(st_local.date(), datetime.time(endm // 60, endm % 60), tzinfo=z)
        if day_end > st_local:
            c.append(day_end + datetime.timedelta(minutes=pol['autoStopGraceMin']))
    if pol['stopAtMidnight']:
        c.append(datetime.datetime.combine(st_local.date(), datetime.time(23, 59, 59), tzinfo=z))
    return min(c).astimezone(datetime.timezone.utc)


def auto_stop_due():
    t = now()
    n = 0
    from . import notify
    for s in WorkSession.objects.filter(status__in=OPEN):
        c = cutoff_for(s)
        if t >= c:
            transition(s, 'auto_stopped', c, None, 'auto_stop', reason='cutoff')
            s.needs_review = True
            s.save(update_fields=['needs_review'])
            n += 1
            notify.emit('time.auto_stopped', 'work_session', str(s.pk), 'auto', [s.emp_id], {'emp_id': s.emp_id, 'task_id': s.task_rid, 'stop_time': iso(c)})
            check_overrun(s.task_rid)
    return n


def check_overrun(task_rid):
    r = Record.objects.filter(coll='tasks', rid=task_rid).first()
    if not r or not r.data.get('est'):
        return
    est = int(r.data['est']) * 60
    act = totals([task_rid]).get(task_rid, {}).get('actual', 0)
    pct = policy(None)['overrunWarnPct']
    from . import notify
    for th in (pct, 100):
        if act >= est * th / 100:
            rcpt = [r.data.get('eid', '')]
            cb = r.data.get('createdBy')
            if cb:
                from django.contrib.auth.models import User
                cu = User.objects.filter(pk=cb).first() if str(cb).isdigit() else None
                if cu:
                    rcpt.append(V.prof(cu).emp_id or 'u:%s' % cu.pk)
            mgr = (V.Record.objects.filter(coll='employees', rid=r.data.get('eid', '')).first() or Record(data={})).data.get('reportsTo')
            if mgr:
                rcpt.append(mgr)
            notify.emit('task.time_exceeded', 'task', task_rid, 'p%d' % th, sorted(set(x for x in rcpt if x)),
                        {'task_id': task_rid, 'emp_id': r.data.get('eid', ''), 'threshold': th, 'actual_s': act, 'est_s': est})


@jobs.handler('time.periodic')
def _periodic(payload, job):
    auto_stop_due()
    for s in WorkSession.objects.filter(status='running').values_list('task_rid', flat=True).distinct():
        check_overrun(s)
