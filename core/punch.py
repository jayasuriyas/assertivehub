"""Self check-in (GPS radius, selfie, QR) and biometric device punches. They all write the normal attendance record."""
import base64
import datetime
import hashlib
import hmac
import json
import math
import re
import secrets
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Attendance, Record, Config

try:
    from saas import tenancy as _tn
except Exception:  # pragma: no cover
    _tn = None


def _cget(k, d):
    c = Config.objects.filter(key=k).first()
    return c.value if c else d


def _cset(k, v):
    Config.objects.update_or_create(key=k, defaults={'value': v})


def _slug():
    return (_tn.current_slug() or '') if (settings.SAAS and _tn) else ''


def data_dir():
    return _tn.data_dir() if (settings.SAAS and _tn) else Path(settings.DATA_DIR)


def mins(t):
    m = re.match(r'^(\d{1,2}):(\d{2})', t or '')
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def hav(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def qr_token(day=None):
    day = day or datetime.date.today().isoformat()
    return hmac.new(settings.SECRET_KEY.encode(), ('qr|%s|%s' % (_slug(), day)).encode(), hashlib.sha256).hexdigest()[:12]


def dev_key():
    k = _cget('devkey', None)
    if not k:
        k = secrets.token_urlsafe(24)
        _cset('devkey', k)
    return k


def shift_for(eid, day, emp):
    """(start, grace) in minutes for this person on this day."""
    st = _cget('settings', {}) or {}
    start, grace = st.get('workStart') or '09:00', int(st.get('graceMin') if st.get('graceMin') is not None else 15)
    bid = str((emp or {}).get('branchId') or '')
    if bid.isdigit():  # v6: branch working hours come before the company default (FR-BR-12)
        from .models import Branch
        br = Branch.objects.filter(pk=int(bid)).first()
        if br and mins(br.work_start) is not None:
            start = br.work_start
    sid = None
    ro = Record.objects.filter(coll='rota', rid='ro_%s_%s' % (eid, day)).first()
    if ro and ro.data.get('shift') not in (None, '', 'off'):
        sid = ro.data.get('shift')
    elif emp and emp.get('shift'):
        sid = emp.get('shift')
    for s in st.get('shifts') or []:
        if isinstance(s, dict) and s.get('id') == sid and mins(s.get('start')) is not None:
            start = s['start']
    return mins(start), grace


def _pairs(tl):
    p, i = [], 0
    while i < len(tl):
        p.append([tl[i], tl[i + 1] if i + 1 < len(tl) else ''])
        i += 2
    return p


def flat(p):
    out = []
    for x in p or []:
        if isinstance(x, list):
            for t in x[:2]:
                if t:
                    out.append(str(t))
    return sorted(out)


SELFIE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}/[A-Za-z0-9_-]{1,80}_\d{4}\.jpg$')


class PunchError(Exception):
    pass


