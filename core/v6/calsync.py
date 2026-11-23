"""Calendar integration (docs/release-v6/04-specifications.md section 2).

* Organizer connected (Google / Outlook): one provider event per meeting in the organizer's calendar; the provider
  sends the invitations. Deterministic ids (Google) / transactionId (Graph) prevent duplicates on retries.
* Organizer not connected: an .ics invitation (stable UID, increasing SEQUENCE, METHOD:CANCEL on cancel) is e-mailed.
* RSVPs are read back by polling linked upcoming events every 15 minutes (works without a public webhook).
OAuth client ids come from HUB_GOOGLE_CLIENT_ID/SECRET and HUB_MS_CLIENT_ID/SECRET. Tokens are stored encrypted.
"""
import base64
import datetime
import hashlib
import json
import os
import secrets
import urllib.parse
import urllib.request
import urllib.error

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect

from core import views as V
from core.models import CalendarConnection as CC, CalendarEventLink as L, Meeting, OAuthPending
from . import crypto, jobs
from .base import action, err, invalid, now, iso, local, module_on

PROVIDERS = {
    'google': {'auth': 'https://accounts.google.com/o/oauth2/v2/auth', 'token': 'https://oauth2.googleapis.com/token',
               'scope': 'openid email https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.freebusy',
               'env': 'HUB_GOOGLE', 'label': 'Google Calendar'},
    'microsoft': {'auth': 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize', 'token': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
                  'scope': 'openid email offline_access User.Read Calendars.ReadWrite', 'env': 'HUB_MS', 'label': 'Outlook Calendar'},
}


class CalError(Exception):
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


def client(p):
    e = PROVIDERS[p]['env']
    cid, sec = os.environ.get(e + '_CLIENT_ID', ''), os.environ.get(e + '_CLIENT_SECRET', '')
    return (cid, sec) if cid and sec else (None, None)


def available():
    return {p: bool(client(p)[0]) for p in PROVIDERS}


def _host():
    if settings.SAAS:
        from saas import tenancy
        return '%s.%s' % (tenancy.current_slug(), settings.BASE_DOMAIN)
    return os.environ.get('HUB_PUBLIC_HOST') or (settings.ALLOWED_HOSTS[0].lstrip('.') if settings.ALLOWED_HOSTS else 'localhost')


def redirect_uri(request, p):
    if settings.SAAS:
        return 'https://%s/oauth/%s/callback' % (settings.BASE_DOMAIN, p)
    return request.build_absolute_uri('/oauth/%s/callback' % p)


def _req(method, url, token=None, data=None, form=None, timeout=15):
    h = {'Accept': 'application/json'}
    body = None
    if token:
        h['Authorization'] = 'Bearer ' + token
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h['Content-Type'] = 'application/x-www-form-urlencoded'
    elif data is not None:
        body = json.dumps(data).encode()
        h['Content-Type'] = 'application/json'
    r = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            raw = x.read().decode('utf-8', 'replace')
            return x.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            j = json.loads(raw)
        except ValueError:
            j = {'raw': raw[:300]}
        return e.code, j
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CalError('Network error: %s' % e, 0)


def _tokens(c):
    """A valid access token for the connection, refreshing it when needed."""
    at = crypto.dec(c.access_token_enc)
    if at and c.token_expiry and c.token_expiry > now() + datetime.timedelta(minutes=2):
        return at
    cid, sec = client(c.provider)
    rt = crypto.dec(c.refresh_token_enc)
    if not cid or not rt:
        c.status = 'needs_consent'
        c.save(update_fields=['status'])
        raise CalError('Calendar connection needs to be renewed', 401)
    st, j = _req('POST', PROVIDERS[c.provider]['token'], form={'client_id': cid, 'client_secret': sec, 'refresh_token': rt, 'grant_type': 'refresh_token'})
    if st != 200 or not j.get('access_token'):
        c.status, c.last_error = ('revoked' if j.get('error') == 'invalid_grant' else 'error'), str(j.get('error_description') or j.get('error') or st)[:300]
        c.save(update_fields=['status', 'last_error'])
        raise CalError('Token refresh failed: %s' % c.last_error, 401)
    c.access_token_enc = crypto.enc(j['access_token'])
    if j.get('refresh_token'):
        c.refresh_token_enc = crypto.enc(j['refresh_token'])
    c.token_expiry = now() + datetime.timedelta(seconds=int(j.get('expires_in', 3600)))
    c.status = 'active'
    c.save()
    return j['access_token']


