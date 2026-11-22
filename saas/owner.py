"""Owner panel for the person who sells the product. Protected by HUB_OWNER_PASSWORD."""
import datetime
import hashlib
import hmac
import html
import secrets
import os

from django.conf import settings
from django.core import signing
from django.db.models import Sum
from django.http import HttpResponse, HttpResponseRedirect
from django.middleware.csrf import get_token

from . import plans, state, tenancy
from .billing import add_months
from .models import Tenant, Payment, OwnerLog
from .pviews import page, _base_url

E_ = html.escape
COOKIE = 'hub_owner'


def _ok(request):
    try:
        return signing.loads(request.COOKIES.get(COOKIE, ''), salt='hub-owner', max_age=8 * 3600) == 'owner'
    except signing.BadSignature:
        return False


def login(request):
    import time
    from core.views import client_ip
    from .models import SignupAttempt
    pw = os.environ.get('HUB_OWNER_PASSWORD', '')
    ip = 'owner|' + client_ip(request)
    SignupAttempt.objects.filter(ip__startswith='owner|', at__lt=time.time() - 1800).delete()
    if request.method == 'POST' and (SignupAttempt.objects.filter(ip=ip[:45]).count() >= 5 or SignupAttempt.objects.filter(ip__startswith='owner|').count() >= 25):
        return page('Owner', '<div style="max-width:380px;margin:0 auto"><div class="c"><p class="err">Too many wrong passwords. Try again in 30 minutes.</p></div></div>', 429)
    if request.method == 'POST' and not (pw and hmac.compare_digest(hashlib.sha256(request.POST.get('pw', '').encode()).digest(), hashlib.sha256(pw.encode()).digest())):
        SignupAttempt.objects.create(ip=ip[:45], at=time.time())
        time.sleep(0.5)
    if request.method == 'POST' and pw and hmac.compare_digest(hashlib.sha256(request.POST.get('pw', '').encode()).digest(), hashlib.sha256(pw.encode()).digest()):
        r = HttpResponseRedirect('/owner/')
        r.set_cookie(COOKIE, signing.dumps('owner', salt='hub-owner'), httponly=True, samesite='Lax', secure=settings.CSRF_COOKIE_SECURE, max_age=8 * 3600)
        return r
    msg = '<p class="err">Wrong password.</p>' if request.method == 'POST' else ''
    if not pw:
        msg = '<p class="err">Set the HUB_OWNER_PASSWORD environment variable first.</p>'
    return page('Owner', '<div style="max-width:380px;margin:0 auto"><div class="c"><h3 style="margin-top:0">Owner sign in</h3>%s<form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="%s"><input type="password" name="pw" placeholder="Owner password" required><p><button class="b">Sign in</button></p></form></div></div>' % (msg, get_token(request)))


def logout(request):
    r = HttpResponseRedirect('/')
    r.delete_cookie(COOKIE)
    return r


