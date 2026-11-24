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
from .models import Tenant, Payment, OwnerLog, Lead, Post
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
        return page('Owner', '<div style="max-width:380px;margin:0 auto"><div class="c"><p class="err">Too many wrong passwords. Try again in 30 minutes.</p></div></div>', 429, request=request)
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
    return page('Owner', '<div style="max-width:380px;margin:0 auto"><div class="c"><h3 style="margin-top:0">Owner sign in</h3>%s<form method="post"><input type="hidden" name="csrfmiddlewaretoken" value="%s"><input type="password" name="pw" placeholder="Owner password" required><p><button class="b">Sign in</button></p></form></div></div>' % (msg, get_token(request)), request=request)


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
    return page('Owner', tabs('companies') + sm + (rows or '<p class="mu">No companies yet.</p>') + '<h3>Recent actions</h3>' + logs, request=request)


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


# ---------- website: leads and blog ----------
def tabs(on):
    n = Lead.objects.filter(status='new').count()
    items = [('companies', '/owner/', 'Companies'), ('leads', '/owner/leads', 'Leads%s' % (' (%d new)' % n if n else '')), ('blog', '/owner/blog', 'Blog')]
    return '<h2 style="display:flex;gap:16px;align-items:center;flex-wrap:wrap">Owner panel <span style="font-size:15px;font-weight:600">%s</span> <a href="/owner/logout" style="font-size:14px;margin-left:auto">Sign out</a></h2>' % ' · '.join(
        '<b>%s</b>' % E_(t) if k == on else '<a href="%s">%s</a>' % (u, E_(t)) for k, u, t in items)


def _hidden(tok, **kw):
    return '<input type="hidden" name="csrfmiddlewaretoken" value="%s">' % tok + ''.join('<input type="hidden" name="%s" value="%s">' % (k, E_(str(v))) for k, v in kw.items())


def leads(request):
    if not _ok(request):
        return HttpResponseRedirect('/owner/login')
    tok, st = get_token(request), request.GET.get('status', 'open')
    q = Lead.objects.order_by('-id')
    q = q.exclude(status='closed') if st == 'open' else q.filter(status=st) if st in ('new', 'contacted', 'closed') else q
    rows = ''
    for l in q[:200]:
        det = ' · '.join(E_(x) for x in [l.email, l.phone, l.company, l.team_size and 'team ' + l.team_size, l.topic, l.preferred and 'prefers ' + l.preferred] if x)
        acts = ''.join('<form method="post" action="/owner/leads/act" style="display:inline">%s<button class="b o" style="padding:4px 10px;font-size:13px">%s</button></form> ' % (_hidden(tok, id=l.pk, act=a), lab)
                       for a, lab in [('contacted', 'Mark contacted'), ('closed', 'Close'), ('new', 'Reopen')] if a != l.status)
        rows += ('<div class="c" style="margin-bottom:10px"><b>%s</b> <span class="tag">%s</span> <span class="tag" style="background:#667">%s</span> <span class="mu" style="font-size:13px">%s</span>'
                 '<div class="mu" style="font-size:14px">%s</div><p style="white-space:pre-wrap;margin:8px 0">%s</p>'
                 '<form method="post" action="/owner/leads/act" style="display:flex;gap:8px;margin:8px 0">%s<input name="note" value="%s" placeholder="Private note" maxlength="500"><button class="b o" style="padding:4px 10px;font-size:13px">Save note</button></form>%s'
                 '<a class="b o" style="padding:4px 10px;font-size:13px" href="mailto:%s">Email</a></div>') % (
            E_(l.name), 'Demo' if l.kind == 'demo' else 'Contact', E_(l.status), l.created.strftime('%d %b %Y %H:%M'), det, E_(l.message), _hidden(tok, id=l.pk, act='note'), E_(l.note), acts, E_(l.email))
    filt = ' · '.join('<b>%s</b>' % lab if k == st else '<a href="/owner/leads?status=%s">%s</a>' % (k, lab) for k, lab in [('open', 'Open'), ('new', 'New'), ('contacted', 'Contacted'), ('closed', 'Closed'), ('all', 'All')])
    return page('Owner', tabs('leads') + '<p>%s</p>' % filt + (rows or '<p class="mu">No leads here.</p>'), request=request)


def lead_act(request):
    if not _ok(request) or request.method != 'POST':
        return HttpResponse('No', status=403)
    l = Lead.objects.filter(pk=request.POST.get('id') if str(request.POST.get('id', '')).isdigit() else 0).first()
    a = request.POST.get('act', '')
    if l and a in ('new', 'contacted', 'closed'):
        l.status = a
        l.save(update_fields=['status'])
    elif l and a == 'note':
        l.note = request.POST.get('note', '')[:500]
        l.save(update_fields=['note'])
    return HttpResponseRedirect('/owner/leads')