# ---------- event body ----------
def _people(m):
    em = {r.rid: r.data for r in V.Record.objects.filter(coll='employees')}
    out = []
    for p in m.participants.all():
        if p.emp_id:
            e = em.get(p.emp_id) or {}
            if p.emp_id != m.organizer_emp and '@' in str(e.get('email', '')):
                out.append({'email': e['email'], 'name': e.get('name', ''), 'optional': p.role == 'optional', 'pid': p.pk})
        elif p.ext_email:
            out.append({'email': p.ext_email, 'name': p.ext_name, 'optional': p.role == 'optional', 'pid': p.pk})
    org = em.get(m.organizer_emp) or {}
    return out, {'email': org.get('email', '') or os.environ.get('HUB_EMAIL_FROM', 'no-reply@localhost'), 'name': org.get('name', '')}


def _location(m):
    if m.location_type == 'online':
        return 'Online' + (' (%s)' % m.platform.replace('_', ' ').title() if m.platform else '')
    parts = [m.room.name if m.room_id else '', m.location_text, m.branch.name if m.branch_id and m.location_type in ('office', 'hybrid') else '']
    return ', '.join(x for x in parts if x)


def _description(m):
    lines = []
    for i, a in enumerate(m.agenda or [], 1):
        lines.append('%d. %s' % (i, a.get('title', '')))
    if m.description:
        lines += ['', m.description]
    if m.join_url:
        lines += ['', 'Join: ' + m.join_url]
    lines += ['', 'Open in Assertive Hub: https://%s/#meetings/%s' % (_host(), m.pk)]
    return '\n'.join(lines).strip()


def uid(m):
    return 'mtg-%s@%s' % (m.uid, _host())


# ---------- ICS (.ics e-mail invitations) ----------
def _esc(s):
    return str(s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r', '').replace('\n', '\\n')


def _fold(line):
    b = line.encode('utf-8')
    if len(b) <= 75:
        return line
    out, cur = [], b''
    for ch in line:
        e = ch.encode('utf-8')
        if len(cur) + len(e) > (75 if not out else 74):
            out.append(cur.decode('utf-8'))
            cur = b''
        cur += e
    out.append(cur.decode('utf-8'))
    return '\r\n '.join(out)


def ics(m, method='REQUEST', attendees=None, organizer=None):
    f = lambda d: d.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if attendees is None:
        attendees, organizer = _people(m)
    L_ = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Assertive Hub//Meetings//EN', 'CALSCALE:GREGORIAN', 'METHOD:%s' % method, 'BEGIN:VEVENT',
          'UID:%s' % uid(m), 'SEQUENCE:%d' % m.sequence, 'DTSTAMP:%s' % f(now()), 'DTSTART:%s' % f(m.start_at), 'DTEND:%s' % f(m.end_at),
          'SUMMARY:%s' % _esc(m.title), 'LOCATION:%s' % _esc(_location(m)), 'DESCRIPTION:%s' % _esc(_description(m)),
          'STATUS:%s' % ('CANCELLED' if method == 'CANCEL' else 'CONFIRMED'), 'CLASS:%s' % ('PRIVATE' if m.visibility == 'private' else 'PUBLIC'),
          'ORGANIZER;CN=%s:mailto:%s' % (_esc(organizer.get('name')), organizer.get('email'))]
    if m.join_url:
        L_.append('URL:%s' % m.join_url)
    for a in attendees:
        L_.append('ATTENDEE;CN=%s;ROLE=%s;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:%s' % (_esc(a['name']), 'OPT-PARTICIPANT' if a['optional'] else 'REQ-PARTICIPANT', a['email']))
    L_ += ['END:VEVENT', 'END:VCALENDAR']
    return '\r\n'.join(_fold(x) for x in L_) + '\r\n'


