"""Razorpay payments and GST invoices. Plans are bought for a month or a year; renewals are manual (the person pays again)."""
import base64
import calendar
import datetime
import hashlib
import hmac
import html
import json
import os
import time
import urllib.request
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import plans, state, mail
from .models import Payment, Tenant

E_ = html.escape


def env(k, d=''):
    return os.environ.get(k, d)


def demo_mode():
    return env('HUB_DEMO_PAY') == '1' and not env('RAZORPAY_KEY_ID')


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return datetime.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def fin_year(d):
    y = d.year if d.month >= 4 else d.year - 1
    return '%02d%02d' % (y % 100, (y + 1) % 100)


def next_invoice_no(d):
    pre = 'AI/%s/' % fin_year(d)
    n = Payment.objects.filter(invoice_no__startswith=pre).count() + 1
    return '%s%04d' % (pre, n)


def mark_paid(p, payment_id):
    """Idempotent: safe to call from both the browser verify and the webhook."""
    with transaction.atomic():
        p = Payment.objects.select_related('tenant').get(pk=p.pk)
        if p.status == 'paid':
            return p
        d = datetime.date.today()
        t = p.tenant
        start = max(d, t.paid_until) if t.paid_until else d
        if t.paid_until and t.paid_until > d and p.plan != t.plan:
            # changing plan: unused days of the old plan become days of the new plan at their value, they are not stacked
            left = (t.paid_until - d).days
            old_rate, new_rate = plans.PLANS.get(t.plan, plans.PLANS['starter'])['month'], plans.PLANS[p.plan]['month']
            start = d + datetime.timedelta(days=int(left * old_rate / new_rate))
        end = add_months(start, 12 if p.period == 'year' else 1)
        for _ in range(5):
            try:
                with transaction.atomic():
                    p.invoice_no = next_invoice_no(d)
                    p.status, p.payment_id, p.paid_at, p.covers_to = 'paid', payment_id, datetime.datetime.now(datetime.timezone.utc), end
                    p.save()
                break
            except IntegrityError:
                p = Payment.objects.get(pk=p.pk)
                if p.status == 'paid':
                    return p
        t.plan, t.paid_until, t.last_notice = p.plan, end, ''
        t.save()
    mail.send(t.owner_email, 'Payment received - invoice %s' % p.invoice_no, 'Thank you. We received Rs %.2f for the %s plan.\nInvoice %s. Active until %s.\nDownload it from Settings > Plan and billing.' % (
        p.total / 100, plans.PLANS[p.plan]['name'], p.invoice_no, end.strftime('%d %b %Y')))
    return p


def _admin(request):
    from core.views import rank_of
    u = request.user
    return u.is_authenticated and u.is_active and rank_of(u) >= 4


def _guard(request):
    if request.headers.get('X-Requested-With') != 'staffroom':
        return JsonResponse({'ok': False, 'error': 'Bad request.'}, status=400)
    if not _admin(request):
        return JsonResponse({'ok': False, 'error': 'Only the super admin can manage billing.'}, status=403)
    return None


def _body(request):
    try:
        return json.loads(request.body or b'{}')
    except Exception:
        return {}


def _pay_view(p):
    return {'order': p.order_id, 'invoice': p.invoice_no or '', 'plan': plans.PLANS.get(p.plan, {}).get('name', p.plan), 'period': p.period,
            'total': p.total / 100, 'status': p.status, 'date': p.paid_at.strftime('%Y-%m-%d') if p.paid_at else p.created.strftime('%Y-%m-%d'),
            'to': p.covers_to.isoformat() if p.covers_to else ''}


def state_api(request):
    g = _guard(request)
    if g:
        return g
    t = request.tenant
    pl = []
    for k, p in plans.PLANS.items():
        pl.append({'id': k, 'name': p['name'], 'emp': p['emp'], 'blurb': p['blurb'], 'month': plans.price(k, 'month')[2] / 100, 'year': plans.price(k, 'year')[2] / 100,
                   'monthBase': p['month'], 'yearBase': p['month'] * plans.YEAR_MONTHS_PAID})
    return JsonResponse({'ok': True, 'info': state.info(t), 'plans': pl, 'gst': plans.GST_RATE, 'demo': demo_mode(), 'live': bool(env('RAZORPAY_KEY_ID')),
                         'bill': {'name': t.name, 'gstin': t.gstin, 'state': t.state}, 'states': plans.STATES,
                         'payments': [_pay_view(p) for p in t.payments.filter(status='paid').order_by('-id')[:30]]})


