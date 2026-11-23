"""One notification service for every module (docs/release-v6/04-specifications.md section 1).

emit() is called inside the business transaction (outbox). In-app items are written at once (no external call);
WhatsApp / SMS / e-mail deliveries are queued and sent by the job runner (send_due), with retry, SMS fallback,
consent, quiet hours, a monthly cap and a unique dedupe key per recipient and channel.
"""
import csv
import datetime
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core import views as V
from core.models import (ConsentEvent, ContactProfile, NotificationCampaign, NotificationDelivery as D, NotificationEventConfig as EC,
                         NotificationJob as NJ, NotificationProvider as P, NotificationTemplate as T, OtpChallenge, Record, WebhookLog)
from . import audit, crypto, jobs
from .base import action, err, invalid, need, can, text, now, iso, local, tz, paged, emp_map, parse_dt, ALL_ADMIN
from .scope import scope_for

log = logging.getLogger('hub.notify')
NOTICE_VERSION = '2026-11'

# event_key: (label, group, default channels, mandatory, whatsapp category)
EVENTS = {
    'employee.welcome': ('New employee welcome', 'general', ['whatsapp', 'inapp'], False, 'utility'),
    'attendance.reminder': ('Attendance reminder', 'attendance', ['whatsapp', 'inapp'], False, 'utility'),
    'attendance.late': ('Late attendance alert', 'attendance', ['whatsapp', 'inapp'], False, 'utility'),
    'leave.submitted': ('Leave request submitted', 'leave', ['whatsapp', 'inapp'], False, 'utility'),
    'leave.decided': ('Leave approved or rejected', 'leave', ['whatsapp', 'inapp'], True, 'utility'),
    'task.assigned': ('New task assigned', 'tasks', ['whatsapp', 'inapp'], False, 'utility'),
    'task.deadline_reminder': ('Task deadline reminder', 'tasks', ['whatsapp', 'inapp'], False, 'utility'),
    'task.completed': ('Task completed', 'tasks', ['inapp'], False, 'utility'),
    'task.time_exceeded': ('Task time exceeded', 'tasks', ['inapp'], False, 'utility'),
    'meeting.invitation': ('Meeting invitation', 'meetings', ['whatsapp', 'inapp'], False, 'utility'),
    'meeting.reminder': ('Meeting reminder', 'meetings', ['whatsapp', 'inapp'], False, 'utility'),
    'meeting.updated': ('Meeting updated', 'meetings', ['whatsapp', 'inapp'], True, 'utility'),
    'meeting.cancelled': ('Meeting cancelled', 'meetings', ['whatsapp', 'inapp'], True, 'utility'),
    'meeting.minutes_published': ('Meeting minutes published', 'meetings', ['inapp', 'email'], False, 'utility'),
    'employee.birthday': ('Birthday wishes', 'celebrations', ['inapp'], False, 'marketing'),
    'branch.announcement': ('Branch announcement', 'announcements', ['whatsapp', 'inapp'], False, 'utility'),
    'time.manual_submitted': ('Manual time waiting for approval', 'time', ['inapp'], False, 'utility'),
    'time.manual_decided': ('Manual time approved or rejected', 'time', ['inapp'], False, 'utility'),
    'time.auto_stopped': ('Timer stopped automatically', 'time', ['inapp'], False, 'utility'),
    'employee.transferred': ('Branch transfer', 'general', ['whatsapp', 'inapp'], False, 'utility'),
}
GROUPS = ['attendance', 'leave', 'tasks', 'meetings', 'time', 'celebrations', 'announcements', 'general']
TPL = {  # default wording: title | body. Variables in {{name}} form.
    'employee.welcome': ('Welcome to {{company_name}}', 'Hi {{recipient_name}}, welcome to {{company_name}} ({{branch_name}}). You will get important updates about attendance, leave, tasks and meetings here.'),
    'attendance.reminder': ('Check-in reminder', 'Hi {{recipient_name}}, your shift starts at {{shift_start}}. Please remember to check in.'),
    'attendance.late': ('You are late today', 'Hi {{recipient_name}}, your shift started at {{shift_start}} and you have not checked in on time today.'),
    'leave.submitted': ('New leave request', 'Hi {{recipient_name}}, {{employee_name}} has requested leave for {{leave_dates}}. Please review it.'),
    'leave.decided': ('Leave {{leave_status}}', 'Hi {{recipient_name}}, your leave for {{leave_dates}} is {{leave_status}}.'),
    'task.assigned': ('New task: {{task_name}}', 'Hi {{recipient_name}}, a new task "{{task_name}}" has been assigned to you. Due {{due_date}}. Estimate: {{estimated_time}}.'),
    'task.deadline_reminder': ('Task due {{due_date}}', 'Hi {{recipient_name}}, your task "{{task_name}}" is due on {{due_date}}.'),
    'task.completed': ('Task completed: {{task_name}}', '{{employee_name}} completed "{{task_name}}". Time spent {{actual_time}} (estimate {{estimated_time}}).'),
    'task.time_exceeded': ('Time alert: {{task_name}}', '"{{task_name}}" ({{employee_name}}) has used {{actual_time}} of an estimated {{estimated_time}}.'),
    'meeting.invitation': ('Meeting: {{meeting_title}}', 'Hi {{recipient_name}}, you are invited to "{{meeting_title}}" on {{meeting_date}} at {{meeting_time}} ({{meeting_location}}). Organizer: {{organizer_name}}.'),
    'meeting.reminder': ('Starting soon: {{meeting_title}}', 'Reminder: "{{meeting_title}}" on {{meeting_date}} at {{meeting_time}} ({{meeting_location}}).'),
    'meeting.updated': ('Meeting changed: {{meeting_title}}', '"{{meeting_title}}" is now on {{meeting_date}} at {{meeting_time}} ({{meeting_location}}).'),
    'meeting.cancelled': ('Meeting cancelled: {{meeting_title}}', '"{{meeting_title}}" on {{meeting_date}} at {{meeting_time}} has been cancelled. {{reason}}'),
    'meeting.minutes_published': ('Minutes: {{meeting_title}}', 'Minutes for "{{meeting_title}}" are ready. Open the app to read the decisions and action items.'),
    'employee.birthday': ('Happy birthday!', 'Happy birthday, {{recipient_name}}! Best wishes from everyone at {{company_name}}.'),
    'branch.announcement': ('{{announcement_title}}', 'Hi {{recipient_name}}, new announcement from {{branch_name}}: {{announcement_title}}. Open the app for details.'),
    'time.manual_submitted': ('Time entry to approve', '{{employee_name}} added {{duration}} on "{{task_name}}" and is waiting for your approval.'),
    'time.manual_decided': ('Time entry {{decision}}', 'Your time entry on "{{task_name}}" was {{decision}}. {{comment}}'),
    'time.auto_stopped': ('Timer stopped automatically', 'Your timer on "{{task_name}}" was stopped at {{stop_time}}. Please check it and send a correction if needed.'),
    'employee.transferred': ('You moved to {{branch_name}}', 'Hi {{recipient_name}}, from {{effective_date}} you are part of {{branch_name}}.'),
    'contact.otp': ('Verification code', '{{code}} is your verification code for {{company_name}}. It expires in 10 minutes.'),
}
LINKS = {'task': 'tasks', 'leave': 'leave', 'meeting': 'meetings', 'work_session': 'tasks', 'time_adjustment': 'tasks', 'transfer': 'me', 'notice': 'notices'}


# ---------- config ----------
def ncfg():
    c = V.cget('v6notify', {}) or {}
    c.setdefault('caps', {})
    c.setdefault('quiet', {'from': '21:00', 'to': '08:00'})
    return c


def event_cfg(key):
    e = EC.objects.filter(event_key=key).first()
    if e is None and key in EVENTS:
        lab, grp, ch, mand, cat = EVENTS[key]
        e, _ = EC.objects.get_or_create(event_key=key, defaults={'channels': ch, 'mandatory': mand, 'enabled': True})
    return e


def template_for(key, channel, lang='en'):
    q = T.objects.filter(event_key=key, channel=channel, active=True)
    return q.filter(language=lang).order_by('-version').first() or q.filter(language='en').order_by('-version').first()


def ensure_defaults():
    """Seed event configs and default templates (idempotent). In-app and e-mail templates are usable at once;
    WhatsApp and SMS templates start as drafts until approved by Meta / registered on DLT."""
    for key, (lab, grp, ch, mand, cat) in EVENTS.items():
        EC.objects.get_or_create(event_key=key, defaults={'channels': ch, 'mandatory': mand, 'enabled': True})
    for key, (title, body) in TPL.items():
        cat = EVENTS.get(key, (None, None, None, None, 'authentication'))[4]
        for chn in ('inapp', 'email', 'whatsapp', 'sms'):
            if not T.objects.filter(event_key=key, channel=chn).exists():
                T.objects.create(event_key=key, channel=chn, name='%s (%s)' % (key, chn), subject=title, body=body, variables=re.findall(r'{{(\w+)}}', body),
                                 category=cat, approval_status='approved' if chn in ('inapp', 'email') else 'draft',
                                 provider_template_name=key.replace('.', '_') if chn == 'whatsapp' else '')


# ---------- phone, consent ----------
def normalize_phone(s, cc='91'):
    d = re.sub(r'[^\d+]', '', str(s or ''))
    if not d:
        return ''
    if d.startswith('+'):
        digits = d[1:]
    elif d.startswith('00'):
        digits = d[2:]
    elif cc == '91' and len(d) == 11 and d.startswith('0'):
        digits = '91' + d[1:]
    elif cc == '91' and len(d) == 10:
        digits = '91' + d
    elif len(d) > 10:
        digits = d
    else:
        return ''
    if not digits.isdigit() or not 8 <= len(digits) <= 15:
        return ''
    if digits.startswith('91') and (len(digits) != 12 or digits[2] not in '6789'):
        return ''
    return '+' + digits


def contact(eid, create=True):
    c = ContactProfile.objects.filter(emp_id=eid).first()
    if c or not create:
        return c
    e = V.Record.objects.filter(coll='employees', rid=eid).first()
    try:
        with transaction.atomic():
            return ContactProfile.objects.create(emp_id=eid, phone_e164=normalize_phone((e.data if e else {}).get('phone')))
    except IntegrityError:
        return ContactProfile.objects.get(emp_id=eid)