def send_ics(m, method):
    from .notify import send_email, SendError
    from .meetings import rsvp_token
    att, org = _people(m)
    body = ics(m, method, att, org)
    lt = local(m.start_at, m.timezone)
    subj = ('Cancelled: ' if method == 'CANCEL' else ('Updated: ' if m.sequence else 'Invitation: ')) + '%s (%s %s)' % (m.title, lt.strftime('%a %d %b'), lt.strftime('%H:%M'))
    sent = 0
    for a in att:
        text = '%s\n\n%s\n%s\n' % (subj, _location(m), _description(m))
        if method != 'CANCEL':
            p = m.participants.filter(pk=a['pid']).first()
            if p and not p.emp_id:
                text += '\nReply: https://%s/m/%s\n' % (_host(), rsvp_token(m, p))
        try:
            send_email(a['email'], subj, text, [('invite.ics', body, 'text/calendar; method=%s; charset=utf-8' % method)])
            sent += 1
        except SendError:
            pass
    return sent


# ---------- provider events ----------
def _google_body(m, att):
    b = {'id': 'mtg' + m.uid.hex, 'iCalUID': uid(m), 'summary': m.title, 'location': _location(m), 'description': _description(m),
         'start': {'dateTime': m.start_at.isoformat(), 'timeZone': m.timezone}, 'end': {'dateTime': m.end_at.isoformat(), 'timeZone': m.timezone},
         'attendees': [{'email': a['email'], 'displayName': a['name'], 'optional': a['optional']} for a in att], 'reminders': {'useDefault': False, 'overrides': []},
         'visibility': 'private' if m.visibility == 'private' else 'default', 'sequence': m.sequence}
    if m.location_type in ('online', 'hybrid') and m.platform == 'google_meet' and not m.join_url:
        b['conferenceData'] = {'createRequest': {'requestId': 'mtg%s-%s' % (m.uid.hex[:16], m.sequence), 'conferenceSolutionKey': {'type': 'hangoutsMeet'}}}
    return b


def _graph_body(m, att):
    b = {'subject': m.title, 'body': {'contentType': 'text', 'content': _description(m)}, 'start': {'dateTime': m.start_at.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'UTC'},
         'end': {'dateTime': m.end_at.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'UTC'}, 'location': {'displayName': _location(m)},
         'attendees': [{'emailAddress': {'address': a['email'], 'name': a['name']}, 'type': 'optional' if a['optional'] else 'required'} for a in att],
         'isReminderOn': False, 'sensitivity': 'private' if m.visibility == 'private' else 'normal', 'transactionId': str(m.uid)}
    if m.location_type in ('online', 'hybrid') and m.platform == 'teams' and not m.join_url:
        b['isOnlineMeeting'] = True
        b['onlineMeetingProvider'] = 'teamsForBusiness'
    return b


def push_provider(m, c):
    link, _ = L.objects.get_or_create(meeting=m, connection=c)
    tok = _tokens(c)
    att, _o = _people(m)
    G = 'https://www.googleapis.com/calendar/v3/calendars/primary/events'
    M = 'https://graph.microsoft.com/v1.0/me/events'
    if m.status == 'cancelled':
        if link.provider_event_id:
            if c.provider == 'google':
                st, j = _req('DELETE', '%s/%s?sendUpdates=all' % (G, link.provider_event_id), tok)
            else:
                st, j = _req('POST', '%s/%s/cancel' % (M, link.provider_event_id), tok, {'comment': m.cancel_reason or 'Cancelled'})
            if st >= 400 and st not in (404, 410):
                raise CalError(str(j)[:200], st)
        link.status = 'deleted'
    else:
        if c.provider == 'google':
            body = _google_body(m, att)
            if link.provider_event_id:
                st, j = _req('PUT', '%s/%s?sendUpdates=all&conferenceDataVersion=1' % (G, link.provider_event_id), tok, body)
            else:
                st, j = _req('POST', '%s?sendUpdates=all&conferenceDataVersion=1' % G, tok, body)
                if st == 409:  # created by an earlier attempt: deterministic id
                    st, j = _req('PUT', '%s/%s?sendUpdates=all&conferenceDataVersion=1' % (G, body['id']), tok, body)
            if st in (404, 410) and link.provider_event_id:
                link.status, link.last_error = 'deleted_externally', 'Removed from the calendar outside the app'
                link.save()
                return link
            if st >= 400:
                raise CalError(str((j.get('error') or {}).get('message') or j)[:200], st)
            link.provider_event_id, link.etag = j.get('id', body['id']), j.get('etag', '')
            hl = next((e.get('uri') for e in ((j.get('conferenceData') or {}).get('entryPoints') or []) if e.get('entryPointType') == 'video'), '')
        else:
            body = _graph_body(m, att)
            if link.provider_event_id:
                body.pop('transactionId', None)
                st, j = _req('PATCH', '%s/%s' % (M, link.provider_event_id), tok, body)
            else:
                st, j = _req('POST', M, tok, body)
            if st in (404, 410) and link.provider_event_id:
                link.status, link.last_error = 'deleted_externally', 'Removed from the calendar outside the app'
                link.save()
                return link
            if st >= 400:
                raise CalError(str((j.get('error') or {}).get('message') or j)[:200], st)
            link.provider_event_id, link.etag = j.get('id', link.provider_event_id), j.get('@odata.etag', '')
            hl = ((j.get('onlineMeeting') or {}).get('joinUrl')) or ''
        if hl and not m.join_url:
            Meeting.objects.filter(pk=m.pk).update(join_url=hl[:500])
        link.status = 'synced'
    link.synced_sequence, link.last_synced_at, link.last_error = m.sequence, now(), ''
    link.save()
    c.last_sync_at = now()
    c.save(update_fields=['last_sync_at'])
    return link


