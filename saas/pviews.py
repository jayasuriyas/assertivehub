import datetime
import html
import secrets
import re
import time

from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.middleware.csrf import get_token

from . import plans, tenancy, mail
from .models import Tenant, SignupAttempt

E_ = html.escape
CSS = """<style>
:root{--bg:#f6f7fb;--card:#fff;--tx:#161a2b;--mu:#667;--ac:#4f46e5;--bd:#e3e6f0}
@media(prefers-color-scheme:dark){:root{--bg:#0f1220;--card:#181c30;--tx:#eef0fa;--mu:#9aa1c0;--ac:#8b85ff;--bd:#2a3050}}
*{box-sizing:border-box}body{margin:0;font:16px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--tx)}
.w{max-width:1040px;margin:0 auto;padding:0 18px}header{display:flex;justify-content:space-between;align-items:center;padding:18px 0}
a{color:var(--ac)}.b{display:inline-block;background:var(--ac);color:#fff;text-decoration:none;padding:11px 20px;border-radius:10px;font-weight:600;border:0;cursor:pointer;font-size:16px}
.b.o{background:transparent;color:var(--ac);border:1.5px solid var(--ac)}h1{font-size:clamp(28px,5vw,46px);line-height:1.15;margin:.4em 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}.c{background:var(--card);border:1px solid var(--bd);border-radius:16px;padding:22px}
.pr{font-size:32px;font-weight:700}.mu{color:var(--mu)}ul.t{padding-left:18px;margin:10px 0}.hero{padding:30px 0 20px;max-width:720px}
label{display:block;font-weight:600;margin:14px 0 4px;font-size:14px}input,select{width:100%;padding:11px 12px;border-radius:9px;border:1px solid var(--bd);background:var(--card);color:var(--tx);font-size:16px}
.err{background:#fee;color:#a11;padding:10px 12px;border-radius:9px;margin:10px 0}footer{padding:30px 0;color:var(--mu);font-size:14px}.tag{font-size:12px;background:var(--ac);color:#fff;border-radius:99px;padding:2px 10px;margin-left:6px}
</style>"""


def page(title, body, code=200):
    h = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>%s</title>%s<body><div class="w"><header><a href="/" style="text-decoration:none;color:var(--tx);font-weight:800;font-size:20px">%s</a><span><a href="/owner/" class="mu" style="font-size:13px;margin-right:14px">Owner</a><a class="b" href="/signup">Start free trial</a></span></header>%s<footer>Made in India. Prices in INR, GST extra. <a href="/terms">Terms</a> · <a href="/privacy">Privacy</a></footer></div></body></html>' % (E_(title), CSS, E_(plans.PRODUCT), body)
    return HttpResponse(h, status=code)


def home(request):
    cards = ''
    for k, p in plans.PLANS.items():
        feats = ''.join('<li>%s</li>' % x for x in ['Up to %d employees' % p['emp'], p['blurb']])
        cards += '<div class="c"><h3>%s%s</h3><div class="pr">₹%s<span class="mu" style="font-size:15px;font-weight:400"> /month</span></div><div class="mu">+ 18%% GST · ₹%s/year</div><ul class="t">%s</ul><a class="b o" href="/signup?plan=%s">Try %s free</a></div>' % (
            p['name'], '<span class="tag">Popular</span>' if k == 'business' else '', format(p['month'], ','), format(p['month'] * plans.YEAR_MONTHS_PAID, ','), feats, k, p['name'])
    body = '''<div class="hero"><h1>HR and work management for creative agencies, in one place</h1>
<p class="mu" style="font-size:18px">Attendance, leave, tasks, clients, monthly team ratings, reports, payroll and more. Every company gets its own private space at <b>yourname.%s</b>. Works on phone and desktop.</p>
<p><a class="b" href="/signup">Start %d-day free trial</a> <span class="mu" style="margin-left:10px">No card needed</span></p></div>
<div class="grid">%s</div>
<div class="grid" style="margin-top:22px"><div class="c"><b>Private by design</b><p class="mu">Your company data lives in its own separate database. Data stays on servers in India.</p></div>
<div class="c"><b>Pay in rupees</b><p class="mu">UPI, cards and netbanking through Razorpay. GST invoice for every payment.</p></div>
<div class="c"><b>Switch modules on or off</b><p class="mu">Show your team only what they need.</p></div></div>''' % (E_(settings.BASE_DOMAIN), plans.TRIAL_DAYS, cards)
    return page(plans.PRODUCT, body)


def _base_url(request, slug):
    scheme = 'https' if (request.is_secure() or settings.CSRF_COOKIE_SECURE) else 'http'
    port = request.get_host().partition(':')[2]
    return '%s://%s.%s%s' % (scheme, slug, settings.BASE_DOMAIN, ':' + port if port else '')


