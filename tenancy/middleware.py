import time

from django.http import HttpResponse, JsonResponse

from . import ctx
from .models import Tenant

_cache = {}


def _lookup(slug):
    hit = _cache.get(slug)
    if hit and time.time() - hit[0] < 10:
        return hit[1]
    t = Tenant.objects.select_related('plan').filter(slug=slug).first()
    _cache[slug] = (time.time(), t)
    return t


def clear_cache():
    _cache.clear()


def blocked_page(t, why):
    msg = {
        'expired': 'The free trial for this workspace has ended.',
        'suspended': 'This workspace has been paused.',
        'cancelled': 'This workspace has been closed.',
    }.get(why, 'This workspace is not available.')
    return ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Workspace unavailable</title><body style="font-family:system-ui;max-width:520px;margin:12vh auto;padding:0 20px;color:#1b2333">'
            '<h1 style="font-size:24px">%s</h1><p>%s Your data is safe and has not been deleted. '
            'Please contact the owner of the service to continue.</p></body>' % (t.company, msg))


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not ctx.enabled():
            return self.get_response(request)
        host = request.get_host().split(':')[0].lower()
        base = ctx.saas_domain().split(':')[0]
        if host in (base, 'www.' + base):
            request.urlconf = 'config.urls_platform'
            return self.get_response(request)
        if not host.endswith('.' + base):
            return HttpResponse('Unknown address.', status=404)
        slug = host[:-len(base) - 1]
        t = _lookup(slug) if ctx.valid_slug(slug) else None
        if not t:
            return HttpResponse('<h1>No such workspace</h1>', status=404)
        if not t.usable():
            if request.path.startswith('/api/'):
                return JsonResponse({'ok': False, 'error': 'This workspace is not available. Please contact the owner of the service.', 'blocked': t.effective_status()}, status=402)
            return HttpResponse(blocked_page(t, t.effective_status()), status=402)
        tok = ctx.use(ctx.register(slug), ctx.info_for(t))
        try:
            return self.get_response(request)
        finally:
            ctx.reset(tok)