def phone_changed(eid, new_phone):
    """Called by the employee save path: a new number must be verified again (FR-NOT-18)."""
    c = contact(eid, create=False)
    if not c:
        return
    n = normalize_phone(new_phone)
    if n != c.phone_e164:
        c.phone_e164, c.phone_verified_at, c.verify_method, c.wa_opt_in, c.sms_opt_in = n, None, '', False, False
        c.save()
        ConsentEvent.objects.create(contact=c, channel='all', action='reset', source='app', note='Phone changed')


def prefs_of(c):
    p = dict((c.prefs if c else {}) or {})
    p.setdefault('groups', {})
    p.setdefault('order', ['whatsapp', 'sms'])
    p.setdefault('quiet', ncfg()['quiet'])
    return p


# ---------- providers ----------
class SendError(Exception):
    def __init__(self, msg, temporary=False, code=''):
        super().__init__(msg)
        self.temporary, self.code = temporary, code


def _http(method, url, headers=None, data=None, timeout=12):
    body = None
    h = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode()
        h['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode('utf-8', 'replace')
            return r.status, (json.loads(raw) if raw.strip().startswith(('{', '[')) else {'raw': raw})
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            j = json.loads(raw)
        except ValueError:
            j = {'raw': raw[:300]}
        return e.code, j
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SendError('Network error: %s' % e, temporary=True, code='network')


def provider(channel):
    p = P.objects.filter(channel=channel).first()
    return p if (p and p.enabled) else None


class MetaCloud:
    """WhatsApp Business Platform (Cloud API). config: phone_number_id, waba_id, api_version. secrets: token, app_secret."""
    BASE = 'https://graph.facebook.com/'

    def __init__(self, p):
        self.p, self.cfg, self.sec = p, p.config_public or {}, crypto.dec(p.secrets_enc, {}) or {}
        self.ver = self.cfg.get('api_version') or 'v21.0'

    def _h(self):
        return {'Authorization': 'Bearer %s' % self.sec.get('token', '')}

    def test(self):
        st, j = _http('GET', '%s%s/%s?fields=display_phone_number,verified_name,quality_rating,messaging_limit_tier' % (self.BASE, self.ver, self.cfg.get('phone_number_id', '')), self._h())
        if st != 200:
            raise SendError((j.get('error') or {}).get('message') or 'WhatsApp returned HTTP %s' % st)
        return {'number': j.get('display_phone_number'), 'name': j.get('verified_name'), 'quality': j.get('quality_rating'), 'tier': j.get('messaging_limit_tier')}

    def send(self, to, tpl, params, lang='en'):
        if not tpl or tpl.approval_status != 'approved' or not tpl.provider_template_name:
            raise SendError('No approved WhatsApp template', code='no_template')
        comp = [{'type': 'body', 'parameters': [{'type': 'text', 'text': (re.sub(r'\s{4,}', '   ', str(v).replace('\n', ' ')) or '-')[:1000]} for v in params]}] if params else []
        data = {'messaging_product': 'whatsapp', 'to': to.lstrip('+'), 'type': 'template',
                'template': {'name': tpl.provider_template_name, 'language': {'code': tpl.language if tpl.language != 'en' else (self.cfg.get('lang') or 'en')}, 'components': comp}}
        st, j = _http('POST', '%s%s/%s/messages' % (self.BASE, self.ver, self.cfg.get('phone_number_id', '')), self._h(), data)
        if st in (200, 201) and j.get('messages'):
            return j['messages'][0].get('id', '')
        e = j.get('error') or {}
        code = str(e.get('code', st))
        temp = st >= 500 or st == 429 or code in ('4', '80007', '130429', '131016', '131056')
        raise SendError(e.get('message') or 'WhatsApp HTTP %s' % st, temporary=temp, code=code)

    def sync_templates(self):
        st, j = _http('GET', '%s%s/%s/message_templates?limit=200' % (self.BASE, self.ver, self.cfg.get('waba_id', '')), self._h())
        if st != 200:
            raise SendError((j.get('error') or {}).get('message') or 'HTTP %s' % st)
        status = {}
        for t in j.get('data') or []:
            status[(t.get('name'), t.get('language'))] = str(t.get('status', '')).lower()
        n = 0
        for t in T.objects.filter(channel='whatsapp').exclude(provider_template_name=''):
            s = status.get((t.provider_template_name, t.language)) or status.get((t.provider_template_name, 'en')) or status.get((t.provider_template_name, 'en_US'))
            if s:
                t.approval_status = {'approved': 'approved', 'rejected': 'rejected', 'paused': 'paused', 'pending': 'submitted', 'disabled': 'paused'}.get(s, t.approval_status)
                t.save(update_fields=['approval_status'])
                n += 1
        return n

    def submit(self, tpl):
        i = [0]
        def num(m):
            i[0] += 1
            return '{{%d}}' % i[0]
        body = re.sub(r'{{\w+}}', num, tpl.body)
        data = {'name': tpl.provider_template_name, 'language': tpl.language if tpl.language != 'en' else 'en', 'category': tpl.category.upper() if tpl.category in ('utility', 'marketing', 'authentication') else 'UTILITY',
                'components': [{'type': 'BODY', 'text': body, 'example': {'body_text': [['Example'] * len(tpl.variables)]} if tpl.variables else None}]}
        if not tpl.variables:
            data['components'][0].pop('example')
        st, j = _http('POST', '%s%s/%s/message_templates' % (self.BASE, self.ver, self.cfg.get('waba_id', '')), self._h(), data)
        if st not in (200, 201):
            raise SendError((j.get('error') or {}).get('message') or 'HTTP %s' % st)
        return j.get('status', 'PENDING')

    def verify_signature(self, body, header):
        secret = self.sec.get('app_secret', '')
        if not secret or not header.startswith('sha256='):
            return False
        return hmac.compare_digest(header[7:], hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())


class Msg91:
    """MSG91 Flow API (supports India DLT: template id registered on DLT). config: sender, dlt_entity_id. secrets: authkey."""
    URL = 'https://control.msg91.com/api/v5/flow'

    def __init__(self, p):
        self.p, self.cfg, self.sec = p, p.config_public or {}, crypto.dec(p.secrets_enc, {}) or {}

    def test(self):
        st, j = _http('GET', 'https://control.msg91.com/api/v5/report/balance?' + urllib.parse.urlencode({'type': '4'}), {'authkey': self.sec.get('authkey', '')})
        if st >= 400:
            raise SendError(j.get('message') or 'MSG91 returned HTTP %s' % st)
        return {'balance': j.get('balance') or j.get('msg') or j.get('raw', '')[:40]}

    def send(self, to, tpl, params, lang='en'):
        if not tpl or not tpl.provider_template_id:
            raise SendError('No DLT-registered SMS template', code='no_template')
        rec = {'mobiles': to.lstrip('+')}
        for i, v in enumerate(params, 1):
            rec['var%d' % i] = str(v)[:200]
        data = {'template_id': tpl.provider_template_id, 'short_url': '0', 'recipients': [rec]}
        if self.cfg.get('sender'):
            data['sender'] = self.cfg['sender']
        st, j = _http('POST', self.URL, {'authkey': self.sec.get('authkey', ''), 'accept': 'application/json'}, data)
        if st < 300 and str(j.get('type', 'success')) == 'success':
            return str(j.get('message') or j.get('request_id') or '')
        raise SendError(j.get('message') or 'MSG91 HTTP %s' % st, temporary=st >= 500 or st == 429, code=str(st))


ADAPTERS = {'meta_cloud': MetaCloud, 'msg91': Msg91}


def adapter(p):
    return ADAPTERS[p.provider](p)


def send_email(to, subject, body, attachments=()):
    from django.core.mail import EmailMessage, get_connection
    kw = {}  # no SMTP host set: Django's configured backend (console by default, captured in tests)
    if os.environ.get('HUB_EMAIL_HOST'):
        kw = dict(backend='django.core.mail.backends.smtp.EmailBackend', host=os.environ['HUB_EMAIL_HOST'], port=int(os.environ.get('HUB_EMAIL_PORT', '587')),
                  username=os.environ.get('HUB_EMAIL_USER', ''), password=os.environ.get('HUB_EMAIL_PASSWORD', os.environ.get('HUB_EMAIL_PASS', '')),
                  use_tls=os.environ.get('HUB_EMAIL_TLS', '1') == '1', timeout=15)
    m = EmailMessage(subject, body, os.environ.get('HUB_EMAIL_FROM', 'no-reply@localhost'), [to], connection=get_connection(**kw))
    for name, content, mime in attachments:
        m.attach(name, content, mime)
    try:
        m.send()
    except Exception as e:
        raise SendError('E-mail failed: %s' % e, temporary=True, code='smtp')
    return ''


# ---------- rendering ----------
def fmt_dur(sec):
    if sec is None:
        return '-'
    m = int(round(sec / 60))
    return '%dh %02dm' % (m // 60, m % 60) if m >= 60 else '%dm' % m


def _emp_tz(e):
    from .branches import branch_by_id, head_office
    return (branch_by_id((e or {}).get('branchId')) or head_office()).timezone


def build_vars(job, rcpt, em=None):
    em = em if em is not None else emp_map()
    ctx = job.context or {}
    r = em.get(rcpt) if not str(rcpt).startswith('u:') else None
    from .branches import branch_by_id, head_office
    rb = branch_by_id((r or {}).get('branchId')) or head_office()
    z = rb.timezone
    v = {'company_name': str(V.cget('company', '') or 'Assertive Hub'), 'recipient_name': (r or {}).get('name', '') or 'there', 'branch_name': rb.name}
    subj = em.get(ctx.get('emp_id', '')) if ctx.get('emp_id') else r
    v['employee_name'] = (subj or {}).get('name', '') or v['recipient_name']
    if ctx.get('branch_id'):
        b2 = branch_by_id(ctx['branch_id'])
        if b2:
            v['branch_name'] = b2.name
    tid = ctx.get('task_id') or (job.entity_id if job.entity_type == 'task' else '')
    if tid:
        tr = Record.objects.filter(coll='tasks', rid=tid).first()
        td = tr.data if tr else {}
        v['task_name'] = td.get('title', '')
        v['due_date'] = _fdate(td.get('due'))
        v['estimated_time'] = fmt_dur(int(td['est']) * 60) if td.get('est') else 'not set'
        from .worktime import totals
        v['actual_time'] = fmt_dur(totals([tid]).get(tid, {}).get('actual', 0))
        if not ctx.get('emp_id'):
            v['employee_name'] = (em.get(td.get('eid', '')) or {}).get('name', v['employee_name'])
        if td.get('createdBy'):
            from django.contrib.auth.models import User
            cu = User.objects.filter(pk=td['createdBy']).first() if str(td['createdBy']).isdigit() else None
            v['assigned_by'] = cu.username if cu else ''
    if job.entity_type == 'leave':
        lr = Record.objects.filter(coll='leaves', rid=job.entity_id).first()
        ld = lr.data if lr else {}
        v['leave_dates'] = _fdate(ld.get('from')) + ((' to ' + _fdate(ld.get('to'))) if ld.get('to') and ld.get('to') != ld.get('from') else '')
        v['leave_status'] = str(ld.get('status', '')).lower() or 'updated'
        v['leave_type'] = {'full': 'full day', 'half': 'half day', '1h': '1 hour', '2h': '2 hours'}.get(ld.get('kind'), str(ld.get('kind', '')))
        v['employee_name'] = (em.get(ld.get('eid', '')) or {}).get('name', v['employee_name'])
    mid = ctx.get('meeting_id') or (job.entity_id if job.entity_type == 'meeting' else '')
    if mid and str(mid).isdigit():
        from core.models import Meeting
        m = Meeting.objects.filter(pk=int(mid)).select_related('room').first()
        if m:
            lt = local(m.start_at, z)
            v.update(meeting_title=m.title, meeting_date=lt.strftime('%a, %d %b').replace(' 0', ' '), meeting_time=lt.strftime('%I:%M %p').lstrip('0'),
                     meeting_link=m.join_url or '', organizer_name=(em.get(m.organizer_emp) or {}).get('name', ''), reason=m.cancel_reason or '',
                     meeting_location=('Online' + (' (%s)' % m.platform.replace('_', ' ').title() if m.platform else '')) if m.location_type == 'online' else
                     (m.room.name if m.room_id else (m.location_text or 'Office')))
    for k in ('shift_start', 'announcement_title', 'decision', 'comment', 'code', 'effective_date'):
        if ctx.get(k) is not None:
            v[k] = str(ctx[k])
    if ctx.get('effective_date'):
        v['effective_date'] = _fdate(ctx['effective_date'])
    if ctx.get('duration_min') is not None:
        v['duration'] = fmt_dur(int(ctx['duration_min']) * 60)
    if ctx.get('stop_time'):
        st = parse_dt(ctx['stop_time'])
        v['stop_time'] = local(st, z).strftime('%I:%M %p').lstrip('0') if st else ''
    v.update({k: str(x) for k, x in (ctx.get('vars') or {}).items()})
    return v


def _fdate(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10]).strftime('%a, %d %b').replace(' 0', ' ')
    except (TypeError, ValueError):
        return str(s or '')


def render(body, v):
    return re.sub(r'{{(\w+)}}', lambda m: str(v.get(m.group(1), '')), body or '').strip()


# ---------- emit ----------
def _valid(job):
    """BR-N7: is this notification still relevant when it is about to be created/sent?"""
    k, c = job.event_key, job.context or {}
    if k.startswith('meeting.') and k not in ('meeting.cancelled',):
        from core.models import Meeting
        m = Meeting.objects.filter(pk=int(job.entity_id) if str(job.entity_id).isdigit() else 0).first()
        if not m or m.status == 'cancelled':
            return False
        if k == 'meeting.reminder' and (iso(m.start_at) != c.get('start') or m.start_at < now()):
            return False
    if k == 'task.deadline_reminder':
        r = Record.objects.filter(coll='tasks', rid=job.entity_id).first()
        if not r or r.data.get('status') == 'done' or r.data.get('due') != c.get('due'):
            return False
    if k == 'task.assigned':
        r = Record.objects.filter(coll='tasks', rid=job.entity_id).first()
        if not r or r.data.get('eid') not in job.recipients:
            return False
    if k in ('attendance.reminder', 'attendance.late') and c.get('day') and c.get('kind') != 'punched_late':
        from core.models import Attendance
        a = Attendance.objects.filter(day=c['day'], eid=(job.recipients or [''])[0]).first()
        if a and (a.data.get('tl') or a.data.get('s') in ('V', 'A', 'W')):
            return False
    if k == 'leave.submitted':
        r = Record.objects.filter(coll='leaves', rid=job.entity_id).first()
        if not r or r.data.get('status') != 'Pending':
            return False
    return True


def emit(event_key, entity_type, entity_id, occurrence_key, recipients, context=None, scheduled_for=None):
    cfg = event_cfg(event_key)
    if cfg is None or not cfg.enabled:
        return None
    rc = sorted(set(str(x) for x in (recipients or []) if x))
    if not rc:
        return None
    ctx = dict(context or {})
    when = scheduled_for or now()
    j = NJ.objects.filter(event_key=event_key, entity_id=str(entity_id), occurrence_key=str(occurrence_key)).first()
    if j:
        if j.status == 'queued':
            j.scheduled_for, j.context = when, ctx
            j.recipients = sorted(set(j.recipients) | set(rc))
            j.save()
            if when <= now():
                materialize(j)
        return j
    try:
        with transaction.atomic():
            j = NJ.objects.create(event_key=event_key, entity_type=entity_type, entity_id=str(entity_id), occurrence_key=str(occurrence_key)[:120],
                                  recipients=rc, context=ctx, scheduled_for=when)
    except IntegrityError:
        return NJ.objects.filter(event_key=event_key, entity_id=str(entity_id), occurrence_key=str(occurrence_key)).first()
    if when <= now():
        materialize(j)
    else:
        jobs.enqueue('notify.materialize', {'job': j.pk}, run_at=when, unique_key='nj:%s' % j.pk)
    return j


def cancel_jobs(event_prefix, entity_id):
    NJ.objects.filter(event_key__startswith=event_prefix, entity_id=str(entity_id), status='queued').update(status='cancelled')
    D.objects.filter(job__event_key__startswith=event_prefix, job__entity_id=str(entity_id), status='queued').exclude(channel='inapp').update(status='cancelled', skip_reason='not_relevant')


@jobs.handler('notify.materialize')
def _job_materialize(payload, job):
    j = NJ.objects.filter(pk=payload.get('job')).first()
    if j and j.status == 'queued':
        materialize(j)


def _quiet_until(prefs, tzname, t):
    q = prefs.get('quiet') or {}
    from .base import hm_to_min
    a, b = hm_to_min(q.get('from')), hm_to_min(q.get('to'))
    if a is None or b is None or a == b:
        return None
    lt = local(t, tzname)
    m = lt.hour * 60 + lt.minute
    inside = (a <= m or m < b) if a > b else (a <= m < b)
    if not inside:
        return None
    end = lt.replace(hour=b // 60, minute=b % 60, second=0, microsecond=0)
    if end <= lt:
        end += datetime.timedelta(days=1)
    return end.astimezone(datetime.timezone.utc)


def _month_count(channel):
    t = now()
    return D.objects.filter(channel=channel, status__in=['sent', 'delivered', 'read', 'sending'], sent_at__year=t.year, sent_at__month=t.month).count()


def _cap_ok(channel):
    cap = (ncfg()['caps'] or {}).get(channel)
    return not cap or _month_count(channel) < int(cap)


def _phone_channel_ok(ch, c):
    """(ok, reason) for whatsapp/sms to this contact."""
    if not provider(ch):
        return False, 'channel_off'
    if not c or not c.phone_e164:
        return False, 'no_number'
    if not c.phone_verified_at:
        return False, 'not_verified'
    if not (c.wa_opt_in if ch == 'whatsapp' else c.sms_opt_in):
        return False, 'no_consent'
    return True, ''


def _mk(job, rcpt, ch, dedupe, **kw):
    try:
        with transaction.atomic():
            return D.objects.create(job=job, emp_id=rcpt, channel=ch, dedupe_key=dedupe[:200], **kw)
    except IntegrityError:
        return None


def materialize(job):
    """Create the deliveries for one notification occurrence."""
    if job.status != 'queued':
        return
    if not T.objects.filter(channel='inapp').exists():  # per company database
        ensure_defaults()
    if not _valid(job):
        job.status = 'cancelled'
        job.save(update_fields=['status'])
        return
    cfg = event_cfg(job.event_key) if job.event_key != 'campaign' else None
    camp = job.campaign
    em = emp_map()
    t = now()
    lab = EVENTS.get(job.event_key, ('', 'general', [], False, ''))
    grp, mandatory = lab[1], (cfg.mandatory if cfg else False)
    channels = list(cfg.channels if cfg else ['inapp', camp.channel if camp else 'whatsapp'])
    from .branches import branch_by_id
    for rcpt in job.recipients:
        e = em.get(rcpt)
        if not str(rcpt).startswith('u:'):
            if not e or (e.get('status', 'Active') != 'Active' and job.event_key != 'employee.transferred'):
                continue
        v = build_vars(job, rcpt, em)
        bid = (e or {}).get('branchId')
        br = branch_by_id(bid)
        base = '%s:%s' % (job.pk, rcpt)
        it = template_for(job.event_key, 'inapp') if not camp else None
        title = render(it.subject if it else (camp.title if camp else ''), v)
        body = render(it.body if it else (camp.template.body if camp and camp.template else ''), v)
        _mk(job, rcpt, 'inapp', base + ':inapp', branch=br, title=title[:150], rendered=body, link=LINKS.get(job.entity_type, ''),
            status='delivered', sent_at=t, delivered_at=t, template=it)
        if str(rcpt).startswith('u:'):
            continue
        c = contact(rcpt)
        pr = prefs_of(c)
        if not mandatory and pr['groups'].get(grp) is False:
            _mk(job, rcpt, 'whatsapp', base + ':pref', branch=br, status='skipped', skip_reason='pref_off')
            continue
        delay = None if (job.context or {}).get('urgent') or not (cfg.quiet_hours if cfg else True) else _quiet_until(pr, br.timezone if br else settings.TIME_ZONE, t)
        if 'email' in channels and (e or {}).get('email') and '@' in str(e.get('email')):
            _mk(job, rcpt, 'email', base + ':email', branch=br, to_address=str(e['email'])[:120], status='queued', next_attempt_at=t,
                template=template_for(job.event_key, 'email'), title=title[:150])
        phone = [x for x in pr['order'] if x in ('whatsapp', 'sms') and x in channels] if not camp else [camp.channel]
        if not camp and 'whatsapp' in channels and (cfg.fallback_sms if cfg else False) and 'sms' not in phone:
            phone.append('sms')
        if 'via' in (job.context or {}):
            phone = [x for x in phone if x in job.context['via']]
        reasons = {}
        for ch in phone:
            if ch not in ('whatsapp', 'sms'):
                continue
            ok, why = _phone_channel_ok(ch, c)
            tpl = camp.template if camp else template_for(job.event_key, ch, pr.get('lang', 'en'))
            if ok and ch == 'whatsapp' and (not tpl or tpl.approval_status != 'approved'):
                ok, why = False, 'no_template'
            if ok and ch == 'sms' and (not tpl or not tpl.provider_template_id):
                ok, why = False, 'no_template'
            if ok and not _cap_ok(ch):
                ok, why = False, 'cap_reached'
            if ok:
                _mk(job, rcpt, ch, base + ':' + ch, branch=br, to_address=c.phone_e164, template=tpl, status='queued',
                    next_attempt_at=delay or t, skip_reason='quiet_delayed' if delay else '')
                break
            reasons[ch] = why
        else:
            for ch, why in reasons.items():
                _mk(job, rcpt, ch, base + ':' + ch, branch=br, status='skipped', skip_reason=why)
    job.status = 'done'
    job.save(update_fields=['status'])
    if camp and camp.status in ('scheduled', 'sending'):
        camp.status = 'sent'
        camp.save(update_fields=['status'])


# ---------- sending ----------
TEMP_BACKOFF = [60, 300, 900]


def send_one(d):
    if d.status != 'queued':
        return
    job = d.job
    if not _valid(job) and job.event_key not in ('meeting.cancelled',):
        d.status, d.skip_reason = 'cancelled', 'not_relevant'
        d.save(update_fields=['status', 'skip_reason'])
        return
    c = contact(d.emp_id, create=False)
    if d.channel in ('whatsapp', 'sms'):
        ok, why = _phone_channel_ok(d.channel, c)
        if not ok:
            d.status, d.skip_reason = 'skipped', why
            d.save(update_fields=['status', 'skip_reason'])
            return
    v = build_vars(job, d.emp_id)
    tpl = d.template
    d.rendered = render(tpl.body if tpl else d.rendered, v)
    d.attempts += 1
    try:
        if d.channel == 'email':
            mid = send_email(d.to_address, render(tpl.subject if tpl else d.title, v) or d.title, d.rendered + '\n\n' + (v.get('meeting_link') or ''))
        else:
            p = provider(d.channel)
            params = [v.get(n, '') for n in (tpl.variables if tpl else [])]
            mid = adapter(p).send(d.to_address, tpl, params, tpl.language if tpl else 'en')
        d.status, d.provider_msg_id, d.sent_at = 'sent', str(mid or '')[:120], now()
        d.error_code = d.error_message = ''
        d.save()
        if d.channel == 'whatsapp':
            cfg = event_cfg(job.event_key) if job.event_key in EVENTS else None
            if cfg and cfg.fallback_sms and cfg.fallback_after_min > 0:
                jobs.enqueue('notify.fallback_check', {'d': d.pk}, run_at=now() + datetime.timedelta(minutes=cfg.fallback_after_min), unique_key='fb:%s' % d.pk)
    except SendError as e:
        d.error_code, d.error_message = e.code[:40], str(e)[:300]
        if e.temporary and d.attempts <= len(TEMP_BACKOFF):
            d.status = 'queued'
            d.next_attempt_at = now() + datetime.timedelta(seconds=TEMP_BACKOFF[d.attempts - 1])
        else:
            d.status, d.failed_at = ('failed' if e.temporary else 'failed_perm'), now()
        d.save()
        if d.status in ('failed', 'failed_perm'):
            fallback(d)


def fallback(d):
    if d.channel != 'whatsapp':
        return None
    job = d.job
    cfg = event_cfg(job.event_key) if job.event_key in EVENTS else None
    if not cfg or not cfg.fallback_sms:
        return None
    c = contact(d.emp_id, create=False)
    ok, why = _phone_channel_ok('sms', c)
    tpl = template_for(job.event_key, 'sms')
    if ok and (not tpl or not tpl.provider_template_id):
        ok, why = False, 'no_template'
    key = '%s:%s:sms' % (job.pk, d.emp_id)
    if not ok:
        return _mk(job, d.emp_id, 'sms', key, branch=d.branch, status='skipped', skip_reason=why, fallback_of=d)
    return _mk(job, d.emp_id, 'sms', key, branch=d.branch, to_address=c.phone_e164, template=tpl, status='queued', next_attempt_at=now(), fallback_of=d)


@jobs.handler('notify.fallback_check')
def _fb_check(payload, job):
    d = D.objects.filter(pk=payload.get('d')).first()
    if d and d.status == 'sent':
        fallback(d)


def send_due(limit=200):
    n = 0
    for d in D.objects.filter(status='queued', next_attempt_at__lte=now()).exclude(channel='inapp').select_related('job', 'template', 'branch').order_by('next_attempt_at')[:limit]:
        with transaction.atomic():
            send_one(d)
        n += 1
    return n


# ---------- business hooks (called from views.op_rec and v6 modules) ----------
def _mgr_of(eid):
    e = V.Record.objects.filter(coll='employees', rid=eid).first()
    return (e.data if e else {}).get('reportsTo', '')


def task_assigned(td, rid, assigner=None):
    if td.get('eid') and td.get('status') != 'done':
        emit('task.assigned', 'task', rid, 'to:%s' % td['eid'], [td['eid']], {'task_id': rid})


def task_completed(td, rid):
    rc = set()
    cb = td.get('createdBy')
    if cb and str(cb).isdigit():
        from django.contrib.auth.models import User
        cu = User.objects.filter(pk=int(cb)).first()
        if cu:
            rc.add(V.prof(cu).emp_id or 'u:%s' % cu.pk)
    m = _mgr_of(td.get('eid', ''))
    if m:
        rc.add(m)
    rc.discard(td.get('eid', ''))
    emit('task.completed', 'task', rid, 'done:%s' % (td.get('doneOn') or datetime.date.today().isoformat()), sorted(rc), {'task_id': rid, 'emp_id': td.get('eid', '')})


def leave_event(kind, ld):
    eid = ld.get('eid', '')
    if kind == 'new':
        rc = set([_mgr_of(eid)]) if _mgr_of(eid) else set()
        from core.models import UserBranchScope
        e = V.Record.objects.filter(coll='employees', rid=eid).first()
        bid = (e.data if e else {}).get('branchId')
        if str(bid or '').isdigit():
            for sc in UserBranchScope.objects.filter(branch_id=int(bid), user__is_active=True).select_related('user'):
                if V.role_of(sc.user) in ('branchadmin', 'hradmin'):
                    rc.add(V.prof(sc.user).emp_id or 'u:%s' % sc.user_id)
        rc.discard(eid)
        emit('leave.submitted', 'leave', ld.get('id', ''), 'new', sorted(rc), {'emp_id': eid})
    else:
        emit('leave.decided', 'leave', ld.get('id', ''), str(ld.get('status', '')), [eid], {'emp_id': eid})


def notice_published(nd, actor):
    via = [x for x in (nd.get('sendVia') or []) if x in ('whatsapp', 'sms')]
    aud = nd.get('audience') or {}
    rc = audience_ids(actor, {'branches': aud.get('branches') or [], 'depts': aud.get('depts') or []})
    ctx = {'vars': {'announcement_title': str(nd.get('title', ''))[:120]}, 'via': via}
    emit('branch.announcement', 'notice', nd.get('id', ''), 'published', rc, ctx)


def audience_ids(u, aud):
    s = scope_for(u)
    em = emp_map()
    br = set(str(x) for x in aud.get('branches') or [])
    dp = set(str(x) for x in aud.get('depts') or [])
    ds = set(str(x) for x in aud.get('designations') or [])
    ids = set(str(x) for x in aud.get('empIds') or [])
    out = []
    for k, e in em.items():
        if e.get('status', 'Active') != 'Active' or not s.sees(k, primary_only=True):
            continue
        if ids and k not in ids:
            continue
        if br and str(e.get('branchId', '')) not in br:
            continue
        if dp and e.get('dept', '') not in dp:
            continue
        if ds and e.get('designation', '') not in ds:
            continue
        out.append(k)
    return sorted(out)


# ---------- scheduled detectors ----------
def scheduled_checks():
    """Attendance reminders/late, task deadline reminders, birthdays. Idempotent (job uniqueness)."""
    from core.punch import shift_for
    from core.models import Attendance
    from .branches import branch_by_id, head_office
    em = emp_map()
    t = now()
    st = V.cget('settings', {}) or {}
    for k, e in em.items():
        if e.get('status', 'Active') != 'Active':
            continue
        br = branch_by_id(e.get('branchId')) or head_office()
        lt = local(t, br.timezone)
        day = lt.date().isoformat()
        wd = (lt.isoweekday() % 7)
        mnow = lt.hour * 60 + lt.minute
        if e.get('dob') and str(e['dob'])[5:10] == day[5:] and mnow >= 9 * 60:
            emit('employee.birthday', 'employee', k, 'bday:%s' % day[:4], [k], {})
        if e.get('core') or wd not in (br.work_days or st.get('workDays') or [1, 2, 3, 4, 5]):
            continue
        if any(ev.get('type') == 'holiday' and ev.get('date', '') <= day <= (ev.get('end') or ev.get('date', '')) and
               (not ev.get('branchIds') or str(e.get('branchId', '')) in ev.get('branchIds')) for ev in (r.data for r in Record.objects.filter(coll='events'))):
            continue
        start, grace = shift_for(k, day, e)
        if start is None:
            continue
        ec = event_cfg('attendance.reminder')
        off = int((ec.timing or {}).get('offsetMin', 10)) if ec else 10
        a = Attendance.objects.filter(day=day, eid=k).first()
        punched = bool(a and (a.data.get('tl') or a.data.get('s') in ('V', 'A', 'W')))
        from .worktime import on_leave
        if punched or on_leave(k, lt.date()):
            continue
        hhmm = '%02d:%02d' % (start // 60, start % 60)
        if start - off <= mnow < start + 30:
            emit('attendance.reminder', 'attendance', '%s:%s' % (k, day), 'r', [k], {'day': day, 'shift_start': hhmm})
        if start + grace + 15 <= mnow < start + 240:
            emit('attendance.late', 'attendance', '%s:%s' % (k, day), 'late', [k], {'day': day, 'shift_start': hhmm})
    for r in Record.objects.filter(coll='tasks'):
        d = r.data
        if d.get('status') == 'done' or not d.get('eid') or not d.get('due'):
            continue
        e = em.get(d['eid']) or {}
        br = branch_by_id(e.get('branchId')) or head_office()
        lt = local(t, br.timezone)
        mnow = lt.hour * 60 + lt.minute
        tom = (lt.date() + datetime.timedelta(days=1)).isoformat()
        if d['due'] == tom and mnow >= 600:
            emit('task.deadline_reminder', 'task', r.rid, 'd1:%s' % d['due'], [d['eid']], {'task_id': r.rid, 'due': d['due']})
        if d['due'] == lt.date().isoformat() and mnow >= 570:
            emit('task.deadline_reminder', 'task', r.rid, 'd0:%s' % d['due'], [d['eid']], {'task_id': r.rid, 'due': d['due']})


def purge_old():
    t = now()
    D.objects.filter(queued_at__lt=t - datetime.timedelta(days=180)).exclude(rendered='').update(rendered='')
    WebhookLog.objects.filter(received_at__lt=t - datetime.timedelta(days=30)).delete()
    OtpChallenge.objects.filter(created_at__lt=t - datetime.timedelta(days=1)).delete()


# ---------- webhooks ----------
def _status_update(pid, status, ts=None, err_code='', err_msg=''):
    d = D.objects.filter(provider_msg_id=pid).first()
    if not d:
        return False
    order = {'queued': 0, 'sending': 1, 'sent': 2, 'delivered': 3, 'read': 4}
    t = ts or now()
    if status in ('failed', 'undelivered', 'rejected'):
        if d.status in ('sent', 'queued', 'sending'):
            d.status, d.failed_at, d.error_code, d.error_message = 'failed_perm', t, err_code[:40], err_msg[:300]
            d.save()
            fallback(d)
        return True
    if order.get(status, -1) > order.get(d.status, -1):
        d.status = status
        if status == 'delivered':
            d.delivered_at = t
        if status == 'read':
            d.read_at = t
            d.delivered_at = d.delivered_at or t
        d.save()
    return True


def _keyword(from_num, word, channel):
    num = normalize_phone(from_num)
    c = ContactProfile.objects.filter(phone_e164=num).first()
    if not c:
        return
    w = word.strip().upper()
    if w in ('STOP', 'UNSUBSCRIBE', 'STOP ALL'):
        if channel == 'whatsapp':
            c.wa_opt_in = False
        else:
            c.sms_opt_in = False
        c.save()
        ConsentEvent.objects.create(contact=c, channel=channel, action='opt_out', source='wa_keyword' if channel == 'whatsapp' else 'sms_keyword')
        D.objects.filter(emp_id=c.emp_id, channel=channel, status='queued').update(status='cancelled', skip_reason='opted_out')
    elif w in ('START', 'SUBSCRIBE') and c.phone_verified_at:
        if channel == 'whatsapp':
            c.wa_opt_in = True
        else:
            c.sms_opt_in = True
        c.save()
        ConsentEvent.objects.create(contact=c, channel=channel, action='opt_in', source='wa_keyword' if channel == 'whatsapp' else 'sms_keyword', notice_version=NOTICE_VERSION)


@csrf_exempt
def webhook_whatsapp(request, token):
    p = P.objects.filter(webhook_token=token, channel='whatsapp').first()
    if not p:
        return HttpResponse('Unknown', status=404)
    if request.method == 'GET':  # Meta verification challenge
        if request.GET.get('hub.mode') == 'subscribe' and hmac.compare_digest(request.GET.get('hub.verify_token', ''), (p.config_public or {}).get('verify_token', '')):
            return HttpResponse(request.GET.get('hub.challenge', ''), content_type='text/plain')
        return HttpResponse('Forbidden', status=403)
    body = request.body
    ok = MetaCloud(p).verify_signature(body, request.headers.get('X-Hub-Signature-256', ''))
    WebhookLog.objects.create(provider='whatsapp', signature_ok=ok, payload=body[:16000].decode('utf-8', 'replace'))
    if not ok:
        return HttpResponse('Bad signature', status=403)
    try:
        j = json.loads(body or b'{}')
        with transaction.atomic():
            for en in j.get('entry') or []:
                for ch in en.get('changes') or []:
                    val = ch.get('value') or {}
                    for s in val.get('statuses') or []:
                        ts = datetime.datetime.fromtimestamp(int(s.get('timestamp', 0)), datetime.timezone.utc) if str(s.get('timestamp', '')).isdigit() else None
                        er = (s.get('errors') or [{}])[0]
                        _status_update(s.get('id', ''), s.get('status', ''), ts, str(er.get('code', '')), er.get('title', '') or er.get('message', ''))
                    for m in val.get('messages') or []:
                        txt = ((m.get('text') or {}).get('body') or (m.get('button') or {}).get('text') or '')
                        if txt and len(txt) < 20:
                            _keyword(m.get('from', ''), txt, 'whatsapp')
    except (ValueError, TypeError) as e:
        log.warning('whatsapp webhook parse: %s', e)
    return JsonResponse({'ok': True})


@csrf_exempt
def webhook_sms(request, token):
    p = P.objects.filter(webhook_token=token, channel='sms').first()
    if not p or request.method != 'POST':
        return HttpResponse('Unknown', status=404)
    body = request.body
    WebhookLog.objects.create(provider='sms', signature_ok=True, payload=body[:16000].decode('utf-8', 'replace'))
    try:
        j = json.loads(body or b'[]')
    except ValueError:
        j = dict(request.POST.items())
    rows = j if isinstance(j, list) else (j.get('data') if isinstance(j, dict) and isinstance(j.get('data'), list) else [j])
    with transaction.atomic():
        for r in rows:
            if not isinstance(r, dict):
                continue
            rid = str(r.get('requestId') or r.get('request_id') or r.get('id') or '')
            st = str(r.get('status') or r.get('desc') or '').lower()
            if r.get('message') and r.get('mobile') and len(str(r['message'])) < 20:
                _keyword(str(r['mobile']), str(r['message']), 'sms')
                continue
            m = {'1': 'delivered', 'delivered': 'delivered', '2': 'failed', 'failed': 'failed', '16': 'failed', 'rejected': 'failed', '9': 'failed', 'ndnc': 'failed'}
            if rid and st:
                _status_update(rid, m.get(st, 'sent'), err_code=st, err_msg=str(r.get('desc', ''))[:300])
    return JsonResponse({'ok': True})


# ---------- actions: providers ----------
def pdict(p, request=None):
    host = request.build_absolute_uri('/') if request else '/'
    sec = crypto.dec(p.secrets_enc, {}) or {}
    return {'id': str(p.pk), 'channel': p.channel, 'provider': p.provider, 'enabled': p.enabled, 'status': p.status, 'config': p.config_public,
            'secrets': {k: crypto.mask(v) for k, v in sec.items()}, 'lastError': p.last_error, 'lastTested': iso(p.last_tested_at),
            'webhookUrl': host + 'hooks/%s/%s' % (p.channel, p.webhook_token), 'verifyToken': (p.config_public or {}).get('verify_token', '') if p.channel == 'whatsapp' else ''}


@action('notify.providers.list', module='notify', read=True)
def a_plist(request, u, b):
    need(u, 'notify.config')
    return {'items': [pdict(p, request) for p in P.objects.all()], 'settings': ncfg(), 'kinds': {'whatsapp': ['meta_cloud'], 'sms': ['msg91']}}


PUBLIC_KEYS = {'meta_cloud': ['phone_number_id', 'waba_id', 'api_version', 'lang'], 'msg91': ['sender', 'dlt_entity_id', 'otp_template_id']}
SECRET_KEYS = {'meta_cloud': ['token', 'app_secret'], 'msg91': ['authkey']}


@action('notify.providers.save', module='notify')
def a_psave(request, u, b):
    need(u, 'notify.config')
    ch = b.get('channel')
    kind = b.get('provider')
    if ch not in ('whatsapp', 'sms') or kind not in ({'whatsapp': ['meta_cloud'], 'sms': ['msg91']}[ch] if ch in ('whatsapp', 'sms') else []):
        invalid(provider='Choose a provider.')
    p = P.objects.filter(channel=ch).first() or P(channel=ch, webhook_token=secrets.token_urlsafe(24))
    cfg = {k: str((b.get('config') or {}).get(k, '') or '').strip()[:120] for k in PUBLIC_KEYS[kind]}
    if kind == 'meta_cloud' and cfg.get('phone_number_id') and not cfg['phone_number_id'].isdigit():
        invalid(phone_number_id='The phone number ID is a number from Meta (not the phone number).')
    if kind == 'msg91' and cfg.get('dlt_entity_id') and not re.match(r'^\d{19}$', cfg['dlt_entity_id']):
        invalid(dlt_entity_id='The DLT entity ID has 19 digits.')
    if ch == 'whatsapp':
        cfg['verify_token'] = (p.config_public or {}).get('verify_token') or secrets.token_urlsafe(24)
    sec = (crypto.dec(p.secrets_enc, {}) or {}) if p.provider == kind else {}
    for k in SECRET_KEYS[kind]:
        nv = str((b.get('secrets') or {}).get(k) or '').strip()
        if nv:
            sec[k] = nv
    missing = [k for k in SECRET_KEYS[kind] if not sec.get(k)]
    if missing:
        invalid(**{missing[0]: 'This value is required.'})
    p.provider, p.config_public, p.secrets_enc, p.status = kind, cfg, crypto.enc(sec), 'unverified'
    p.enabled = False if p.pk is None else p.enabled
    p.save()
    audit.record(u, 'notify.provider.save', 'provider', p.pk, after={'channel': ch, 'provider': kind, 'config': cfg, 'secretsChanged': [k for k in SECRET_KEYS[kind] if (b.get('secrets') or {}).get(k)]}, request=request)
    return {'provider': pdict(p, request)}


@action('notify.providers.test', module='notify')
def a_ptest(request, u, b):
    need(u, 'notify.config')
    p = P.objects.filter(channel=b.get('channel')).first()
    if not p:
        err('NOT_FOUND', 'Set up the provider first.')
    try:
        info = adapter(p).test()
    except SendError as e:
        p.status, p.last_error, p.last_tested_at = 'error', str(e)[:300], now()
        p.save()
        err('PROVIDER_ERROR', 'The provider said: %s' % e)
    p.status, p.last_error, p.last_tested_at = 'verified', '', now()
    p.save()
    return {'info': info, 'provider': pdict(p, request)}


@action('notify.providers.toggle', module='notify')
def a_ptoggle(request, u, b):
    need(u, 'notify.config')
    p = P.objects.filter(channel=b.get('channel')).first()
    if not p:
        err('NOT_FOUND', 'Set up the provider first.')
    on = bool(b.get('enabled'))
    if on and p.status != 'verified':
        err('INVALID_STATE', 'Test the connection successfully before switching this channel on.')
    p.enabled = on
    p.save(update_fields=['enabled'])
    n = 0
    if not on:
        n = D.objects.filter(channel=p.channel, status='queued').update(status='cancelled', skip_reason='channel_off')
    audit.record(u, 'notify.provider.toggle', 'provider', p.pk, after={'enabled': on, 'cancelled': n}, request=request)
    return {'provider': pdict(p, request), 'cancelled': n}


@action('notify.settings.save', module='notify')
def a_nsettings(request, u, b):
    need(u, 'notify.config')
    c = ncfg()
    caps = {}
    for ch in ('whatsapp', 'sms'):
        v = (b.get('caps') or {}).get(ch)
        if v not in (None, '', 0, '0'):
            try:
                caps[ch] = max(1, int(v))
            except (TypeError, ValueError):
                invalid(caps='Monthly limits must be numbers.')
    from .base import hm_to_min
    q = b.get('quiet') or c['quiet']
    if hm_to_min(q.get('from')) is None or hm_to_min(q.get('to')) is None:
        invalid(quiet='Enter quiet hours as HH:MM.')
    c.update(caps=caps, quiet={'from': q['from'], 'to': q['to']})
    V.cset('v6notify', c)
    return {'settings': c}


# ---------- actions: templates and events ----------
def tdict(t):
    return {'id': str(t.pk), 'event': t.event_key, 'channel': t.channel, 'lang': t.language, 'name': t.name, 'subject': t.subject, 'body': t.body,
            'variables': t.variables, 'category': t.category, 'providerName': t.provider_template_name, 'providerId': t.provider_template_id,
            'status': t.approval_status, 'version': t.version}


VARS = ['recipient_name', 'employee_name', 'company_name', 'branch_name', 'task_name', 'due_date', 'estimated_time', 'actual_time', 'assigned_by',
        'leave_dates', 'leave_status', 'leave_type', 'meeting_title', 'meeting_date', 'meeting_time', 'meeting_location', 'meeting_link', 'organizer_name',
        'reason', 'shift_start', 'announcement_title', 'decision', 'comment', 'duration', 'stop_time', 'effective_date', 'code']


@action('notify.templates.list', module='notify', read=True)
def a_tlist(request, u, b):
    need(u, 'notify.templates.view')
    q = T.objects.filter(active=True).order_by('event_key', 'channel', 'language', '-version')
    seen, out = set(), []
    for t in q:
        k = (t.event_key, t.channel, t.language)
        if k not in seen:
            seen.add(k)
            out.append(tdict(t))
    return {'items': out, 'variables': VARS, 'events': {k: {'label': v[0], 'group': v[1], 'category': v[4]} for k, v in EVENTS.items()}}


@action('notify.templates.save', module='notify')
def a_tsave(request, u, b):
    need(u, 'notify.templates')
    ev, ch = str(b.get('event') or ''), str(b.get('channel') or '')
    if ev not in EVENTS and ev not in ('contact.otp', 'campaign'):
        invalid(event='Unknown event.')
    if ch not in ('inapp', 'email', 'whatsapp', 'sms'):
        invalid(channel='Unknown channel.')
    body = text(b, 'body', 1024, req=True, label='Message')
    bad = [x for x in re.findall(r'{{(\w+)}}', body) if x not in VARS]
    if bad:
        invalid(body='Unknown variable {{%s}}.' % bad[0])
    lang = (text(b, 'lang', 5) or 'en').lower()
    last = T.objects.filter(event_key=ev, channel=ch, language=lang).order_by('-version').first()
    T.objects.filter(event_key=ev, channel=ch, language=lang, active=True).update(active=False)
    pname = text(b, 'providerName', 120).lower()
    if pname and not re.match(r'^[a-z0-9_]{1,120}$', pname):
        invalid(providerName='WhatsApp template names use lower-case letters, numbers and underscores.')
    pid = text(b, 'providerId', 60)
    status = 'approved' if ch in ('inapp', 'email') else ('approved' if ch == 'sms' and pid else 'draft')
    if ch == 'whatsapp' and last and last.body == body and last.provider_template_name == pname:
        status = last.approval_status
    if ch == 'whatsapp' and b.get('approved') is True:
        status = 'approved'  # admin confirms it was approved in Meta Business Manager
    t = T.objects.create(event_key=ev, channel=ch, language=lang, name=text(b, 'name', 80) or '%s (%s)' % (ev, ch), subject=text(b, 'subject', 150), body=body,
                         variables=re.findall(r'{{(\w+)}}', body), category=b.get('category') if b.get('category') in ('utility', 'marketing', 'authentication', 'transactional', 'service') else 'utility',
                         provider_template_name=pname, provider_template_id=pid, approval_status=status, version=(last.version + 1) if last else 1)
    audit.record(u, 'notify.template.save', 'template', t.pk, after={'event': ev, 'channel': ch, 'version': t.version}, request=request)
    return {'template': tdict(t)}


@action('notify.templates.preview', module='notify')
def a_tpreview(request, u, b):
    need(u, 'notify.templates.view')
    sample = {'recipient_name': 'Rahul', 'employee_name': 'Rahul Nair', 'company_name': str(V.cget('company', '') or 'Your company'), 'branch_name': 'Bangalore',
              'task_name': 'Landing page for Client X', 'due_date': 'Thu, 4 Dec', 'estimated_time': '5h 00m', 'actual_time': '4h 30m', 'assigned_by': 'arjun',
              'leave_dates': 'Thu, 4 Dec to Fri, 5 Dec', 'leave_status': 'approved', 'leave_type': 'full day', 'meeting_title': 'Sprint sync', 'meeting_date': 'Wed, 3 Dec',
              'meeting_time': '3:00 PM', 'meeting_location': 'BLR-Board', 'meeting_link': 'https://meet.google.com/abc-defg-hij', 'organizer_name': 'Arjun',
              'reason': 'Client moved the review.', 'shift_start': '09:30', 'announcement_title': 'Office closed on Friday', 'decision': 'approved', 'comment': '',
              'duration': '1h 30m', 'stop_time': '8:30 PM', 'effective_date': 'Mon, 1 Dec', 'code': '482913'}
    return {'text': render(str(b.get('body') or ''), sample), 'subject': render(str(b.get('subject') or ''), sample)}


@action('notify.templates.sync', module='notify')
def a_tsync(request, u, b):
    need(u, 'notify.templates')
    p = P.objects.filter(channel='whatsapp').first()
    if not p:
        err('NOT_FOUND', 'Set up WhatsApp first.')
    try:
        n = MetaCloud(p).sync_templates()
    except SendError as e:
        err('PROVIDER_ERROR', 'WhatsApp said: %s' % e)
    return {'updated': n}


@action('notify.templates.submit', module='notify')
def a_tsubmit(request, u, b):
    need(u, 'notify.templates')
    t = T.objects.filter(pk=int(b.get('id')) if str(b.get('id') or '').isdigit() else 0, channel='whatsapp').first()
    p = P.objects.filter(channel='whatsapp').first()
    if not t or not p:
        err('NOT_FOUND', 'Template or WhatsApp set-up not found.')
    if not t.provider_template_name:
        invalid(providerName='Give the template a WhatsApp name first.')
    try:
        st = MetaCloud(p).submit(t)
    except SendError as e:
        err('PROVIDER_ERROR', 'WhatsApp said: %s' % e)
    t.approval_status = 'approved' if str(st).upper() == 'APPROVED' else 'submitted'
    t.save(update_fields=['approval_status'])
    return {'template': tdict(t)}


def ecdict(e):
    lab = EVENTS.get(e.event_key, (e.event_key, 'general', [], False, 'utility'))
    return {'key': e.event_key, 'label': lab[0], 'group': lab[1], 'category': lab[4], 'enabled': e.enabled, 'channels': e.channels, 'mandatory': e.mandatory,
            'fallbackSms': e.fallback_sms, 'fallbackAfter': e.fallback_after_min, 'timing': e.timing, 'quietHours': e.quiet_hours,
            'templates': {ch: (template_for(e.event_key, ch).approval_status if template_for(e.event_key, ch) else 'missing') for ch in ('whatsapp', 'sms', 'email', 'inapp')}}


@action('notify.events.list', module='notify', read=True)
def a_elist(request, u, b):
    need(u, 'notify.templates.view')
    ensure_defaults()
    return {'items': [ecdict(e) for e in EC.objects.filter(event_key__in=list(EVENTS)).order_by('event_key')]}


@action('notify.events.save', module='notify')
def a_esave(request, u, b):
    need(u, 'notify.templates')
    e = event_cfg(str(b.get('key') or ''))
    if not e:
        err('NOT_FOUND', 'Unknown event.')
    ch = [x for x in (b.get('channels') or []) if x in ('whatsapp', 'sms', 'email', 'inapp')]
    if 'inapp' not in ch:
        ch.append('inapp')  # in-app is always on (final fallback, BR-N6)
    before = ecdict(e)
    e.enabled = True if e.mandatory else bool(b.get('enabled', e.enabled))
    e.channels = ch
    e.fallback_sms = bool(b.get('fallbackSms', e.fallback_sms))
    from .base import int_in
    e.fallback_after_min = int_in(b, 'fallbackAfter', 0, 120, e.fallback_after_min, 'Fallback wait')
    e.quiet_hours = bool(b.get('quietHours', e.quiet_hours))
    tm = b.get('timing') or {}
    if 'offsetMin' in tm:
        e.timing = dict(e.timing or {}, offsetMin=int_in(tm, 'offsetMin', 0, 120, 10, 'Offset'))
    e.save()
    audit.record(u, 'notify.event.save', 'notify_event', e.event_key, before=before, after=ecdict(e), request=request)
    return {'event': ecdict(e)}


# ---------- actions: sending ----------
def _audience_preview(u, aud, channel, tpl):
    ids = audience_ids(u, aud)
    out = {'total': len(ids), 'willReceive': {'whatsapp': 0, 'sms': 0, 'inapp': len(ids)}, 'skipped': {}}
    cs = {c.emp_id: c for c in ContactProfile.objects.filter(emp_id__in=ids)}
    for k in ids:
        ok, why = _phone_channel_ok(channel, cs.get(k))
        if ok:
            out['willReceive'][channel] += 1
        else:
            out['skipped'][why] = out['skipped'].get(why, 0) + 1
    if channel == 'whatsapp' and (not tpl or tpl.approval_status != 'approved'):
        out['skipped'] = {'no_template': len(ids)}
        out['willReceive']['whatsapp'] = 0
    return ids, out


def _tpl_arg(b, channel):
    t = T.objects.filter(pk=int(b.get('templateId')) if str(b.get('templateId') or '').isdigit() else 0, channel=channel).first()
    if not t:
        invalid(templateId='Choose a message template.')
    return t


@action('notify.audience.preview', module='notify')
def a_apreview(request, u, b):
    need(u, 'notify.send')
    ch = b.get('channel') if b.get('channel') in ('whatsapp', 'sms') else 'whatsapp'
    tpl = T.objects.filter(pk=int(b.get('templateId')) if str(b.get('templateId') or '').isdigit() else 0).first()
    ids, out = _audience_preview(u, b.get('audience') or {}, ch, tpl)
    return out


@action('notify.campaigns.create', module='notify')
def a_ccreate(request, u, b):
    need(u, 'notify.send')
    ch = b.get('channel')
    if ch not in ('whatsapp', 'sms'):
        invalid(channel='Choose WhatsApp or SMS.')
    if not provider(ch):
        err('INVALID_STATE', '%s is not set up or switched off.' % ('WhatsApp' if ch == 'whatsapp' else 'SMS'))
    tpl = _tpl_arg(b, ch)
    if ch == 'whatsapp' and tpl.approval_status != 'approved':
        err('TEMPLATE_NOT_APPROVED', 'This WhatsApp template is not approved yet.')
    if ch == 'sms' and not tpl.provider_template_id:
        err('TEMPLATE_NOT_APPROVED', 'This SMS template has no DLT template ID.')
    aud = b.get('audience') or {}
    if V.role_of(u) == 'branchmgr' and not aud.get('branches'):
        err('FORBIDDEN', 'Branch managers can send announcements to their branch only.')
    ids, pv = _audience_preview(u, aud, ch, tpl)
    if not ids:
        invalid(audience='Nobody matches this audience.')
    vars_ = {k: str(v)[:200].replace('\n', ' ') for k, v in (b.get('variables') or {}).items() if k in VARS}
    missing = [x for x in tpl.variables if x not in vars_ and x not in ('recipient_name', 'employee_name', 'company_name', 'branch_name')]
    if missing:
        invalid(variables='Fill in {{%s}}.' % missing[0])
    when = parse_dt(b.get('scheduledFor')) if b.get('scheduledFor') else None
    if when and (when < now() + datetime.timedelta(minutes=5) or when > now() + datetime.timedelta(days=90)):
        invalid(scheduledFor='Schedule at least 5 minutes and at most 90 days ahead.')
    if len(ids) > 200 or when:
        need(u, 'notify.bulk')
    if len(ids) > 200 and not b.get('confirmed'):
        return {'needsConfirm': True, 'count': len(ids), 'preview': pv}
    recent = NotificationCampaign.objects.filter(created_by=u, created_at__gte=now() - datetime.timedelta(hours=1)).count()
    if recent >= 10:
        err('RATE_LIMITED', 'You have sent 10 messages to groups in the last hour. Please wait a little.')
    if not _cap_ok(ch):
        err('CAP_REACHED', 'This month\'s %s limit has been reached.' % ch)
    title = text(b, 'title', 150) or tpl.subject or 'Message'
    c = NotificationCampaign.objects.create(title=title, audience=aud, channel=ch, template=tpl, variables=vars_, scheduled_for=when,
                                            recipient_count=len(ids), skipped=pv['skipped'], created_by=u)
    j = NJ.objects.create(event_key='campaign', entity_type='campaign', entity_id=str(c.pk), occurrence_key='send', recipients=ids,
                          context={'vars': vars_}, scheduled_for=when or now(), campaign=c)
    if when:
        jobs.enqueue('notify.materialize', {'job': j.pk}, run_at=when, unique_key='nj:%s' % j.pk)
    else:
        c.status = 'sending'
        c.save(update_fields=['status'])
        materialize(j)
    audit.record(u, 'notify.campaign', 'campaign', c.pk, after={'channel': ch, 'count': len(ids), 'scheduled': iso(when)}, request=request)
    return {'campaign': cdict(c)}


def cdict(c):
    q = D.objects.filter(job__campaign=c).exclude(channel='inapp')
    st = {}
    for s in q.values_list('status', flat=True):
        st[s] = st.get(s, 0) + 1
    return {'id': str(c.pk), 'title': c.title, 'channel': c.channel, 'status': c.status, 'scheduledFor': iso(c.scheduled_for), 'count': c.recipient_count,
            'skipped': c.skipped, 'progress': st, 'by': c.created_by.username if c.created_by_id else '', 'created': iso(c.created_at), 'audience': c.audience}


@action('notify.campaigns.list', module='notify', read=True)
def a_clist(request, u, b):
    need(u, 'notify.send')
    q = NotificationCampaign.objects.order_by('-created_at')
    if V.role_of(u) not in ALL_ADMIN:
        q = q.filter(created_by=u)
    return paged(q, b, cdict)


@action('notify.campaigns.cancel', module='notify')
def a_ccancel(request, u, b):
    need(u, 'notify.send')
    c = NotificationCampaign.objects.filter(pk=int(b.get('id')) if str(b.get('id') or '').isdigit() else 0).first()
    if not c or (V.role_of(u) not in ALL_ADMIN and c.created_by_id != u.pk):
        err('NOT_FOUND', 'Not found.')
    if c.status != 'scheduled' or (c.scheduled_for and c.scheduled_for <= now() + datetime.timedelta(minutes=1)):
        err('LOCKED', 'Sending has started or is about to start.')
    c.status = 'cancelled'
    c.save(update_fields=['status'])
    NJ.objects.filter(campaign=c, status='queued').update(status='cancelled')
    return {'campaign': cdict(c)}


# ---------- actions: history ----------
def ddict(d, em):
    return {'id': str(d.pk), 'event': d.job.event_key, 'label': EVENTS.get(d.job.event_key, ('Message',))[0] if d.job.event_key != 'campaign' else (d.job.campaign.title if d.job.campaign_id else 'Message'),
            'empId': d.emp_id, 'name': (em.get(d.emp_id) or {}).get('name', d.emp_id), 'channel': d.channel, 'to': crypto.mask(d.to_address) if d.channel in ('whatsapp', 'sms') else d.to_address,
            'status': d.status, 'skip': d.skip_reason, 'error': d.error_message, 'attempts': d.attempts, 'queued': iso(d.queued_at), 'sent': iso(d.sent_at),
            'delivered': iso(d.delivered_at), 'read': iso(d.read_at), 'failed': iso(d.failed_at), 'fallbackOf': str(d.fallback_of_id or ''),
            'branchId': str(d.branch_id or '')}


def _hist_q(u, b):
    need(u, 'notify.history')
    s = scope_for(u)
    q = D.objects.exclude(channel='inapp').select_related('job', 'job__campaign').order_by('-queued_at')
    if not s.all:
        q = q.filter(branch_id__in=[int(x) for x in s.branches if x.isdigit()])
    for k, f in (('status', 'status'), ('channel', 'channel'), ('empId', 'emp_id')):
        if b.get(k):
            q = q.filter(**{f: str(b[k])})
    if b.get('event'):
        q = q.filter(job__event_key=str(b['event']))
    if str(b.get('branchId') or '').isdigit():
        q = q.filter(branch_id=int(b['branchId']))
    from .base import parse_date
    d0, d1 = parse_date(b.get('from')), parse_date(b.get('to'))
    if d0:
        q = q.filter(queued_at__date__gte=d0)
    if d1:
        q = q.filter(queued_at__date__lte=d1)
    return q


@action('notify.deliveries.list', module='notify', read=True)
def a_dlist(request, u, b):
    if b.get('status') == 'failed':
        b = dict(b, status='')
        q = _hist_q(u, b).filter(status__in=['failed', 'failed_perm'])
    else:
        q = _hist_q(u, b)
    em = emp_map()
    return paged(q, b, lambda d: ddict(d, em))


@action('notify.deliveries.get', module='notify', read=True)
def a_dget(request, u, b):
    q = _hist_q(u, {})
    d = q.filter(pk=int(b.get('id')) if str(b.get('id') or '').isdigit() else 0).first()
    if not d:
        err('NOT_FOUND', 'Not found.')
    em = emp_map()
    out = ddict(d, em)
    out['rendered'] = d.rendered
    out['template'] = '%s v%s' % (d.template.name, d.template.version) if d.template_id else ''
    out['providerId'] = d.provider_msg_id
    out['fallbacks'] = [ddict(x, em) for x in d.fallbacks.all()]
    return {'delivery': out}


@action('notify.deliveries.retry', module='notify')
def a_retry(request, u, b):
    q = _hist_q(u, {})
    done, refused = [], []
    for i in (b.get('ids') or [])[:500]:
        d = q.filter(pk=int(i) if str(i).isdigit() else 0).first()
        if not d:
            refused.append({'id': i, 'reason': 'Not found'})
        elif d.status == 'failed_perm':
            refused.append({'id': i, 'reason': 'Permanent failure (%s). Fix the phone number or template first.' % (d.error_message or d.error_code)})
        elif d.status != 'failed':
            refused.append({'id': i, 'reason': 'Only failed messages can be retried.'})
        else:
            d.status, d.next_attempt_at, d.attempts = 'queued', now(), 0
            d.save()
            done.append(i)
    if done:
        audit.record(u, 'notify.retry', 'delivery', ','.join(map(str, done[:20])), after={'count': len(done)}, request=request)
    return {'retried': done, 'refused': refused}


@action('notify.deliveries.export', module='notify', read=True)
def a_dexport(request, u, b):
    q = _hist_q(u, b)
    em = emp_map()
    from .reports import csv_response
    rows = [[d['queued'], d['name'], d['label'], d['channel'], d['status'], d['skip'], d['error'], d['attempts'], d['delivered'] or '', d['read'] or '']
            for d in (ddict(x, em) for x in q[:100000])]
    return csv_response('notification-history.csv', ['Queued', 'Employee', 'Message', 'Channel', 'Status', 'Skipped because', 'Error', 'Attempts', 'Delivered', 'Read'], rows)


@action('notify.usage', module='notify', read=True)
def a_usage(request, u, b):
    need(u, 'notify.config')
    t = now()
    out = {}
    for d in D.objects.filter(sent_at__year=t.year, sent_at__month=t.month).exclude(channel='inapp').select_related('job'):
        k = (d.channel, d.job.event_key)
        out[k] = out.get(k, 0) + 1
    return {'month': t.strftime('%Y-%m'), 'items': [{'channel': k[0], 'event': k[1], 'count': v} for k, v in sorted(out.items())], 'caps': ncfg()['caps']}


# ---------- actions: inbox, preferences, contact ----------
def _my_keys(u):
    me = V.prof(u).emp_id
    return [x for x in (me, 'u:%s' % u.pk) if x]


@action('notify.inbox', read=True)
def a_inbox(request, u, b):
    q = D.objects.filter(channel='inapp', emp_id__in=_my_keys(u)).order_by('-queued_at')
    if b.get('unread') in ('1', 1, True, 'true'):
        q = q.filter(read_in_app_at__isnull=True)
    unread = D.objects.filter(channel='inapp', emp_id__in=_my_keys(u), read_in_app_at__isnull=True).count()
    r = paged(q, dict(b, size=b.get('size') or 20), lambda d: {'id': str(d.pk), 'title': d.title, 'text': d.rendered, 'link': d.link, 'at': iso(d.queued_at),
                                                               'read': bool(d.read_in_app_at), 'event': d.job.event_key, 'entity': d.job.entity_id})
    r['unread'] = unread
    return r


@action('notify.inbox.read')
def a_inbox_read(request, u, b):
    q = D.objects.filter(channel='inapp', emp_id__in=_my_keys(u), read_in_app_at__isnull=True)
    if not b.get('all'):
        q = q.filter(pk__in=[int(x) for x in (b.get('ids') or []) if str(x).isdigit()])
    return {'marked': q.update(read_in_app_at=now())}


def _my_contact(u):
    me = V.prof(u).emp_id
    if not me:
        err('FORBIDDEN', 'Your login is not linked to an employee profile.')
    return contact(me)


def cpdict(c):
    p = prefs_of(c)
    return {'phone': c.phone_e164, 'verified': bool(c.phone_verified_at), 'verifiedAt': iso(c.phone_verified_at), 'method': c.verify_method,
            'whatsapp': c.wa_opt_in, 'sms': c.sms_opt_in, 'prefs': p, 'groups': GROUPS, 'notice': NOTICE_VERSION,
            'mandatoryGroups': sorted({EVENTS[k][1] for k in EVENTS if EVENTS[k][3]}),
            'channels': {'whatsapp': bool(provider('whatsapp')), 'sms': bool(provider('sms'))}}


@action('notify.prefs.get', read=True)
def a_prefs_get(request, u, b):
    return {'contact': cpdict(_my_contact(u))}


@action('notify.prefs.save')
def a_prefs_save(request, u, b):
    c = _my_contact(u)
    p = prefs_of(c)
    g = b.get('groups') or {}
    p['groups'] = {k: bool(g.get(k, True)) for k in GROUPS}
    order = [x for x in (b.get('order') or []) if x in ('whatsapp', 'sms')]
    p['order'] = order or ['whatsapp', 'sms']
    from .base import hm_to_min
    q = b.get('quiet') or p['quiet']
    if hm_to_min(q.get('from')) is None or hm_to_min(q.get('to')) is None:
        invalid(quiet='Enter quiet hours as HH:MM.')
    p['quiet'] = {'from': q['from'], 'to': q['to']}
    p['lang'] = str(b.get('lang') or 'en')[:5]
    c.prefs = p
    c.save(update_fields=['prefs'])
    return {'contact': cpdict(c)}


def _otp_hash(code, cid):
    return hmac.new(settings.SECRET_KEY.encode(), ('otp|%s|%s' % (cid, code)).encode(), hashlib.sha256).hexdigest()


@action('notify.contact.sendOtp')
def a_send_otp(request, u, b):
    c = _my_contact(u)
    phone = normalize_phone(b.get('phone') or c.phone_e164)
    if not phone:
        invalid(phone='Enter a valid mobile number, for example +91 98470 12345.')
    if OtpChallenge.objects.filter(contact=c, created_at__gte=now() - datetime.timedelta(hours=1)).count() >= 3:
        err('RATE_LIMITED', 'Too many codes requested. Try again in an hour.')
    p = provider('sms')
    tpl = template_for('contact.otp', 'sms')
    if not p or not tpl or not tpl.provider_template_id:
        err('INVALID_STATE', 'SMS is not set up, so a code cannot be sent. Ask your admin to confirm your number instead.')
    code = '%06d' % secrets.randbelow(1000000)
    OtpChallenge.objects.create(contact=c, phone=phone, code_hash=_otp_hash(code, c.pk), expires_at=now() + datetime.timedelta(minutes=10))
    try:
        adapter(p).send(phone, tpl, [render('{{%s}}' % n, {'code': code, 'company_name': str(V.cget('company', '') or '')}) for n in tpl.variables])
    except SendError as e:
        err('PROVIDER_ERROR', 'The code could not be sent: %s' % e)
    return {'sentTo': crypto.mask(phone)}


@action('notify.contact.verify')
def a_verify(request, u, b):
    c = _my_contact(u)
    o = OtpChallenge.objects.filter(contact=c, consumed_at__isnull=True).order_by('-created_at').first()
    if not o or o.expires_at < now():
        invalid(code='The code has expired. Ask for a new one.')
    if o.attempts >= 5:
        err('RATE_LIMITED', 'Too many wrong codes. Ask for a new code in 30 minutes.')
    if not hmac.compare_digest(o.code_hash, _otp_hash(str(b.get('code') or '').strip(), c.pk)):
        o.attempts += 1
        o.save(update_fields=['attempts'])
        invalid(code='That code is not right.')
    o.consumed_at = now()
    o.save(update_fields=['consumed_at'])
    c.phone_e164, c.phone_verified_at, c.verify_method = o.phone, now(), 'otp_sms'
    c.save()
    ConsentEvent.objects.create(contact=c, channel='all', action='verify', source='otp', actor=u)
    return {'contact': cpdict(c)}


@action('notify.contact.optin')
def a_optin(request, u, b):
    c = _my_contact(u)
    ch = b.get('channel')
    if ch not in ('whatsapp', 'sms'):
        invalid(channel='Choose a channel.')
    on = bool(b.get('on'))
    if on and not c.phone_verified_at:
        err('INVALID_STATE', 'Verify your phone number first.')
    if ch == 'whatsapp':
        c.wa_opt_in = on
    else:
        c.sms_opt_in = on
    c.save()
    ConsentEvent.objects.create(contact=c, channel=ch, action='opt_in' if on else 'opt_out', source='app', notice_version=NOTICE_VERSION, actor=u)
    if not on:
        D.objects.filter(emp_id=c.emp_id, channel=ch, status='queued').update(status='cancelled', skip_reason='opted_out')
    return {'contact': cpdict(c)}


@action('notify.contact.attest')
def a_attest(request, u, b):
    need(u, 'emp.edit')
    eid = str(b.get('empId') or '')
    if not scope_for(u).sees(eid, primary_only=True) or not V.Record.objects.filter(coll='employees', rid=eid).exists():
        err('NOT_FOUND', 'Employee not found.')
    reason = text(b, 'reason', 300)
    if len(reason) < 5:
        invalid(reason='Say how the employee gave consent (for example, signed form on 1 Dec).')
    c = contact(eid)
    phone = normalize_phone(b.get('phone') or c.phone_e164)
    if not phone:
        invalid(phone='Enter a valid mobile number.')
    chans = [x for x in (b.get('channels') or ['whatsapp', 'sms']) if x in ('whatsapp', 'sms')]
    c.phone_e164, c.phone_verified_at, c.verify_method = phone, now(), 'attested'
    c.wa_opt_in = 'whatsapp' in chans or c.wa_opt_in
    c.sms_opt_in = 'sms' in chans or c.sms_opt_in
    c.save()
    for ch in chans:
        ConsentEvent.objects.create(contact=c, channel=ch, action='attest', source='admin', notice_version=NOTICE_VERSION, actor=u, note=reason)
    audit.record(u, 'notify.attest', 'employee', eid, after={'channels': chans, 'reason': reason}, request=request)
    return {'contact': cpdict(c)}


@action('notify.contact.get', read=True)
def a_contact_get(request, u, b):
    need(u, 'emp.edit')
    eid = str(b.get('empId') or '')
    if not scope_for(u).sees(eid, primary_only=True):
        err('NOT_FOUND', 'Employee not found.')
    c = contact(eid)
    out = cpdict(c)
    out['history'] = [{'at': iso(x.at), 'channel': x.channel, 'action': x.action, 'source': x.source, 'by': x.actor.username if x.actor_id else '', 'note': x.note}
                      for x in c.consents.order_by('-at')[:50]]
    return {'contact': out}