def _rzp(path, data):
    kid, sec = env('RAZORPAY_KEY_ID'), env('RAZORPAY_KEY_SECRET')
    req = urllib.request.Request('https://api.razorpay.com/v1/' + path, json.dumps(data).encode(), {
        'Content-Type': 'application/json', 'Authorization': 'Basic ' + base64.b64encode(('%s:%s' % (kid, sec)).encode()).decode()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


GSTIN_CHARS = set('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ')


@csrf_exempt
def order_api(request):
    g = _guard(request)
    if g:
        return g
    b = _body(request)
    plan, period = b.get('plan'), b.get('period')
    if plan not in plans.PLANS or period not in ('month', 'year'):
        return JsonResponse({'ok': False, 'error': 'Choose a plan and period.'}, status=400)
    gstin = str(b.get('gstin', '')).strip().upper()
    if gstin and (len(gstin) != 15 or not set(gstin) <= GSTIN_CHARS):
        return JsonResponse({'ok': False, 'error': 'GSTIN must be 15 letters/numbers.'}, status=400)
    st = str(b.get('state', '')).strip()
    if st not in plans.STATES:
        return JsonResponse({'ok': False, 'error': 'Choose the state for the invoice.'}, status=400)
    name = str(b.get('name', '')).strip()[:80] or request.tenant.name
    t = request.tenant
    info = state.info(t)
    base, gst, total = plans.price(plan, period)
    cg, sg, ig = plans.gst_split(base, st)
    if info['state'] == 'active' and plans.PLANS[plan]['emp'] < plans.PLANS[info['paidPlan']]['emp']:
        pass  # downgrades allowed; the employee limit is enforced when adding people
    if not demo_mode() and not env('RAZORPAY_KEY_ID'):
        return JsonResponse({'ok': False, 'error': 'Online payment is not set up yet. Please contact support.'}, status=503)
    ref = 'ah%s' % uuid.uuid4().hex[:20]
    try:
        order_id = 'demo_' + ref if demo_mode() else _rzp('orders', {'amount': total, 'currency': 'INR', 'receipt': ref, 'notes': {'tenant': t.slug, 'plan': plan, 'period': period}})['id']
    except Exception:
        return JsonResponse({'ok': False, 'error': 'Could not reach the payment service. Please try again.'}, status=502)
    t.gstin, t.state = gstin, st
    t.save(update_fields=['gstin', 'state'])
    Payment.objects.create(tenant=t, order_id=order_id, plan=plan, period=period, base=base, cgst=cg, sgst=sg, igst=ig, total=total,
                           bill_name=name, bill_gstin=gstin, bill_state=st, method='demo' if demo_mode() else 'razorpay')
    return JsonResponse({'ok': True, 'order': order_id, 'amount': total, 'key': env('RAZORPAY_KEY_ID'), 'demo': demo_mode(), 'name': plans.PRODUCT,
                         'email': t.owner_email, 'phone': t.owner_phone, 'desc': '%s plan, 1 %s' % (plans.PLANS[plan]['name'], period)})


@csrf_exempt
def verify_api(request):
    g = _guard(request)
    if g:
        return g
    b = _body(request)
    oid = str(b.get('order', ''))
    p = Payment.objects.filter(order_id=oid, tenant=request.tenant).first()
    if not p:
        return JsonResponse({'ok': False, 'error': 'Unknown order.'}, status=404)
    if p.method == 'demo':
        if not demo_mode():
            return JsonResponse({'ok': False, 'error': 'Demo payment is switched off.'}, status=403)
        pid = 'pay_demo_' + uuid.uuid4().hex[:12]
    else:
        pid, sig = str(b.get('payment', '')), str(b.get('signature', ''))
        want = hmac.new(env('RAZORPAY_KEY_SECRET').encode(), ('%s|%s' % (oid, pid)).encode(), hashlib.sha256).hexdigest()
        if not pid or not hmac.compare_digest(want, sig):
            return JsonResponse({'ok': False, 'error': 'Payment could not be verified.'}, status=400)
    mark_paid(p, pid)
    return JsonResponse({'ok': True, 'info': state.info(Tenant.objects.get(pk=request.tenant.pk))})


@csrf_exempt
def webhook(request):
    """Razorpay calls this when a payment is captured, even if the buyer closed the browser."""
    sec = env('RAZORPAY_WEBHOOK_SECRET')
    if not sec:
        return HttpResponse('off', status=503)
    want = hmac.new(sec.encode(), request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, request.headers.get('X-Razorpay-Signature', '')):
        return HttpResponse('bad signature', status=400)
    try:
        d = json.loads(request.body)
        if d.get('event') in ('payment.captured', 'order.paid'):
            ent = d['payload']['payment']['entity']
            p = Payment.objects.filter(order_id=ent.get('order_id')).first()
            if p and p.total == ent.get('amount'):
                mark_paid(p, ent['id'])
    except Exception as e:
        print('webhook error', e)
        return HttpResponse('error', status=500)
    return HttpResponse('ok')


def rupees(n):
    return '{:,.2f}'.format(n / 100)


def invoice_page(request, no):
    r = _invoice_page(request, no)
    r['Content-Security-Policy'] = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    return r


def _invoice_page(request, no):
    if not _admin(request):
        return HttpResponse('Sign in as super admin.', status=403)
    p = Payment.objects.filter(tenant=request.tenant, invoice_no=no.replace('_', '/'), status='paid').first()
    if not p:
        return HttpResponse('Not found', status=404)
    seller = {'name': env('HUB_SELLER_NAME', '[Your registered business name]'), 'gstin': env('HUB_SELLER_GSTIN', '[Your GSTIN]'), 'addr': env('HUB_SELLER_ADDRESS', '[Your address]'),
              'state': env('HUB_SELLER_STATE', '[Your state]'), 'sac': env('HUB_SELLER_SAC', '998431')}
    per = 'year' if p.period == 'year' else 'month'
    d = p.paid_at.astimezone().strftime('%d %b %Y')
    tax = ''
    if p.igst:
        tax = '<tr><td>IGST @ %d%%</td><td>%s</td></tr>' % (plans.GST_RATE, rupees(p.igst))
    else:
        tax = '<tr><td>CGST @ %s%%</td><td>%s</td></tr><tr><td>SGST @ %s%%</td><td>%s</td></tr>' % (plans.GST_RATE / 2, rupees(p.cgst), plans.GST_RATE / 2, rupees(p.sgst))
    h = '''<!doctype html><meta charset=utf-8><title>Invoice %s</title><meta name=viewport content="width=device-width,initial-scale=1"><style>body{font:14px/1.5 system-ui,sans-serif;max-width:720px;margin:20px auto;padding:0 16px;color:#111}table{width:100%%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}td:last-child,th:last-child{text-align:right}.r{display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}.b{margin:20px 0;padding:10px 16px;border:1px solid #333;background:#fff;border-radius:8px;cursor:pointer}@media print{.b{display:none}}</style>
<button class=b onclick=print()>Print / Save as PDF</button><h2>TAX INVOICE</h2>
<div class=r><div><b>%s</b><br>%s<br>GSTIN: %s<br>State: %s</div><div>Invoice no: <b>%s</b><br>Date: %s<br>Order: %s</div></div><hr>
<div><b>Billed to</b><br>%s<br>%s<br>GSTIN: %s</div><br>
<table><tr><th>Description (SAC %s)</th><th>Amount (Rs)</th></tr><tr><td>%s plan, 1 %s of software subscription<br><small>Valid to %s</small></td><td>%s</td></tr>%s<tr><td><b>Total</b></td><td><b>%s</b></td></tr></table>
<p><small>Place of supply: %s. This is a computer generated invoice.</small></p>''' % (
        E_(p.invoice_no), E_(seller['name']), E_(seller['addr']), E_(seller['gstin']), E_(seller['state']), E_(p.invoice_no), d, E_(p.order_id),
        E_(p.bill_name), E_(request.tenant.name), E_(p.bill_gstin or 'Unregistered'), E_(seller['sac']), E_(plans.PLANS[p.plan]['name']), per,
        p.covers_to.strftime('%d %b %Y') if p.covers_to else '', rupees(p.base), tax, rupees(p.total), E_(p.bill_state))
    return HttpResponse(h)