def check(request):
    s = request.GET.get('slug', '').strip().lower()
    if not tenancy.valid_slug(s):
        return JsonResponse({'ok': False, 'msg': 'Use 3-30 letters, numbers or hyphens.'})
    if Tenant.objects.filter(slug=s).exists():
        return JsonResponse({'ok': False, 'msg': 'Already taken.'})
    return JsonResponse({'ok': True, 'msg': 'Available'})


FIELDS = [('company', 'Company name', 'text'), ('slug', 'Web address', 'slug'), ('owner', 'Your name', 'text'), ('email', 'Email', 'email'),
          ('phone', 'Mobile number', 'tel'), ('state', 'State', 'state'), ('user', 'Admin username (you will sign in with this)', 'text'), ('pw', 'Password (8+ characters)', 'password')]


def _form(v, err='', tok=''):
    rows = ''
    for k, lab, typ in FIELDS:
        val = E_(v.get(k, ''))
        if typ == 'state':
            opts = ''.join('<option%s>%s</option>' % (' selected' if x == v.get(k) else '', x) for x in plans.STATES)
            inp = '<select name="%s" required><option value="">Choose…</option>%s</select>' % (k, opts)
        elif typ == 'slug':
            inp = '<input name="slug" value="%s" required pattern="[a-z0-9-]{3,30}" autocapitalize="none"><div class="mu" style="font-size:13px">%s</div>' % (val, 'yourname.' + E_(settings.BASE_DOMAIN))
        else:
            inp = '<input name="%s" type="%s" value="%s" required%s>' % (k, typ, val if typ != 'password' else '', ' minlength=8' if typ == 'password' else '')
        rows += '<label>%s</label>%s' % (lab, inp)
    plan_in = '<input type="hidden" name="plan" value="%s"><input type="hidden" name="csrfmiddlewaretoken" value="%s">' % (E_(v.get('plan', 'business')), tok)
    terms = '<label style="font-weight:400;margin-top:16px"><input type="checkbox" name="terms" required style="width:auto"> I agree to the <a href="/terms" target="_blank">Terms</a> and <a href="/privacy" target="_blank">Privacy Policy</a></label>'
    return '<div style="max-width:520px;margin:0 auto"><div class="c"><h2 style="margin-top:0">Start your %d-day free trial</h2>%s<form method="post">%s%s%s<p><button class="b" style="width:100%%">Create my workspace</button></p></form></div></div>' % (
        plans.TRIAL_DAYS, '<div class="err">%s</div>' % E_(err) if err else '', rows, plan_in, terms)


def _ip(request):
    from core.views import client_ip
    return client_ip(request)


def signup(request):
    if request.method != 'POST':
        return page('Sign up', _form({'plan': request.GET.get('plan', 'business')}, '', get_token(request)))
    v = {k: request.POST.get(k, '').strip() for k in ['company', 'slug', 'owner', 'email', 'phone', 'state', 'user', 'pw', 'plan']}
    v['slug'] = v['slug'].lower()
    v['user'] = v['user'].lower()

    def bad(m):
        return page('Sign up', _form(v, m, get_token(request)), 400)
    ip = _ip(request)
    SignupAttempt.objects.filter(at__lt=time.time() - 3600).delete()
    if SignupAttempt.objects.filter(ip=ip).count() >= 5:
        return bad('Too many sign-ups from this network. Please try again in an hour.')
    if not request.POST.get('terms'):
        return bad('Please accept the terms.')
    if not v['company'] or len(v['company']) > 80 or not v['owner']:
        return bad('Please enter company and your name.')
    if not tenancy.valid_slug(v['slug']):
        return bad('Web address: 3-30 letters, numbers or hyphens (not a reserved word).')
    if Tenant.objects.filter(slug=v['slug']).exists():
        return bad('That web address is taken. Try another.')
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', v['email']):
        return bad('Enter a valid email.')
    if not re.match(r'^[6-9]\d{9}$', re.sub(r'^(\+91|0)', '', re.sub(r'[\s-]', '', v['phone']))):
        return bad('Enter a valid 10-digit Indian mobile number.')
    if v['state'] not in plans.STATES:
        return bad('Choose your state.')
    if not re.match(r'^[a-z0-9._-]{3,30}$', v['user']):
        return bad('Username: 3-30 letters, numbers, dot, dash or underscore.')
    if len(v['pw']) < 8:
        return bad('Password must be at least 8 characters.')
    plan = v['plan'] if v['plan'] in plans.PLANS else 'business'
    SignupAttempt.objects.create(ip=ip, at=time.time())
    phone = re.sub(r'^(\+91|0)', '', re.sub(r'[\s-]', '', v['phone']))
    t = Tenant.objects.create(slug=v['slug'], name=v['company'], owner_name=v['owner'], owner_email=v['email'], owner_phone=phone, state=v['state'],
                              plan=plan, trial_ends=datetime.date.today() + datetime.timedelta(days=plans.TRIAL_DAYS))
    try:
        uid = provision_company(t, v['user'], v['pw'])
    except Exception as e:  # do not leave a half-made company behind
        t.delete()
        return bad('Could not create the workspace (%s). Please try again.' % type(e).__name__)
    tok = signing.dumps({'slug': t.slug, 'uid': uid, 'n': secrets.token_hex(12)}, salt='hub-enter')
    mail.send(t.owner_email, 'Your %s workspace is ready' % plans.PRODUCT, 'Hi %s,\n\nYour workspace %s is ready.\nAddress: %s\nUsername: %s\nTrial ends: %s\n\nThank you.' % (
        t.owner_name, t.name, _base_url(request, t.slug), v['user'], t.trial_ends.strftime('%d %b %Y')))
    return HttpResponseRedirect(_base_url(request, t.slug) + '/_enter?t=' + tok)


