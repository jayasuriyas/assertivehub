from django.contrib.auth import login as dj_login
from django.contrib.auth.models import User
from django.core import signing
from django.http import HttpResponse, HttpResponseRedirect
from django.conf import settings


def enter(request):
    """One-time sign-in link from signup or the owner panel: /_enter?t=<signed token>."""
    try:
        d = signing.loads(request.GET.get('t', ''), salt='hub-enter', max_age=300)
    except signing.BadSignature:
        return HttpResponse('This link has expired. Please sign in normally.', status=403)
    if d.get('slug') != request.tenant.slug:
        return HttpResponse('Wrong company.', status=403)
    from core.views import cget, cset
    import time
    n = str(d.get('n', ''))
    used = {k: v for k, v in (cget('enter_used', {}) or {}).items() if v > time.time() - 600}
    if not n or n in used:
        return HttpResponse('This link was already used. Please sign in normally.', status=403)
    used[n] = time.time()
    cset('enter_used', used)
    u = User.objects.filter(pk=d.get('uid'), is_active=True).first()
    if not u:
        return HttpResponse('Account not found.', status=404)
    dj_login(request, u, backend='django.contrib.auth.backends.ModelBackend')
    request.session.set_expiry(settings.SESSION_COOKIE_AGE if d.get('long') else 8 * 3600)
    return HttpResponseRedirect('/')