def organizer_connection(m):
    if not module_on('calsync') or not m.organizer_user_id:
        return None
    c = CC.objects.filter(user_id=m.organizer_user_id, status='active').first()
    return c if c and client(c.provider)[0] else None


def queue(m):
    if m.record_only:
        return
    jobs.enqueue('calsync.push', {'m': m.pk, 'seq': m.sequence}, unique_key='cal:%s:%s' % (m.pk, m.sequence), max_attempts=7)


@jobs.handler('calsync.push')
def _push(payload, job):
    m = Meeting.objects.filter(pk=payload.get('m')).select_related('room', 'branch').prefetch_related('participants').first()
    if not m or m.sequence != payload.get('seq'):
        return  # a newer version has its own job
    c = organizer_connection(m)
    if c:
        try:
            push_provider(m, c)
            return
        except CalError as e:
            lk = L.objects.filter(meeting=m, connection=c).first()
            if lk:
                lk.status, lk.last_error = 'error', str(e)[:300]
                lk.save()
            if e.status in (401, 403):
                pass  # connection revoked: fall back to .ics below
            else:
                raise  # temporary: the job runner retries with back-off
    send_ics(m, 'CANCEL' if m.status == 'cancelled' else 'REQUEST')


def poll_rsvps():
    """Read attendee responses for upcoming synced meetings (fallback that needs no public webhook)."""
    t = now()
    n = 0
    for lk in L.objects.filter(status='synced', meeting__status='scheduled', meeting__start_at__gte=t, meeting__start_at__lte=t + datetime.timedelta(days=30)).select_related('meeting', 'connection')[:200]:
        c = lk.connection
        try:
            tok = _tokens(c)
        except CalError:
            continue
        if c.provider == 'google':
            st, j = _req('GET', 'https://www.googleapis.com/calendar/v3/calendars/primary/events/%s' % lk.provider_event_id, tok)
            resp = {a.get('email', '').lower(): {'accepted': 'accepted', 'declined': 'declined', 'tentative': 'tentative'}.get(a.get('responseStatus')) for a in (j.get('attendees') or [])}
            if st == 200 and j.get('status') != 'cancelled':
                ext = _external_change(lk.meeting, j.get('start', {}).get('dateTime'), j.get('summary'))
            else:
                ext = None
        else:
            st, j = _req('GET', 'https://graph.microsoft.com/v1.0/me/events/%s' % lk.provider_event_id, tok)
            resp = {((a.get('emailAddress') or {}).get('address') or '').lower(): {'accepted': 'accepted', 'declined': 'declined', 'tentativelyAccepted': 'tentative'}.get((a.get('status') or {}).get('response'))
                    for a in (j.get('attendees') or [])}
            ext = None
        if st in (404, 410):
            lk.status, lk.last_error = 'deleted_externally', 'Removed from the calendar outside the app'
            lk.save()
            continue
        if st != 200:
            continue
        em = {r.rid: r.data for r in V.Record.objects.filter(coll='employees')}
        for p in lk.meeting.participants.all():
            addr = (p.ext_email or (em.get(p.emp_id) or {}).get('email', '')).lower()
            r = resp.get(addr)
            if r and r != p.rsvp:
                p.rsvp, p.rsvp_at = r, now()
                p.save(update_fields=['rsvp', 'rsvp_at'])
                n += 1
        if ext:
            lk.status, lk.last_error = 'conflict', ext
            lk.save()
    return n


