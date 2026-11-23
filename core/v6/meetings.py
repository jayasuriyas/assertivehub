"""Meeting schedule management: categories, conditional fields, rooms, conflicts, recurrence, RSVP, reminders,
minutes, action items -> tasks, attendance, dashboard widget, calendar view."""
import datetime
import html
import re
import time

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt

from core import views as V
from core.models import Meeting, MeetingActionItem, MeetingMinutes, MeetingParticipant as MP, MeetingRoom, MeetingSeries, Record
from . import audit, jobs
from .base import (action, err, invalid, need, can, text, int_in, parse_date, hm_to_min, now, iso, local, tz, paged, emp_map, bump_rev,
                   ALL_ADMIN, SCOPED_ADMIN)
from .scope import scope_for, picker_emps

CATS = {  # category: (label, default location type, private by default)
    'online': ('Online meeting', 'online', False), 'direct': ('Direct meeting', 'office', False), 'client': ('Client meeting', 'office', False),
    'team': ('Team meeting', 'office', False), 'management': ('Management meeting', 'office', False), 'one_to_one': ('One-to-one', 'office', True),
    'interview': ('Interview', 'office', True), 'training': ('Training session', 'office', False), 'vendor': ('Vendor meeting', 'office', False),
    'other': ('Other', 'office', False)}
LOC = ('online', 'office', 'external', 'hybrid')
PLATFORMS = ('google_meet', 'teams', 'zoom', 'webex', 'other')
MATERIAL = ('start_at', 'end_at', 'room_id', 'location_type', 'location_text', 'join_url')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ---------- permissions ----------
def _parts(m):
    return set(p.emp_id for p in m.participants.all() if p.emp_id) | ({m.organizer_emp} if m.organizer_emp else set())


def is_party(u, m):
    me = V.prof(u).emp_id
    return (m.organizer_user_id == u.pk) or (me and me in _parts(m))


def can_view(u, m):
    if is_party(u, m):
        return 'full'
    s = scope_for(u)
    if s.all:
        return 'full'
    if can(u, 'meeting.view_scope') and (_parts(m) & s.emps or (m.branch_id and s.sees_branch(m.branch_id) and V.role_of(u) in SCOPED_ADMIN | {'branchmgr'})):
        return 'full' if (m.visibility != 'private' or V.role_of(u) in SCOPED_ADMIN) else 'busy'
    return None


def can_manage(u, m):
    if m.organizer_user_id == u.pk or (V.prof(u).emp_id and V.prof(u).emp_id == m.organizer_emp):
        return True
    s = scope_for(u)
    return can(u, 'meeting.manage_scope') and (s.all or (m.branch_id and s.sees_branch(m.branch_id)))


def _get(u, mid, manage=False):
    m = Meeting.objects.filter(pk=int(mid) if str(mid or '').isdigit() else 0).select_related('room', 'branch').prefetch_related('participants').first()
    if not m or not can_view(u, m):
        err('NOT_FOUND', 'Meeting not found.')
    if manage and not can_manage(u, m):
        err('FORBIDDEN', 'Only the organizer can change this meeting.')
    return m


# ---------- serialisation ----------
def status_of(m, t=None):
    t = t or now()
    if m.status == 'scheduled' and m.start_at <= t < m.end_at:
        return 'in_progress'
    if m.status == 'scheduled' and t >= m.end_at + datetime.timedelta(hours=2):
        return 'awaiting_update'
    return m.status