def provision_company(t, username, password):
    from core import views as cv
    from core.models import Profile
    tenancy.provision(t.slug)
    with tenancy.ctx(t.slug, t):
        al = tenancy.alias()
        with transaction.atomic(using=al):
            u = User.objects.create_user(username, password=password)
            Profile.objects.create(user=u, role='superadmin')
            cv.cset('company', t.name)
            cv.cset('settings', {})
            cv.cset('rev', 1)
            return u.pk


LEGAL = """<div class="c" style="max-width:760px;margin:0 auto"><div class="err" style="background:#fff4d6;color:#6b4e00"><b>Draft.</b> This is a plain-language starting point, not legal advice. Have a lawyer review and replace the bracketed items before you launch.</div>%s</div>"""


def terms(request):
    n = E_(plans.PRODUCT)
    return page('Terms', LEGAL % ('''<h2>Terms of Service</h2>
<p>These terms are between [Your registered company name], [address], India ("we") and the business that creates a workspace ("you").</p>
<h3>1. The service</h3><p>%s is a web application for HR and work management. It is provided to businesses, not consumers.</p>
<h3>2. Trial and payment</h3><p>New workspaces get a %d-day free trial. After that a paid plan is needed. Prices are in Indian rupees and GST at 18%% is added. Payments are made through Razorpay. We issue a GST invoice for every payment.</p>
<h3>3. Renewal and expiry</h3><p>Plans do not renew automatically. When a plan ends, the workspace becomes read-only. You can still sign in and export your data. It becomes editable again once you renew.</p>
<h3>4. Refunds</h3><p>[Describe your refund policy, e.g. no refunds after the first 7 days of a paid plan.]</p>
<h3>5. Your data</h3><p>You own the data you put in. You are responsible for having the right to store your employees' information. You may export it at any time. After cancellation we keep your data for [30] days and then delete it.</p>
<h3>6. Acceptable use</h3><p>No unlawful use, no attempts to break into other workspaces, no abuse of the service.</p>
<h3>7. Liability</h3><p>The service is provided as is. Our total liability is limited to the fees you paid in the last 3 months.</p>
<h3>8. Law and courts</h3><p>Indian law applies. Courts at [your city] have jurisdiction.</p>
<p>Contact: [support email]</p>''' % (n, plans.TRIAL_DAYS)))


def privacy(request):
    return page('Privacy', LEGAL % '''<h2>Privacy Policy</h2>
<p>This policy follows the Digital Personal Data Protection Act, 2023 (India).</p>
<h3>Who is responsible</h3><p>For employee data you enter, your company decides why and how it is used (the data fiduciary). We process it for you on our servers in India.</p>
<h3>What we collect</h3><p>From the person who signs up: name, email, mobile number, state and GSTIN if given. Inside a workspace: whatever your company enters, such as employee details, attendance, leave, ratings, salary and documents.</p>
<h3>How we use it</h3><p>To run the service, send service emails, bill you and prevent misuse. We do not sell data and do not show ads.</p>
<h3>Payments</h3><p>Card and UPI details are handled by Razorpay. We never see or store them.</p>
<h3>Your rights</h3><p>You can ask to see, correct or delete your data by writing to [grievance officer name and email]. We reply within [30] days.</p>
<h3>Security and retention</h3><p>Each company has a separate database, passwords are stored hashed, daily backups are kept for 30 days. After cancellation data is deleted after [30] days.</p>'''.replace('%', '%%') % ())
