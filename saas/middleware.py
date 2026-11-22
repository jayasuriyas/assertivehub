from django.conf import settings
from django.http import HttpResponse

from . import tenancy
from .models import Tenant


def _page(msg, code):
    return HttpResponse('<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><body style="font-family:system-ui;text-align:center;padding:15vh 20px"><h2>%s</h2></body>' % msg, status=code)


class TenantMiddleware:
    """slug.yourdomain.in -> that company's database. yourdomain.in -> the sales site and owner panel."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0].lower()
        base = settings.BASE_DOMAIN
        if host in (base, 'www.' + base):
            request.urlconf = 'saas.urls_platform'
            request.tenant = None
            return self.get_response(request)
        if not host.endswith('.' + base):
            return _page('Unknown address.', 404)
        slug = host[:-len(base) - 1]
        if not tenancy.valid_slug(slug):
            return _page('Unknown address.', 404)
        t = Tenant.objects.filter(slug=slug).first()
        if not t:
            return _page('No company found at this address.', 404)
        request.tenant = t
        request.urlconf = 'saas.urls_tenant'
        tok = tenancy.use(slug, t)
        try:
            return self.get_response(request)
        finally:
            tenancy.reset(tok)