def _external_change(m, start_s, summary):
    """Edits made in Google are flagged for the organizer, not applied (v6 rule)."""
    try:
        st = datetime.datetime.fromisoformat(str(start_s).replace('Z', '+00:00')) if start_s else None
    except ValueError:
        st = None
    if st and abs((st - m.start_at).total_seconds()) > 60:
        return 'Time changed in the calendar to %s' % local(st, m.timezone).strftime('%d %b %H:%M')
    if summary and summary != m.title:
        return 'Title changed in the calendar to "%s"' % summary[:80]
    return None


def freebusy(emp_ids, start, end):
    """{emp_id: [busy blocks]} for participants with a connected calendar (only free/busy, never details)."""
    if not module_on('calsync'):
        return {}
    out = {}
    profs = {p.emp_id: p.user for p in V.Profile.objects.filter(emp_id__in=list(emp_ids)).select_related('user')}
    for eid, u in profs.items():
        c = CC.objects.filter(user=u, status='active').first()
        if not c or not client(c.provider)[0]:
            continue
        try:
            tok = _tokens(c)
            if c.provider == 'google':
                st, j = _req('POST', 'https://www.googleapis.com/calendar/v3/freeBusy', tok, {'timeMin': iso(start), 'timeMax': iso(end), 'items': [{'id': 'primary'}]}, timeout=6)
                busy = ((j.get('calendars') or {}).get('primary') or {}).get('busy') or []
            else:
                st, j = _req('POST', 'https://graph.microsoft.com/v1.0/me/calendar/getSchedule', tok,
                             {'schedules': [c.account_email], 'startTime': {'dateTime': start.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'UTC'},
                              'endTime': {'dateTime': end.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': 'UTC'}}, timeout=6)
                busy = [x for v in (j.get('value') or []) for x in (v.get('scheduleItems') or []) if x.get('status') != 'free']
            # our own synced meetings are already checked internally
            own = set(L.objects.filter(connection=c).values_list('provider_event_id', flat=True))
            out[eid] = [b for b in busy if b.get('id') not in own]
        except CalError:
            continue
    return out


# ---------- actions ----------
def cdict(c):
    return {'provider': c.provider, 'label': PROVIDERS[c.provider]['label'], 'email': c.account_email, 'status': c.status, 'lastSync': iso(c.last_sync_at), 'error': c.last_error,
            'errors': L.objects.filter(connection=c, status__in=['error', 'conflict', 'deleted_externally']).count()}


@action('cal.connections', module='calsync', read=True)
def a_conns(request, u, b):
    return {'items': [cdict(c) for c in CC.objects.filter(user=u)], 'available': available()}


@action('cal.connect', module='calsync')
def a_connect(request, u, b):
    p = b.get('provider')
    if p not in PROVIDERS:
        invalid(provider='Choose Google or Outlook.')
    cid, sec = client(p)
    if not cid:
        err('SERVER_CONFIG', '%s is not set up on this server yet. Ask the system administrator to add the OAuth app keys.' % PROVIDERS[p]['label'])
    verifier = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(24)
    OAuthPending.objects.filter(expires_at__lt=now()).delete()
    OAuthPending.objects.create(nonce=nonce, user=u, provider=p, verifier_enc=crypto.enc(verifier), expires_at=now() + datetime.timedelta(minutes=10))
    slug = ''
    if settings.SAAS:
        from saas import tenancy as _t
        slug = _t.current_slug() or ''
    state = signing.dumps({'s': slug, 'n': nonce, 'p': p}, salt='cal-oauth')
    ch = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    q = {'client_id': cid, 'redirect_uri': redirect_uri(request, p), 'response_type': 'code', 'scope': PROVIDERS[p]['scope'], 'state': state,
         'code_challenge': ch, 'code_challenge_method': 'S256'}
    if p == 'google':
        q.update(access_type='offline', prompt='consent', include_granted_scopes='true')
    return {'authUrl': PROVIDERS[p]['auth'] + '?' + urllib.parse.urlencode(q)}


@action('cal.disconnect', module='calsync')
def a_disconnect(request, u, b):
    c = CC.objects.filter(user=u, provider=b.get('provider')).first()
    if not c:
        err('NOT_FOUND', 'Not connected.')
    if b.get('removeEvents'):
        for lk in L.objects.filter(connection=c, status='synced').select_related('meeting'):
            try:
                tok = _tokens(c)
                if c.provider == 'google':
                    _req('DELETE', 'https://www.googleapis.com/calendar/v3/calendars/primary/events/%s?sendUpdates=none' % lk.provider_event_id, tok)
                else:
                    _req('DELETE', 'https://graph.microsoft.com/v1.0/me/events/%s' % lk.provider_event_id, tok)
            except CalError:
                break
    rt = crypto.dec(c.refresh_token_enc)
    if rt and c.provider == 'google':
        try:
            _req('POST', 'https://oauth2.googleapis.com/revoke', form={'token': rt}, timeout=6)
        except CalError:
            pass
    c.delete()
    return {'ok': True}


@action('cal.resync', module='calsync')
def a_resync(request, u, b):
    from .meetings import _get
    m = _get(u, b.get('meetingId'), manage=True)
    L.objects.filter(meeting=m).update(status='pending', last_error='')
    jobs.enqueue('calsync.push', {'m': m.pk, 'seq': m.sequence}, unique_key='cal:%s:%s:r%s' % (m.pk, m.sequence, int(now().timestamp())))
    return {'ok': True}


def oauth_callback(request, provider):
    """GET /oauth/<provider>/callback. In SaaS this runs on the platform domain and switches to the company named in the
    signed state (providers do not allow wildcard redirect URIs)."""
    if provider not in PROVIDERS:
        return HttpResponse('Unknown provider', status=404)
    try:
        st = signing.loads(request.GET.get('state', ''), salt='cal-oauth', max_age=600)
    except signing.BadSignature:
        return HttpResponse('This sign-in link has expired. Please try connecting again.', status=400)
    slug = st.get('s') or ''
    if settings.SAAS:
        from saas import tenancy
        from saas.models import Tenant
        t = Tenant.objects.filter(slug=slug).first()
        if not t:
            return HttpResponse('Unknown company.', status=404)
        with tenancy.ctx(slug, t):
            msg = _finish(request, provider, st)
        return HttpResponseRedirect('https://%s.%s/#settings?cal=%s' % (slug, settings.BASE_DOMAIN, msg))
    msg = _finish(request, provider, st)
    return HttpResponseRedirect('/#settings?cal=%s' % msg)


def _finish(request, provider, st):
    with transaction.atomic():
        pend = OAuthPending.objects.filter(nonce=st.get('n'), provider=provider, expires_at__gte=now()).first()
        if not pend or request.GET.get('error'):
            return 'cancelled'
        verifier = crypto.dec(pend.verifier_enc)
        u = pend.user
        pend.delete()
        cid, sec = client(provider)
        code, j = _req('POST', PROVIDERS[provider]['token'], form={'client_id': cid, 'client_secret': sec, 'code': request.GET.get('code', ''), 'grant_type': 'authorization_code',
                                                                     'redirect_uri': redirect_uri(request, provider), 'code_verifier': verifier})
        if code != 200 or not j.get('access_token'):
            return 'failed'
        email = ''
        idt = j.get('id_token', '')
        if idt.count('.') == 2:
            try:
                pl = idt.split('.')[1]
                email = json.loads(base64.urlsafe_b64decode(pl + '=' * (-len(pl) % 4))).get('email', '') or ''
            except ValueError:
                email = ''
        c, _ = CC.objects.get_or_create(user=u, provider=provider)
        c.account_email = email[:200] or c.account_email
        c.access_token_enc = crypto.enc(j['access_token'])
        if j.get('refresh_token'):
            c.refresh_token_enc = crypto.enc(j['refresh_token'])
        c.token_expiry = now() + datetime.timedelta(seconds=int(j.get('expires_in', 3600)))
        c.status, c.last_error = 'active', ''
        c.save()
    return 'connected'