def record_punch(eid, when, source, photo_name=None):
    """Add one punch time. Returns the new record. Raises PunchError when it is refused."""
    day, hm = when.date().isoformat(), when.strftime('%H:%M')
    er = Record.objects.filter(coll='employees', rid=eid).first()
    emp = er.data if er else None
    if not emp or emp.get('status', 'Active') != 'Active' or emp.get('core'):
        raise PunchError('This person is not tracked for attendance.')
    ex = Attendance.objects.filter(day=day, eid=eid).first()
    d = dict(ex.data) if ex else {}
    if ex and d.get('br') not in ('self', 'device'):
        raise PunchError('Attendance for this day was already marked by a manager or admin.')
    if d.get('s') in ('V',) and not d.get('tl'):
        raise PunchError('This day is marked as leave.')
    tl = list(d.get('tl') or flat(d.get('p')))
    if hm in tl or (tl and abs(mins(hm) - mins(tl[-1])) < 1 and source == 'self'):
        raise PunchError('Already recorded a moment ago.')
    if source == 'self' and tl and mins(hm) - mins(tl[-1]) < 1:
        raise PunchError('Already recorded a moment ago.')
    tl = sorted(tl + [hm])
    if source == 'device':
        dedup = []
        for t in tl:  # device users often tap twice: ignore taps within 2 minutes of the previous one
            if dedup and mins(t) - mins(dedup[-1]) < 2:
                continue
            dedup.append(t)
        tl = dedup
    start, grace = shift_for(eid, day, emp)
    first = mins(tl[0])
    status = 'L' if (start is not None and first > start + grace) else 'P'
    sf = dict(d.get('sf') or {})
    if photo_name:
        sf[hm] = photo_name
    rec = {'s': status, 'p': _pairs(tl), 'tl': tl, 'by': 'check-in' if source == 'self' else 'biometric', 'br': source}
    if sf:
        rec['sf'] = sf
    if emp.get('branchId'):
        rec['b'] = str(emp['branchId'])
    Attendance.objects.update_or_create(day=day, eid=eid, defaults={'data': rec})
    if status == 'L' and len(tl) == 1:
        try:
            from django.db import transaction
            with transaction.atomic():
                from .v6 import notify
                notify.emit('attendance.late', 'attendance', '%s:%s' % (eid, day), 'late', [eid], {'day': day, 'kind': 'punched_late', 'shift_start': '%02d:%02d' % (start // 60, start % 60)})
        except Exception:
            pass
    return rec, (len(tl) % 2 == 1)


def check_in(u, emp_id, body):
    st = (_cget('settings', {}) or {}).get('checkin') or {}
    if not st.get('on'):
        raise PunchError('Check-in is not switched on.')
    if not emp_id:
        raise PunchError('Your login is not linked to an employee profile.')
    now = datetime.datetime.now()
    if st.get('gps'):
        sites = [s for s in (st.get('sites') or []) if isinstance(s, dict)]
        from .v6.scope import own_branches
        mine = own_branches(emp_id)
        sites = [s for s in sites if not s.get('branchId') or str(s['branchId']) in mine]  # v6: a site can belong to a branch
        try:
            lat, lng, acc = float(body.get('lat')), float(body.get('lng')), float(body.get('acc') or 0)
        except (TypeError, ValueError):
            raise PunchError('Location is needed. Allow location access in your browser and try again.')
        if not sites:
            raise PunchError('No office location is set. Ask your admin.')
        if acc > 150:
            raise PunchError('Your GPS signal is weak (accuracy %d m). Step outside or turn on high accuracy and try again.' % acc)
        best = None
        for s in sites:
            try:
                dist = hav(lat, lng, float(s['lat']), float(s['lng']))
            except (KeyError, TypeError, ValueError):
                continue
            rad = max(20, float(s.get('r') or 100))
            if dist <= rad:
                best = None
                break
            if best is None or dist < best[0]:
                best = (dist, s.get('name') or 'the office')
        if best:
            raise PunchError('You are about %d m from %s. Move inside the allowed area to check in.' % (best[0], best[1]))
    if st.get('qr'):
        if not hmac.compare_digest(str(body.get('qr') or ''), qr_token()):
            raise PunchError('Scan today\'s QR code at the office to check in.')
    photo = None
    if st.get('selfie'):
        m = re.match(r'^data:image/jpeg;base64,([A-Za-z0-9+/=]+)$', str(body.get('photo') or ''))
        if not m or len(m.group(1)) > 220000:
            raise PunchError('A selfie is needed. Allow camera access and try again.')
        raw = base64.b64decode(m.group(1))
        if raw[:3] != b'\xff\xd8\xff':
            raise PunchError('That picture is not valid.')
        photo = '%s/%s_%s.jpg' % (now.date().isoformat(), re.sub(r'[^A-Za-z0-9_-]', '', emp_id), now.strftime('%H%M'))
        f = data_dir() / 'selfies' / photo
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(raw)
    rec, is_in = record_punch(emp_id, now, 'self', photo)
    return {'kind': 'in' if is_in else 'out', 'time': now.strftime('%H:%M'), 'rec': rec, 'day': now.date().isoformat()}


def selfie_data(day, eid, hm):
    rec = Attendance.objects.filter(day=day, eid=eid).first()
    name = ((rec.data if rec else {}).get('sf') or {}).get(hm)
    if not isinstance(name, str) or not SELFIE_RE.match(name):
        return None
    base = (data_dir() / 'selfies').resolve()
    f = (base / name).resolve()
    if base not in f.parents or not f.is_file():
        return None
    return 'data:image/jpeg;base64,' + base64.b64encode(f.read_bytes()).decode()


def qr_svg(request):
    url = request.build_absolute_uri('/') + '?qr=' + qr_token()
    try:
        import segno
        return url, segno.make(url, error='m').svg_inline(scale=6, border=2)
    except Exception:
        return url, ''


# ---------- biometric devices ----------
def _pin_map():
    m = {}
    for r in Record.objects.filter(coll='employees'):
        e = r.data
        if e.get('core') or e.get('status', 'Active') != 'Active':
            continue
        for k in (str(e.get('bioId') or '').strip(), str(e.get('empId') or '').strip(), re.sub(r'\D', '', str(e.get('empId') or '')).lstrip('0')):
            if k and k not in m:
                m[k] = r.rid
    return m


def _device_ok(sn):
    for d in (_cget('settings', {}) or {}).get('devices') or []:
        if isinstance(d, dict) and d.get('sn') and d['sn'] == sn:
            return True
    return False


def _module(name):
    if not settings.SAAS:
        return True
    from saas import hooks
    return hooks.module_ok(name)


def ingest(rows):
    """rows: list of (pin, datetime). Returns (saved, skipped)."""
    pm = _pin_map()
    ok = bad = 0
    for pin, when in sorted(rows, key=lambda x: x[1]):
        eid = pm.get(str(pin).strip()) or pm.get(str(pin).strip().lstrip('0'))
        if not eid:
            bad += 1
            continue
        try:
            record_punch(eid, when, 'device')
            ok += 1
        except PunchError:
            bad += 1
    if ok:
        r = Config.objects.filter(key='rev').first()
        _cset('rev', (r.value if r else 1) + 1)
    return ok, bad


def _seen(sn):
    seen = _cget('devseen', {})
    seen[sn] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    _cset('devseen', seen)


@csrf_exempt
def adms_cdata(request):
    """ZKTeco / eSSL / Realtime push protocol. Set the device's cloud server to your company address."""
    sn = request.GET.get('SN', '')
    if not _module('biometric') or not _device_ok(sn):
        return HttpResponse('Unknown device', status=403)
    _seen(sn)
    if request.method == 'GET':
        return HttpResponse('GET OPTION FROM:%s\nErrorDelay=60\nDelay=30\nTransInterval=1\nTransFlag=TransData AttLog\nRealtime=1\nEncrypt=0\n' % sn, content_type='text/plain')
    if request.GET.get('table') != 'ATTLOG':
        return HttpResponse('OK', content_type='text/plain')
    rows = []
    for ln in request.body.decode('utf-8', 'ignore').splitlines():
        f = ln.strip().split('\t')
        if len(f) >= 2:
            try:
                rows.append((f[0], datetime.datetime.strptime(f[1][:19], '%Y-%m-%d %H:%M:%S')))
            except ValueError:
                pass
    ok, bad = ingest(rows[:500])
    return HttpResponse('OK: %d' % len(rows), content_type='text/plain')


@csrf_exempt
def adms_getrequest(request):
    sn = request.GET.get('SN', '')
    if not _module('biometric') or not _device_ok(sn):
        return HttpResponse('Unknown device', status=403)
    _seen(sn)
    return HttpResponse('OK', content_type='text/plain')


@csrf_exempt
def device_punch(request):
    """For other machines or a small bridge script: POST {"punches":[{"pin":"12","time":"2026-11-03 09:01:00"}]} with header X-Device-Key."""
    if not _module('biometric'):
        return JsonResponse({'ok': False, 'error': 'Not in your plan.'}, status=403)
    if not hmac.compare_digest(request.headers.get('X-Device-Key', ''), dev_key()):
        return JsonResponse({'ok': False, 'error': 'Bad key.'}, status=403)
    try:
        b = json.loads(request.body or b'{}')
        items = b.get('punches') if isinstance(b.get('punches'), list) else [b]
        rows = [(str(x['pin']), datetime.datetime.strptime(str(x['time'])[:19], '%Y-%m-%d %H:%M:%S')) for x in items[:500]]
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Bad data.'}, status=400)
    ok, bad = ingest(rows)
    return JsonResponse({'ok': True, 'saved': ok, 'skipped': bad})