def panel(request):
    if not _ok(request):
        return HttpResponseRedirect('/owner/login')
    tok = get_token(request)
    ts = Tenant.objects.order_by('-id')
    stc = {}
    rows = ''
    for t in ts:
        i = state.info(t)
        stc[i['state']] = stc.get(i['state'], 0) + 1
        opts = ''.join('<option value="%s"%s>%s</option>' % (k, ' selected' if k == t.plan else '', p['name']) for k, p in plans.PLANS.items())
        f = lambda act, extra, label: '<form method="post" action="/owner/act" style="display:inline"><input type="hidden" name="csrfmiddlewaretoken" value="%s"><input type="hidden" name="slug" value="%s"><input type="hidden" name="act" value="%s">%s<button class="b o" style="padding:4px 10px;font-size:13px">%s</button></form> ' % (tok, t.slug, act, extra, label)
        rows += '<div class="c" style="margin-bottom:10px"><b>%s</b> <span class="mu">%s.%s</span> <span class="tag">%s</span> <span class="tag" style="background:#667">%s</span><div class="mu" style="font-size:14px">%s · %s · %s · %s · joined %s · trial to %s · paid to %s</div><div style="margin-top:8px"><a class="b o" style="padding:4px 10px;font-size:13px" href="/owner/go/%s">Open as admin</a> %s%s%s%s</div></div>' % (
            E_(t.name), t.slug, E_(settings.BASE_DOMAIN), i['state'], i['planName'], E_(t.owner_name), E_(t.owner_email), E_(t.owner_phone), E_(t.state), t.created, t.trial_ends, t.paid_until or '-', t.slug,
            f('trial', '<input name="n" type="number" value="7" style="width:60px;padding:4px">', '+ trial days'),
            f('plan', '<select name="n" style="width:auto;padding:4px">%s</select>' % opts, 'Set plan'),
            f('manual', '<input name="n" type="number" value="30" style="width:60px;padding:4px">', '+ paid days (manual)'),
            f('resume' if t.status != 'active' else 'suspend', '', 'Resume' if t.status != 'active' else 'Suspend'))
    rev = Payment.objects.filter(status='paid').aggregate(s=Sum('base'))['s'] or 0
    mrr = sum(plans.PLANS[t.plan]['month'] for t in ts if state.info(t)['state'] == 'active')
    sm = '<div class="grid" style="margin-bottom:18px"><div class="c"><div class="mu">Companies</div><div class="pr">%d</div></div><div class="c"><div class="mu">Trial / Active / Expired</div><div class="pr">%d / %d / %d</div></div><div class="c"><div class="mu">Approx monthly (active, ex-GST)</div><div class="pr">₹%s</div></div><div class="c"><div class="mu">Collected so far (ex-GST)</div><div class="pr">₹%s</div></div></div>' % (
        ts.count(), stc.get('trial', 0), stc.get('active', 0), stc.get('expired', 0), format(mrr, ','), format(rev // 100, ','))
    logs = ''.join('<div class="mu" style="font-size:13px">%s · %s · %s %s</div>' % (l.at.strftime('%d %b %H:%M'), E_(l.who), E_(l.action), E_(l.slug)) for l in OwnerLog.objects.order_by('-id')[:15])
    return page('Owner', '<h2>Owner panel <a href="/owner/logout" style="font-size:14px">sign out</a></h2>' + sm + (rows or '<p class="mu">No companies yet.</p>') + '<h3>Recent actions</h3>' + logs)


def act(request):
    if not _ok(request) or request.method != 'POST':
        return HttpResponse('No', status=403)
    t = Tenant.objects.filter(slug=request.POST.get('slug', '')).first()
    a = request.POST.get('act', '')
    if not t:
        return HttpResponseRedirect('/owner/')
    try:
        n = int(request.POST.get('n', '0'))
    except ValueError:
        n = 0
    d = datetime.date.today()
    if a == 'trial':
        t.trial_ends = max(t.trial_ends, d) + datetime.timedelta(days=max(0, min(n, 365)))
    elif a == 'manual':
        t.paid_until = max(t.paid_until or d, d) + datetime.timedelta(days=max(0, min(n, 800)))
    elif a == 'plan' and request.POST.get('n') in plans.PLANS:
        t.plan = request.POST.get('n')
    elif a == 'suspend':
        t.status = 'suspended'
    elif a == 'resume':
        t.status = 'active'
    else:
        return HttpResponseRedirect('/owner/')
    t.last_notice = ''
    t.save()
    OwnerLog.objects.create(who='owner', action='%s %s' % (a, request.POST.get('n', '')), slug=t.slug)
    return HttpResponseRedirect('/owner/')


def go(request, slug):
    """Sign in to a customer's workspace for support. Logged. The link works for 5 minutes."""
    if not _ok(request):
        return HttpResponseRedirect('/owner/login')
    t = Tenant.objects.filter(slug=slug).first()
    if not t:
        return HttpResponse('No such company', status=404)
    from django.contrib.auth.models import User
    with tenancy.ctx(t.slug, t):
        u = User.objects.filter(profile__role='superadmin', is_active=True).order_by('pk').first()
        uid = u.pk if u else None
    if uid is None:
        return HttpResponse('No super admin', status=404)
    OwnerLog.objects.create(who='owner', action='support sign-in', slug=slug)
    return HttpResponseRedirect(_base_url(request, slug) + '/_enter?t=' + signing.dumps({'slug': slug, 'uid': uid, 'n': secrets.token_hex(12)}, salt='hub-enter'))