def mdict(m, u=None, em=None, full=False, level='full'):
    em = em if em is not None else emp_map()
    lt = local(m.start_at, m.timezone)
    d = {'id': str(m.pk), 'start': iso(m.start_at), 'end': iso(m.end_at), 'tz': m.timezone, 'date': lt.date().isoformat(), 'startLocal': lt.strftime('%H:%M'),
         'endLocal': local(m.end_at, m.timezone).strftime('%H:%M'), 'duration': int((m.end_at - m.start_at).total_seconds() // 60),
         'status': status_of(m), 'category': m.category, 'branchId': str(m.branch_id or ''), 'visibility': m.visibility, 'seriesId': str(m.series_id or '')}
    if level == 'busy':
        d.update(title='Busy', private=True)
        return d
    me = V.prof(u).emp_id if u else ''
    parts = list(m.participants.all())
    mine = next((p for p in parts if p.emp_id and p.emp_id == me), None)
    d.update(title=m.title, customType=m.custom_type, organizer=m.organizer_emp, organizerName=(em.get(m.organizer_emp) or {}).get('name', ''),
             locationType=m.location_type, room=m.room.name if m.room_id else '', roomId=str(m.room_id or ''), location=m.location_text, platform=m.platform,
             joinUrl=m.join_url, myRsvp=mine.rsvp if mine else '', canManage=bool(u and can_manage(u, m)),
             people=[{'empId': p.emp_id, 'name': (em.get(p.emp_id) or {}).get('name', '') if p.emp_id else p.ext_name, 'ext': not p.emp_id, 'rsvp': p.rsvp,
                      'role': p.role} for p in parts][:200], count=len(parts))
    if full:
        d.update(departments=m.departments, clientId=m.client_rid, clientName=m.client_name, clientCompany=m.client_company, clientLocation=m.client_location,
                 candidateId=m.candidate_rid, agenda=m.agenda, description=m.description, reminders=m.reminder_offsets, recordOnly=m.record_only,
                 cancelReason=m.cancel_reason, sequence=m.sequence, seriesRule=m.series.rule if m.series_id else None,
                 participants=[{'id': str(p.pk), 'empId': p.emp_id, 'name': (em.get(p.emp_id) or {}).get('name', '') if p.emp_id else p.ext_name,
                                'email': p.ext_email, 'phone': p.ext_phone, 'company': p.ext_company, 'role': p.role, 'rsvp': p.rsvp, 'note': p.rsvp_note,
                                'attendance': p.attendance} for p in parts])
        mn = MeetingMinutes.objects.filter(meeting=m).first()
        d['minutes'] = {'summary': mn.summary, 'discussion': mn.discussion, 'decisions': mn.decisions, 'followUp': mn.follow_up_date.isoformat() if mn.follow_up_date else '',
                        'status': mn.status, 'publishedAt': iso(mn.published_at), 'locked': minutes_locked(mn)} if mn else None
        tasks = {r.rid: r.data for r in Record.objects.filter(coll='tasks', rid__in=[a.task_rid for a in m.action_items.all() if a.task_rid])}
        d['actions'] = [{'id': str(a.pk), 'title': a.title, 'assignee': a.assignee_emp, 'assigneeName': (em.get(a.assignee_emp) or {}).get('name', ''),
                         'due': a.due_date.isoformat() if a.due_date else '', 'priority': a.priority, 'estMin': a.est_min, 'taskId': a.task_rid or '',
                         'taskStatus': (tasks.get(a.task_rid) or {}).get('status', '') if a.task_rid else ''} for a in m.action_items.all()]
        from core.models import CalendarEventLink
        d['sync'] = [{'provider': l.connection.provider, 'status': l.status, 'error': l.last_error} for l in CalendarEventLink.objects.filter(meeting=m).select_related('connection')]
    return d


def minutes_locked(mn):
    return bool(mn and mn.published_at and now() > mn.published_at + datetime.timedelta(days=7))


# ---------- conflicts ----------
def _overlap_q(start, end):
    return Q(start_at__lt=end, end_at__gt=start, status='scheduled')


def busy_minutes(eid, day):
    from .branches import branch_of_emp
    br = branch_of_emp(eid)
    z = tz(br.timezone)
    a = datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=z)
    b = a + datetime.timedelta(days=1)
    tot = 0
    for m in Meeting.objects.filter(_overlap_q(a, b), participants__in=MP.objects.filter(emp_id=eid).exclude(rsvp='declined')).distinct():
        tot += int((min(m.end_at, b) - max(m.start_at, a)).total_seconds() // 60)
    return tot


def conflicts(start, end, emp_ids, room_id=None, exclude=(), external=True):
    out = {'room': [], 'people': []}
    if room_id:
        for m in Meeting.objects.filter(_overlap_q(start, end), room_id=room_id).exclude(pk__in=exclude):
            out['room'].append({'id': str(m.pk), 'start': iso(m.start_at), 'end': iso(m.end_at), 'title': 'Booked'})
    em = emp_map()
    from .branches import branch_by_id, head_office
    from .worktime import on_leave
    events = [r.data for r in Record.objects.filter(coll='events') if r.data.get('type') == 'holiday']
    ids = [e for e in emp_ids if e]
    busy = {}
    for m in Meeting.objects.filter(_overlap_q(start, end)).exclude(pk__in=exclude).filter(Q(organizer_emp__in=ids) | Q(participants__emp_id__in=ids)).distinct().prefetch_related('participants'):
        for p in m.participants.all():
            if p.emp_id in ids and p.rsvp != 'declined':
                busy.setdefault(p.emp_id, []).append(m)
        if m.organizer_emp in ids:
            busy.setdefault(m.organizer_emp, []).append(m)
    for eid in ids:
        e = em.get(eid) or {}
        nm = e.get('name', '')
        for m in {x.pk: x for x in busy.get(eid, [])}.values():
            lt = local(m.start_at, m.timezone)
            out['people'].append({'empId': eid, 'name': nm, 'type': 'meeting', 'detail': '%s %s–%s' % ('Another meeting' if m.visibility == 'private' else m.title, lt.strftime('%H:%M'), local(m.end_at, m.timezone).strftime('%H:%M'))})
        br = branch_by_id(e.get('branchId')) or head_office()
        ls, le = local(start, br.timezone), local(end, br.timezone)
        day = ls.date()
        if on_leave(eid, day) or any(r.data.get('eid') == eid and r.data.get('status') == 'Approved' and str(r.data.get('from', '')) <= day.isoformat() <= str(r.data.get('to') or r.data.get('from'))
                                     for r in Record.objects.filter(coll='leaves')):
            out['people'].append({'empId': eid, 'name': nm, 'type': 'leave', 'detail': 'On leave'})
        if (day.isoweekday() % 7) not in (br.work_days or [1, 2, 3, 4, 5]):
            out['people'].append({'empId': eid, 'name': nm, 'type': 'off', 'detail': 'Weekly off'})
        elif any(ev.get('date', '') <= day.isoformat() <= (ev.get('end') or ev.get('date', '')) and (not ev.get('branchIds') or str(e.get('branchId', '')) in ev['branchIds']) for ev in events):
            out['people'].append({'empId': eid, 'name': nm, 'type': 'off', 'detail': 'Holiday'})
        ws, we = hm_to_min(br.work_start), hm_to_min(br.work_end)
        if ws is not None and we is not None and (ls.hour * 60 + ls.minute < ws or le.hour * 60 + le.minute > we or le.date() != ls.date()):
            out['people'].append({'empId': eid, 'name': nm, 'type': 'hours', 'detail': 'Outside working hours (%s–%s)' % (br.work_start, br.work_end)})
    if external:
        try:
            from .calsync import freebusy
            for eid, blocks in freebusy(ids, start, end).items():
                if blocks:
                    out['people'].append({'empId': eid, 'name': (em.get(eid) or {}).get('name', ''), 'type': 'external_busy', 'detail': 'Busy in their calendar'})
        except Exception:
            pass
    return out


@action('meetings.conflicts', module='meetings')
def a_conflicts(request, u, b):
    f = _times(b)
    ids = [str(p.get('empId')) for p in (b.get('participants') or []) if isinstance(p, dict) and p.get('empId')]
    org = str(b.get('organizer') or V.prof(u).emp_id or '')
    if org:
        ids.append(org)
    ex = [int(b['excludeId'])] if str(b.get('excludeId') or '').isdigit() else []
    room = int(b['roomId']) if str(b.get('roomId') or '').isdigit() else None
    return {'conflicts': conflicts(f['start'], f['end'], sorted(set(ids)), room, ex)}


# ---------- create / edit ----------
def _times(b, tzname=None):
    from .branches import branch_of_emp
    tzname = tzname or b.get('tz') or branch_of_emp(str(b.get('organizer') or '')).timezone
    day = parse_date(b.get('date'))
    st, en = hm_to_min(b.get('start')), hm_to_min(b.get('end'))
    if not day:
        invalid(date='Choose the date.')
    if st is None:
        invalid(start='Choose the start time.')
    if en is None and b.get('duration') not in (None, ''):
        en = st + int_in(b, 'duration', 5, 720, None, 'Duration')
    if en is None:
        invalid(end='Choose the end time.')
    if en <= st:
        invalid(end='End time must be after start time.')
    if en - st < 5 or en - st > 720:
        invalid(end='A meeting must be between 5 minutes and 12 hours.')
    if en > 24 * 60:
        invalid(end='A meeting must end on the same day.')
    z = tz(tzname)
    start = datetime.datetime.combine(day, datetime.time(st // 60, st % 60), tzinfo=z).astimezone(datetime.timezone.utc)
    end = start + datetime.timedelta(minutes=en - st)
    return {'day': day, 'start': start, 'end': end, 'tz': tzname, 'st': st, 'dur': en - st}


def _validate(u, b, existing=None):
    me = V.prof(u).emp_id
    org = str(b.get('organizer') or (existing.organizer_emp if existing else me) or '')
    if not org or not Record.objects.filter(coll='employees', rid=org).exists():
        invalid(organizer='Choose the organizer.')
    if org != me and not (existing and existing.organizer_emp == org) and not (can(u, 'meeting.manage_scope') and scope_for(u).sees(org)):
        err('FORBIDDEN', 'You can organise meetings only for yourself.')
    from .branches import branch_of_emp, branch_by_id
    obr = branch_of_emp(org)
    br = branch_by_id(b.get('branchId')) or (existing.branch if existing else None) or obr
    s = scope_for(u)
    if not (s.all or s.sees_branch(br.pk) or str(br.pk) == str(obr.pk)):
        invalid(branchId='Choose one of your branches.')
    f = _times(b, br.timezone)
    title = text(b, 'title', 150, req=True, mn=3, label='Title')
    cat = str(b.get('category') or '')
    if cat not in CATS:
        invalid(category='Choose a category.')
    custom = text(b, 'customType', 50)
    if cat == 'other' and len(custom) < 2:
        invalid(customType='Say what kind of meeting this is.')
    rec_only = bool(b.get('recordOnly'))
    if not rec_only and f['start'] < now() - datetime.timedelta(minutes=5):
        invalid(date='This time is in the past. Tick "Record only" to note a meeting that already happened.')
    loc = str(b.get('locationType') or CATS[cat][1])
    if cat == 'online':
        loc = 'online' if loc not in ('online', 'hybrid') else loc
    if loc not in LOC:
        invalid(locationType='Choose where the meeting happens.')
    f.update(title=title, cat=cat, custom=custom if cat == 'other' else '', loc=loc, rec_only=rec_only, org=org, branch=br)
    f['platform'] = f['url'] = f['loc_text'] = ''
    f['room'] = None
    if loc in ('online', 'hybrid'):
        f['platform'] = str(b.get('platform') or '')
        if f['platform'] not in PLATFORMS:
            invalid(platform='Choose the online platform.')
        f['url'] = text(b, 'joinUrl', 500)
        if f['url'] and not re.match(r'^https://[^\s"\'<>]+$', f['url']):
            invalid(joinUrl='Use a secure (https://) meeting link.')
        if not f['url'] and not b.get('generateLink'):
            invalid(joinUrl='Paste the meeting link, or connect your calendar to create one.')
    if loc in ('office', 'hybrid') and str(b.get('roomId') or '').isdigit():
        r = MeetingRoom.objects.filter(pk=int(b['roomId']), branch=br, status='active').first()
        if not r:
            invalid(roomId='That room is not available in this branch.')
        f['room'] = r
    if loc in ('external', 'hybrid', 'office'):
        f['loc_text'] = text(b, 'location', 200)
        if loc == 'external' and len(f['loc_text']) < 3:
            invalid(location='Enter the address or venue.')
    f['client'] = {'rid': '', 'name': '', 'company': '', 'location': ''}
    if cat == 'client':
        cid = str(b.get('clientId') or '')
        cr = Record.objects.filter(coll='clients', rid=cid).first() if cid else None
        f['client'] = {'rid': cr.rid if cr else '', 'name': text(b, 'clientName', 120) or ((cr.data or {}).get('name', '') if cr else ''),
                       'company': text(b, 'clientCompany', 120) or ((cr.data or {}).get('business', '') if cr else ''), 'location': text(b, 'clientLocation', 120)}
        if not f['client']['name']:
            invalid(clientName='Enter the client\'s name or pick a client.')
    f['depts'] = [str(x)[:80] for x in (b.get('departments') or [])][:20]
    if cat == 'team' and not f['depts']:
        invalid(departments='Choose the team or department.')
    f['candidate'] = str(b.get('candidateId') or '')[:60] if cat == 'interview' else ''
    vis = b.get('visibility')
    f['vis'] = 'private' if (vis == 'private' or (vis is None and CATS[cat][2])) else 'normal'
    ag = []
    for a in (b.get('agenda') or [])[:31]:
        if isinstance(a, dict) and str(a.get('title') or '').strip():
            ag.append({'title': str(a['title']).strip()[:150], 'presenter': str(a.get('presenter') or '')[:60], 'minutes': max(0, min(480, int(a.get('minutes') or 0))) if str(a.get('minutes') or '0').isdigit() else 0})
    if len(ag) > 30:
        invalid(agenda='At most 30 agenda items.')
    f['agenda'] = ag
    f['desc'] = text(b, 'description', 5000)
    rem = sorted(set(int(x) for x in (b.get('reminders') or []) if str(x).isdigit() and 5 <= int(x) <= 10080), reverse=True)
    if len(rem) > 3:
        invalid(reminders='At most 3 reminders.')
    f['rem'] = rem if b.get('reminders') is not None else [1440, 15]
    allowed = picker_emps(u)
    parts, seen = [], set()
    for p in (b.get('participants') or []):
        if not isinstance(p, dict):
            continue
        role = p.get('role') if p.get('role') in ('required', 'optional') else 'required'
        if p.get('empId'):
            eid = str(p['empId'])
            if eid == org or eid in seen:
                continue
            if not Record.objects.filter(coll='employees', rid=eid).exists() or (allowed is not None and eid not in allowed):
                err('NOT_FOUND', 'One of the participants was not found.')
            seen.add(eid)
            parts.append({'emp': eid, 'role': role})
        else:
            nm, em_ = str(p.get('name') or '').strip()[:120], str(p.get('email') or '').strip().lower()[:120]
            if not nm or not EMAIL_RE.match(em_):
                invalid(participants='Each outside guest needs a name and an e-mail address.')
            if em_ in seen:
                continue
            seen.add(em_)
            parts.append({'ext': True, 'name': nm, 'email': em_, 'phone': str(p.get('phone') or '')[:20], 'company': str(p.get('company') or '')[:120], 'role': role})
    if not parts and not rec_only:
        invalid(participants='Invite at least one person.')
    if len(parts) > 200:
        invalid(participants='A meeting can have at most 200 participants.')
    f['parts'] = parts
    return f


def _occurrences(f, rule):
    """Start dates for a recurring meeting (daily / weekly on days / monthly by day), max 52 or 12 months."""
    if not rule or rule.get('freq') not in ('daily', 'weekly', 'monthly'):
        return [f['day']]
    count = max(1, min(52, int(rule.get('count') or 0) or 52))
    until = parse_date(rule.get('until')) or (f['day'] + datetime.timedelta(days=366))
    until = min(until, f['day'] + datetime.timedelta(days=366))
    if not rule.get('count') and not rule.get('until'):
        invalid(recurrence='Say when the series ends (a date or a number of meetings).')
    iv = max(1, min(12, int(rule.get('interval') or 1)))
    out, d = [], f['day']
    if rule['freq'] == 'daily':
        while d <= until and len(out) < count:
            out.append(d)
            d += datetime.timedelta(days=iv)
    elif rule['freq'] == 'weekly':
        days = sorted(set(int(x) for x in (rule.get('byweekday') or [f['day'].isoweekday() % 7]) if 0 <= int(x) <= 6))
        wk = f['day'] - datetime.timedelta(days=f['day'].isoweekday() % 7)
        while wk <= until and len(out) < count:
            for x in days:
                dd = wk + datetime.timedelta(days=x)
                if f['day'] <= dd <= until and len(out) < count:
                    out.append(dd)
            wk += datetime.timedelta(weeks=iv)
    else:
        dom = f['day'].day
        y, mo = f['day'].year, f['day'].month
        while len(out) < count:
            try:
                dd = datetime.date(y, mo, dom)
                if dd > until:
                    break
                out.append(dd)
            except ValueError:
                pass
            mo += iv
            while mo > 12:
                mo -= 12
                y += 1
            if datetime.date(y, mo, 1) > until:
                break
    return out


def _apply(m, f, u, day=None):
    day = day or f['day']
    z = tz(f['tz'])
    st = datetime.datetime.combine(day, datetime.time(f['st'] // 60, f['st'] % 60), tzinfo=z).astimezone(datetime.timezone.utc)
    m.title, m.category, m.custom_type, m.start_at, m.end_at, m.timezone = f['title'], f['cat'], f['custom'], st, st + datetime.timedelta(minutes=f['dur']), f['tz']
    m.organizer_emp = f['org']
    ou = V.Profile.objects.filter(emp_id=f['org']).select_related('user').first()
    m.organizer_user = ou.user if ou else (u if V.prof(u).emp_id == f['org'] else None)
    m.branch, m.departments, m.location_type, m.room, m.location_text = f['branch'], f['depts'], f['loc'], f['room'], f['loc_text']
    m.platform, m.join_url = f['platform'], f['url']
    m.client_rid, m.client_name, m.client_company, m.client_location = f['client']['rid'], f['client']['name'], f['client']['company'], f['client']['location']
    m.candidate_rid, m.agenda, m.description, m.visibility, m.reminder_offsets, m.record_only = f['candidate'], f['agenda'], f['desc'], f['vis'], f['rem'], f['rec_only']


def _sync_parts(m, f):
    keep = set()
    MP.objects.get_or_create(meeting=m, emp_id=f['org'], defaults={'role': 'organizer', 'rsvp': 'accepted'})
    keep.add(('e', f['org']))
    added, removed = [], []
    for p in f['parts']:
        if p.get('emp'):
            o, c = MP.objects.get_or_create(meeting=m, emp_id=p['emp'], defaults={'role': p['role']})
            if not c and o.role != p['role'] and o.role != 'organizer':
                o.role = p['role']
                o.save(update_fields=['role'])
            keep.add(('e', p['emp']))
            if c:
                added.append(p['emp'])
        else:
            o, c = MP.objects.get_or_create(meeting=m, ext_email=p['email'], defaults={'ext_name': p['name'], 'ext_phone': p['phone'], 'ext_company': p['company'], 'role': p['role']})
            keep.add(('x', p['email']))
    for p in list(m.participants.all()):
        k = ('e', p.emp_id) if p.emp_id else ('x', p.ext_email)
        if k not in keep:
            if p.emp_id:
                removed.append(p.emp_id)
            p.delete()
    return added, removed


def internal_recipients(m, exclude_declined=True):
    return sorted(set(p.emp_id for p in m.participants.all() if p.emp_id and (not exclude_declined or p.rsvp != 'declined')))


def schedule_reminders(m):
    jobs.cancel('mrem:%s:' % m.pk)
    if m.record_only or m.status != 'scheduled':
        return
    for off in m.reminder_offsets or []:
        at = m.start_at - datetime.timedelta(minutes=int(off))
        if at > now():
            jobs.enqueue('meetings.remind', {'m': m.pk, 'off': int(off), 'start': iso(m.start_at)}, run_at=at, unique_key='mrem:%s:%s:%s' % (m.pk, off, iso(m.start_at)))


@jobs.handler('meetings.remind')
def _remind(payload, job):
    m = Meeting.objects.filter(pk=payload.get('m'), status='scheduled').first()
    if not m or iso(m.start_at) != payload.get('start'):
        return
    from . import notify
    off = int(payload.get('off') or 0)
    notify.emit('meeting.reminder', 'meeting', str(m.pk), '%s:%s' % (off, iso(m.start_at)), internal_recipients(m),
                {'meeting_id': m.pk, 'start': iso(m.start_at), 'urgent': off < 120})


def _after_save(m, u, created, added, removed, material, series_first=None):
    from . import notify
    if m.record_only:
        return
    if created and (series_first is None or series_first.pk == m.pk):
        notify.emit('meeting.invitation', 'meeting', str(m.pk), 'inv:%s' % m.sequence, [x for x in internal_recipients(m) if x != m.organizer_emp], {'meeting_id': m.pk})
    elif added:
        notify.emit('meeting.invitation', 'meeting', str(m.pk), 'inv:%s:%s' % (m.sequence, int(time.time())), added, {'meeting_id': m.pk})
    if material and not created:
        notify.emit('meeting.updated', 'meeting', str(m.pk), 'upd:%s' % m.sequence, [x for x in internal_recipients(m) if x != m.organizer_emp],
                    {'meeting_id': m.pk, 'urgent': (m.start_at - now()) < datetime.timedelta(hours=2)})
    if removed:
        notify.emit('meeting.cancelled', 'meeting', str(m.pk), 'rm:%s:%s' % (m.sequence, int(time.time())), removed, {'meeting_id': m.pk, 'urgent': True})
    schedule_reminders(m)
    from . import calsync
    calsync.queue(m)


@action('meetings.save', module='meetings')
def a_save(request, u, b):
    mid = b.get('id')
    m0 = _get(u, mid, manage=True) if mid else None
    if m0 and m0.status != 'scheduled':
        err('INVALID_STATE', 'Cancelled or completed meetings cannot be changed.')
    f = _validate(u, b, m0)
    scope = b.get('scope') if b.get('scope') in ('this', 'following', 'series') else 'this'
    rule = b.get('recurrence') if (not m0 and isinstance(b.get('recurrence'), dict)) else None
    days = _occurrences(f, rule) if not m0 else None
    targets = [m0] if m0 else []
    if m0 and m0.series_id and scope != 'this':
        q = Meeting.objects.filter(series_id=m0.series_id, status='scheduled')
        if scope == 'following':
            q = q.filter(start_at__gte=m0.start_at)
        targets = list(q.order_by('start_at'))
    # conflicts for the first (or only) slot; series checks every date for room clashes
    ids = [p['emp'] for p in f['parts'] if p.get('emp')] + [f['org']]
    ex = [t.pk for t in targets]
    slots = []
    if days:
        slots = [d for d in days]
    else:
        slots = [f['day'] if (t is m0 and scope == 'this') else local(t.start_at, t.timezone).date() for t in targets]
    room_hits, people = [], []
    for i, d in enumerate(slots):
        z = tz(f['tz'])
        s0 = datetime.datetime.combine(d, datetime.time(f['st'] // 60, f['st'] % 60), tzinfo=z).astimezone(datetime.timezone.utc)
        c = conflicts(s0, s0 + datetime.timedelta(minutes=f['dur']), ids, f['room'].pk if f['room'] else None, ex, external=(i == 0))
        room_hits += c['room']
        if i < 12:
            people += [dict(x, date=d.isoformat()) for x in c['people']]
    if room_hits:
        err('CONFLICT_ROOM', 'The room is already booked at this time%s.' % ('' if len(slots) == 1 else ' on %d of the dates' % len(room_hits)), meetings=room_hits[:20])
    if people and not b.get('confirmConflicts') and not f['rec_only']:  # a record of a past meeting is never blocked
        err('CONFLICTS', 'Some people are not free at this time.', conflicts=people[:50])
    out = []
    em = emp_map()
    with transaction.atomic():
        if not m0:
            series = MeetingSeries.objects.create(rule=rule, timezone=f['tz']) if rule and len(days) > 1 else None
            first = None
            for i, d in enumerate(days):
                m = Meeting(series=series, occurrence_index=i, created_by=u)
                _apply(m, f, u, d)
                m.save()
                added, removed = _sync_parts(m, f)
                first = first or m
                out.append(m)
            for m in out:
                _after_save(m, u, True, [], [], False, series_first=first)
            audit.record(u, 'meeting.create', 'meeting', first.pk, after={'title': f['title'], 'count': len(out)}, branch_id=f['branch'].pk, request=request)
        else:
            for t in targets:
                before = {k: getattr(t, k) for k in MATERIAL}
                d = f['day'] if (t.pk == m0.pk and scope == 'this') else local(t.start_at, t.timezone).date()
                _apply(t, f, u, d)
                material = any(before[k] != getattr(t, k) for k in MATERIAL)
                if material:
                    t.sequence += 1
                t.save()
                added, removed = _sync_parts(t, f)
                _after_save(t, u, False, added, removed, material)
                out.append(t)
            audit.record(u, 'meeting.update', 'meeting', m0.pk, after={'title': f['title'], 'scope': scope, 'count': len(out)}, branch_id=f['branch'].pk, request=request)
    return {'meeting': mdict(out[0], u, em, full=True), 'count': len(out)}


@action('meetings.cancel', module='meetings')
def a_cancel(request, u, b):
    m0 = _get(u, b.get('id'), manage=True)
    if m0.status != 'scheduled':
        err('INVALID_STATE', 'This meeting is already %s.' % m0.status)
    reason = text(b, 'reason', 300, req=True, mn=3, label='Reason')
    scope = b.get('scope') if b.get('scope') in ('this', 'following', 'series') else 'this'
    targets = [m0]
    if m0.series_id and scope != 'this':
        q = Meeting.objects.filter(series_id=m0.series_id, status='scheduled')
        targets = list(q.filter(start_at__gte=m0.start_at) if scope == 'following' else q)
    from . import notify, calsync
    for m in targets:
        m.status, m.cancel_reason, m.cancelled_at = 'cancelled', reason, now()
        m.sequence += 1
        m.save()
        jobs.cancel('mrem:%s:' % m.pk)
        notify.cancel_jobs('meeting.reminder', m.pk)
        if not m.record_only and m.end_at > now():
            notify.emit('meeting.cancelled', 'meeting', str(m.pk), 'cancel', [x for x in internal_recipients(m, False) if x != m.organizer_emp], {'meeting_id': m.pk, 'urgent': True})
            calsync.queue(m)
    audit.record(u, 'meeting.cancel', 'meeting', m0.pk, after={'reason': reason, 'count': len(targets)}, branch_id=m0.branch_id, request=request)
    return {'count': len(targets), 'meeting': mdict(m0, u, full=True)}


@action('meetings.complete', module='meetings')
def a_complete(request, u, b):
    m = _get(u, b.get('id'), manage=True)
    if m.status != 'scheduled':
        err('INVALID_STATE', 'This meeting is already %s.' % m.status)
    if m.start_at > now():
        err('INVALID_STATE', 'The meeting has not started yet.')
    m.status, m.completed_at = 'completed', now()
    m.save(update_fields=['status', 'completed_at'])
    jobs.cancel('mrem:%s:' % m.pk)
    return {'meeting': mdict(m, u, full=True)}


@action('meetings.rsvp', module='meetings')
def a_rsvp(request, u, b):
    m = _get(u, b.get('id'))
    me = V.prof(u).emp_id
    p = m.participants.filter(emp_id=me).first() if me else None
    if not p:
        err('NOT_FOUND', 'You are not invited to this meeting.')
    r = b.get('response')
    if r not in ('accepted', 'declined', 'tentative'):
        invalid(response='Choose accept, decline or maybe.')
    if m.status != 'scheduled':
        err('INVALID_STATE', 'This meeting is %s.' % m.status)
    p.rsvp, p.rsvp_at, p.rsvp_note = r, now(), text(b, 'note', 300)
    p.save()
    return {'meeting': mdict(_get(u, m.pk), u)}


@action('meetings.attendance', module='meetings')
def a_attendance(request, u, b):
    m = _get(u, b.get('id'), manage=True)
    if m.start_at > now():
        err('INVALID_STATE', 'Attendance can be marked once the meeting has started.')
    n = 0
    for x in b.get('marks') or []:
        if isinstance(x, dict) and str(x.get('participantId') or '').isdigit() and x.get('attendance') in ('present', 'late', 'absent', 'unknown'):
            p = m.participants.filter(pk=int(x['participantId'])).first()
            if p:
                if not p.emp_id and x['attendance'] == 'late':
                    x['attendance'] = 'present'
                p.attendance = x['attendance']
                p.save(update_fields=['attendance'])
                n += 1
    return {'marked': n, 'meeting': mdict(m, u, full=True)}


def _list_items(i, cap=40):
    return [str(x).strip()[:500] for x in (i or []) if str(x).strip()][:cap]


@action('meetings.minutes.save', module='meetings')
def a_minutes(request, u, b):
    m = _get(u, b.get('meetingId'), manage=True)
    if m.status == 'cancelled':
        err('INVALID_STATE', 'This meeting was cancelled.')
    if m.start_at > now():
        err('INVALID_STATE', 'Minutes can be written once the meeting has started.')
    mn = MeetingMinutes.objects.filter(meeting=m).first()
    if minutes_locked(mn) and V.role_of(u) not in ALL_ADMIN:
        err('LOCKED', 'Minutes are locked 7 days after publishing. Ask an admin to correct them.')
    if minutes_locked(mn) and len(text(b, 'correctionReason', 300)) < 5:
        invalid(correctionReason='Say why the published minutes are being corrected.')
    mn = mn or MeetingMinutes(meeting=m)
    before = {'summary': mn.summary, 'decisions': mn.decisions} if mn.pk else None
    mn.summary = text(b, 'summary', 10000)
    mn.discussion, mn.decisions = _list_items(b.get('discussion')), _list_items(b.get('decisions'))
    mn.follow_up_date = parse_date(b.get('followUp'))
    publish = bool(b.get('publish'))
    if publish:
        mn.status, mn.published_at, mn.version = 'published', mn.published_at or now(), mn.version + 1
    mn.save()
    if m.status == 'scheduled' and m.end_at <= now():
        m.status, m.completed_at = 'completed', now()
        m.save(update_fields=['status', 'completed_at'])
    if publish:
        from . import notify
        notify.emit('meeting.minutes_published', 'meeting', str(m.pk), 'min:%s' % mn.version, [x for x in internal_recipients(m, False) if x != m.organizer_emp], {'meeting_id': m.pk})
    audit.record(u, 'meeting.minutes', 'meeting', m.pk, before=before, after={'summary': mn.summary, 'decisions': mn.decisions, 'published': publish}, branch_id=m.branch_id, request=request)
    return {'meeting': mdict(m, u, full=True)}


@action('meetings.actions.save', module='meetings')
def a_actions(request, u, b):
    m = _get(u, b.get('meetingId'), manage=True)
    keep = set()
    allowed = picker_emps(u)
    for x in (b.get('items') or [])[:50]:
        if not isinstance(x, dict):
            continue
        title = str(x.get('title') or '').strip()[:150]
        if not title:
            continue
        ae = str(x.get('assignee') or '')
        if ae and (not Record.objects.filter(coll='employees', rid=ae).exists() or (allowed is not None and ae not in allowed)):
            invalid(items='An action item is assigned to someone outside your branches.')
        a = MeetingActionItem.objects.filter(pk=int(x['id']), meeting=m).first() if str(x.get('id') or '').isdigit() else None
        a = a or MeetingActionItem(meeting=m)
        if not a.task_rid:
            a.title, a.assignee_emp, a.due_date = title, ae, parse_date(x.get('due'))
            a.priority = x.get('priority') if x.get('priority') in ('high', 'med', 'low') else 'med'
            a.est_min = int(x['estMin']) if str(x.get('estMin') or '').isdigit() and 5 <= int(x['estMin']) <= 59999 else None
            a.save()
        keep.add(a.pk)
    MeetingActionItem.objects.filter(meeting=m, task_rid__isnull=True).exclude(pk__in=keep).delete()
    return {'meeting': mdict(m, u, full=True)}


def can_assign(u, eid):
    r = V.role_of(u)
    return V.rank_of(u) >= 2 and r != 'employee' and scope_for(u).sees(eid)


@action('meetings.actions.toTasks', module='meetings')
def a_to_tasks(request, u, b):
    m = _get(u, b.get('meetingId'), manage=True)
    created, refused = [], []
    from . import notify
    from .branches import branch_of_emp
    for iid in (b.get('itemIds') or [])[:50]:
        a = MeetingActionItem.objects.select_for_update().filter(pk=int(iid) if str(iid).isdigit() else 0, meeting=m).first()
        if not a:
            refused.append({'id': iid, 'reason': 'Not found'})
            continue
        if a.task_rid:
            created.append({'itemId': str(a.pk), 'taskId': a.task_rid, 'existing': True})
            continue
        if not a.assignee_emp or not a.due_date:
            refused.append({'id': str(a.pk), 'reason': 'Choose who does it and by when'})
            continue
        if not can_assign(u, a.assignee_emp):
            refused.append({'id': str(a.pk), 'reason': 'You cannot assign tasks to this person. Ask their manager.'})
            continue
        e = V.Record.objects.filter(coll='employees', rid=a.assignee_emp).first().data
        rid = 't' + format(int(time.time() * 1000), 'x') + format(a.pk, 'x')
        d = {'id': rid, 'title': a.title, 'desc': 'From meeting "%s" on %s' % (m.title, local(m.start_at, m.timezone).strftime('%d %b %Y')), 'eid': a.assignee_emp,
             'cid': m.client_rid or '', 'pri': a.priority, 'status': 'todo', 'due': a.due_date.isoformat(), 'cat': 'Meeting action', 'qty': 1, 'reopens': [], 'mistakes': [],
             'createdBy': str(u.pk), 'createdAt': iso(now()), 'branchId': str(branch_of_emp(a.assignee_emp, e).pk), 'dept': e.get('dept', ''),
             'src': {'type': 'meeting', 'id': str(m.pk), 'itemId': str(a.pk)}}
        if a.est_min:
            d['est'] = a.est_min
        Record.objects.create(coll='tasks', rid=rid, data=d)
        a.task_rid = rid
        a.save(update_fields=['task_rid'])
        notify.task_assigned(d, rid)
        created.append({'itemId': str(a.pk), 'taskId': rid})
    if any(not c.get('existing') for c in created):
        bump_rev()
    audit.record(u, 'meeting.actions_to_tasks', 'meeting', m.pk, after={'created': [c['taskId'] for c in created if not c.get('existing')]}, branch_id=m.branch_id, request=request)
    return {'created': created, 'refused': refused, 'meeting': mdict(m, u, full=True)}


# ---------- reading ----------
def _filtered(u, b, base=None):
    s = scope_for(u)
    me = V.prof(u).emp_id
    q = base if base is not None else Meeting.objects.all()
    view = b.get('view') or 'my'
    if view == 'all' and can(u, 'meeting.view_scope'):
        if not s.all:
            q = q.filter(Q(participants__emp_id__in=list(s.emps)) | Q(organizer_emp__in=list(s.emps)) | Q(branch_id__in=[int(x) for x in s.branches if x.isdigit()] if V.role_of(u) in SCOPED_ADMIN | {'branchmgr'} else []) | Q(organizer_user=u)).distinct()
    else:
        q = q.filter(Q(participants__emp_id=me) | Q(organizer_user=u) | Q(organizer_emp=me) if me else Q(organizer_user=u)).distinct()
    if str(b.get('branchId') or '').isdigit():
        q = q.filter(branch_id=int(b['branchId']))
    if b.get('category') in CATS:
        q = q.filter(category=b['category'])
    if b.get('empId'):
        eid = str(b['empId'])
        if s.sees(eid) or eid == me:
            q = q.filter(Q(participants__emp_id=eid) | Q(organizer_emp=eid)).distinct()
    if b.get('dept'):
        dept_emps = [k for k, e in emp_map().items() if e.get('dept') == b['dept']]
        q = q.filter(Q(participants__emp_id__in=dept_emps) | Q(organizer_emp__in=dept_emps)).distinct()
    if b.get('status') in ('scheduled', 'completed', 'cancelled'):
        q = q.filter(status=b['status'])
    return q


@action('meetings.list', module='meetings', read=True)
def a_list(request, u, b):
    q = _filtered(u, b).select_related('room').prefetch_related('participants')
    d0, d1 = parse_date(b.get('from')), parse_date(b.get('to'))
    if d0:
        q = q.filter(end_at__gte=datetime.datetime.combine(d0, datetime.time(0), tzinfo=tz(None)))
    if d1:
        q = q.filter(start_at__lt=datetime.datetime.combine(d1 + datetime.timedelta(days=1), datetime.time(0), tzinfo=tz(None)))
    q = q.order_by('-start_at' if b.get('order') == 'desc' else 'start_at')
    em = emp_map()
    return paged(q, b, lambda m: mdict(m, u, em, level=can_view(u, m) or 'busy'))


@action('meetings.get', module='meetings', read=True)
def a_get(request, u, b):
    m = _get(u, b.get('id'))
    lv = can_view(u, m)
    return {'meeting': mdict(m, u, full=(lv == 'full'), level=lv)}


@action('meetings.widget', module='meetings', read=True)
def a_widget(request, u, b):
    t = now()
    from .branches import branch_of_emp
    me = V.prof(u).emp_id
    z = branch_of_emp(me).timezone if me else None
    day = parse_date(b.get('date')) or local(t, z).date()
    d0 = datetime.datetime.combine(day, datetime.time(0), tzinfo=tz(z))
    d1 = d0 + datetime.timedelta(days=1)
    base = _filtered(u, dict(b, view=b.get('view') or 'my')).select_related('room').prefetch_related('participants')
    tabs = {
        'today': base.filter(start_at__lt=d1, end_at__gt=d0).exclude(status='cancelled').order_by('start_at'),
        'upcoming': base.filter(start_at__gte=d1, start_at__lt=d0 + datetime.timedelta(days=8), status='scheduled').order_by('start_at'),
        'completed': base.filter(Q(status='completed') | Q(status='scheduled', end_at__lt=t), start_at__gte=d0 - datetime.timedelta(days=7), start_at__lt=d1).order_by('-start_at'),
        'cancelled': base.filter(status='cancelled', start_at__gte=d0 - datetime.timedelta(days=7), start_at__lt=d0 + datetime.timedelta(days=8)).order_by('-start_at'),
    }
    tab = b.get('tab') if b.get('tab') in tabs else 'today'
    em = emp_map()
    items = [mdict(m, u, em, level=can_view(u, m) or 'busy') for m in tabs[tab][:8]]
    nxt = base.filter(status='scheduled', end_at__gt=t).order_by('start_at').first()
    return {'tab': tab, 'items': items, 'counts': {k: q.count() for k, q in tabs.items()}, 'nextUp': mdict(nxt, u, em, level=can_view(u, nxt) or 'busy') if nxt else None,
            'date': day.isoformat()}


@action('meetings.calendar', module='meetings', read=True)
def a_calendar(request, u, b):
    d0, d1 = parse_date(b.get('from')), parse_date(b.get('to'))
    if not d0 or not d1 or d1 < d0 or (d1 - d0).days > 42:
        invalid(to='Choose a range of up to 6 weeks.')
    z = tz(None)
    q = _filtered(u, b).filter(start_at__lt=datetime.datetime.combine(d1 + datetime.timedelta(days=1), datetime.time(0), tzinfo=z),
                               end_at__gt=datetime.datetime.combine(d0, datetime.time(0), tzinfo=z)).exclude(status='cancelled').select_related('room').prefetch_related('participants').order_by('start_at')
    em = emp_map()
    s = scope_for(u)
    leave = []
    for r in Record.objects.filter(coll='leaves'):
        l = r.data
        if l.get('status') == 'Approved' and s.sees(l.get('eid', ''), primary_only=True) and str(l.get('from', '')) <= d1.isoformat() and str(l.get('to') or l.get('from', '')) >= d0.isoformat():
            leave.append({'empId': l.get('eid'), 'name': (em.get(l.get('eid')) or {}).get('name', ''), 'from': l.get('from'), 'to': l.get('to') or l.get('from'), 'kind': l.get('kind')})
    hol = [{'title': r.data.get('title', ''), 'from': r.data.get('date'), 'to': r.data.get('end') or r.data.get('date'), 'branchIds': r.data.get('branchIds') or []}
           for r in Record.objects.filter(coll='events') if r.data.get('type') == 'holiday']
    return {'items': [mdict(m, u, em, level=can_view(u, m) or 'busy') for m in q[:500]], 'leave': leave, 'holidays': hol}


# ---------- RSVP link for WhatsApp / e-mail (external guests too) ----------
def rsvp_token(m, p):
    return signing.dumps({'m': m.pk, 'p': p.pk}, salt='mtg-rsvp', compress=True)


def _page(title, body):
    return HttpResponse('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>'
                        '<body style="font-family:system-ui;max-width:520px;margin:10vh auto;padding:0 20px;color:#1b2333">%s</body>' % (html.escape(title), body))


@csrf_exempt
def rsvp_page(request, token):
    try:
        d = signing.loads(token, salt='mtg-rsvp', max_age=180 * 86400)
    except signing.BadSignature:
        return _page('Link expired', '<h2>This link has expired</h2>')
    m = Meeting.objects.filter(pk=d.get('m')).first()
    p = MP.objects.filter(pk=d.get('p'), meeting=m).first() if m else None
    if not m or not p or m.end_at < now():
        return _page('Link expired', '<h2>This link has expired</h2>')
    if p.emp_id:  # staff open the meeting inside the app (sign-in required)
        return HttpResponseRedirect('/#meetings/%s' % m.pk)
    msg = ''
    if request.method == 'POST' and request.POST.get('r') in ('accepted', 'declined', 'tentative') and m.status == 'scheduled':
        p.rsvp, p.rsvp_at = request.POST['r'], now()
        p.save(update_fields=['rsvp', 'rsvp_at'])
        msg = '<p><b>Thank you, your reply was saved.</b></p>'
    lt = local(m.start_at, m.timezone)
    btn = lambda v, l: '<button name="r" value="%s" style="padding:10px 16px;margin-right:8px">%s</button>' % (v, l)
    st = '<p><b>This meeting has been cancelled.</b></p>' if m.status == 'cancelled' else (
        '<form method="post">%s%s%s</form>' % (btn('accepted', 'Accept'), btn('tentative', 'Maybe'), btn('declined', 'Decline')))
    return _page(m.title, '<h2>%s</h2><p>%s at %s (%s)</p><p>Your reply: <b>%s</b></p>%s%s' % (
        html.escape(m.title), lt.strftime('%a %d %b %Y'), lt.strftime('%H:%M'), html.escape(m.timezone), html.escape(p.rsvp), msg, st))
