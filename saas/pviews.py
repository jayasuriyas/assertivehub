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


def page(title, body, code=200, request=None):
    """Older platform pages (sign-up, legal, owner panel) inside the new website layout."""
    from . import site
    return site.render(request, title, body, status=code, noindex=title in ('Owner', 'Sign up'))


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
        return page('Sign up', _form({'plan': request.GET.get('plan', 'business'), 'company': request.GET.get('company', '')[:80]}, '', get_token(request)), request=request)
    v = {k: request.POST.get(k, '').strip() for k in ['company', 'slug', 'owner', 'email', 'phone', 'state', 'user', 'pw', 'plan']}
    v['slug'] = v['slug'].lower()
    v['user'] = v['user'].lower()

    def bad(m):
        return page('Sign up', _form(v, m, get_token(request)), 400, request=request)
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
    return page('Terms', request=request, body=LEGAL % ('''<h2>Terms of Service</h2>
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
    return page('Privacy', request=request, body=LEGAL % '''<h2>Privacy Policy</h2>
<p>This policy follows the Digital Personal Data Protection Act, 2023 (India).</p>
<h3>Who is responsible</h3><p>For employee data you enter, your company decides why and how it is used (the data fiduciary). We process it for you on our servers in India.</p>
<h3>What we collect</h3><p>From the person who signs up: name, email, mobile number, state and GSTIN if given. Inside a workspace: whatever your company enters, such as employee details, attendance, leave, ratings, salary and documents.</p>
<h3>How we use it</h3><p>To run the service, send service emails, bill you and prevent misuse. We do not sell data and do not show ads.</p>
<h3>Payments</h3><p>Card and UPI details are handled by Razorpay. We never see or store them.</p>
<h3>Your rights</h3><p>You can ask to see, correct or delete your data by writing to [grievance officer name and email]. We reply within [30] days.</p>
<h3>Security and retention</h3><p>Each company has a separate database, passwords are stored hashed, daily backups are kept for 30 days. After cancellation data is deleted after [30] days.</p>'''.replace('%', '%%') % ())