def blog(request):
    if not _ok(request):
        return HttpResponseRedirect('/owner/login')
    rows = ''.join('<div class="c" style="margin-bottom:10px"><b>%s</b> <span class="tag"%s>%s</span> <span class="mu" style="font-size:13px">/blog/%s · %s</span><div style="margin-top:8px"><a class="b o" style="padding:4px 10px;font-size:13px" href="/owner/blog/%d">Edit</a>%s</div></div>' % (
        E_(p.title), '' if p.published else ' style="background:#667"', 'Published' if p.published else 'Draft', E_(p.slug), p.updated.strftime('%d %b %Y'), p.pk,
        ' <a class="b o" style="padding:4px 10px;font-size:13px" href="/blog/%s" target="_blank" rel="noopener">View</a>' % E_(p.slug) if p.published else '') for p in Post.objects.order_by('-updated'))
    return page('Owner', tabs('blog') + '<p><a class="b" href="/owner/blog/new">Write a new post</a></p>' + (rows or '<p class="mu">No posts yet.</p>'), request=request)


def _slugify(s):
    import re
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', s.lower())).strip('-')[:80]


def blog_edit(request, pid):
    import re
    from django.utils import timezone
    if not _ok(request):
        return HttpResponseRedirect('/owner/login')
    p = Post.objects.filter(pk=int(pid)).first() if pid.isdigit() else None
    if pid != 'new' and not p:
        return HttpResponseRedirect('/owner/blog')
    err = ''
    if request.method == 'POST':
        if request.POST.get('act') == 'delete' and p:
            OwnerLog.objects.create(who='owner', action='blog delete %s' % p.slug[:40])
            p.delete()
            return HttpResponseRedirect('/owner/blog')
        v = {k: request.POST.get(k, '').strip() for k in ('title', 'slug', 'excerpt', 'body', 'tag', 'author')}
        slug = _slugify(v['slug'] or v['title'])
        if not 3 <= len(v['title']) <= 150:
            err = 'Title: 3 to 150 characters.'
        elif not slug or not re.match(r'^[a-z0-9-]+$', slug):
            err = 'Web address: letters, numbers and hyphens.'
        elif Post.objects.filter(slug=slug).exclude(pk=p.pk if p else None).exists():
            err = 'Another post already uses that web address.'
        elif len(v['body']) < 20:
            err = 'Write the article (at least 20 characters).'
        if not err:
            p = p or Post()
            p.title, p.slug, p.excerpt, p.body, p.tag, p.author = v['title'], slug, v['excerpt'][:300], v['body'], v['tag'][:30], v['author'][:60]
            pub = request.POST.get('published') == '1'
            if pub and not p.published:
                p.published_at = timezone.now()
            p.published = pub
            p.save()
            OwnerLog.objects.create(who='owner', action='blog %s %s' % ('publish' if pub else 'save', slug[:40]))
            return HttpResponseRedirect('/owner/blog/%d?saved=1' % p.pk)
    else:
        v = {'title': p.title, 'slug': p.slug, 'excerpt': p.excerpt, 'body': p.body, 'tag': p.tag, 'author': p.author} if p else {'author': plans.PRODUCT + ' team'}
    tok = get_token(request)
    f = lambda k, lab, extra='': '<label>%s</label><input name="%s" value="%s"%s>' % (lab, k, E_(v.get(k, '')), extra)
    form = ('<div class="c">%s%s<form method="post">%s%s%s%s%s%s<label>Article</label><textarea name="body" rows="22" style="font-family:ui-monospace,Menlo,monospace;font-size:14px">%s</textarea>'
            '<p class="mu" style="font-size:13px">Blank line = new paragraph · <b>## </b>Heading · <b>### </b>Small heading · <b>- </b>bullet · **bold** · [link text](https://example.com)</p>'
            '<label style="font-weight:400"><input type="checkbox" name="published" value="1" style="width:auto"%s> Published (visible on the website)</label><p><button class="b">Save</button></p></form>%s</div>') % (
        '<p class="err">%s</p>' % E_(err) if err else '', '<p style="color:#166534">Saved.%s</p>' % (' <a href="/blog/%s" target="_blank" rel="noopener">View it</a>' % E_(p.slug) if p and p.published else '') if request.GET.get('saved') else '',
        _hidden(tok), f('title', 'Title', ' required maxlength="150"'), f('slug', 'Web address (leave empty to make one from the title)', ' maxlength="80"'), f('excerpt', 'Short summary (shown on cards and in search results)', ' maxlength="300"'),
        f('tag', 'Tag (e.g. Guides, Product, News)', ' maxlength="30"'), f('author', 'Author', ' maxlength="60"'), E_(v.get('body', '')), ' checked' if p and p.published else '',
        ('<form method="post" style="margin-top:14px">%s<input type="hidden" name="act" value="delete"><button class="b o">Delete this post</button></form>' % _hidden(tok)) if p else '')
    return page('Owner', tabs('blog') + '<p><a href="/owner/blog">← All posts</a></p>' + form, 400 if err else 200, request=request)
