"""Which company is this request for? Every company has its own database file."""
import contextvars
import re
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import connections

SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$')
RESERVED = {'www', 'app', 'api', 'admin', 'owner', 'mail', 'smtp', 'ftp', 'static', 'billing', 'support', 'help', 'blog', 'status',
            'login', 'signup', 'demo', 'test', 'staging', 'dev', 'platform', 'assertive', 'hub'}

_slug = contextvars.ContextVar('hub_slug', default=None)
_tenant = contextvars.ContextVar('hub_tenant', default=None)


def valid_slug(s):
    return bool(SLUG_RE.match(s or '')) and '--' not in s and s not in RESERVED


def alias():
    s = _slug.get()
    return 't_' + s.replace('-', '_') if s else 'default'


def current_slug():
    return _slug.get()


def tenant():
    return _tenant.get()


def tenant_dir(slug):
    if not SLUG_RE.match(slug):
        raise ValueError('bad slug')
    return Path(settings.DATA_DIR) / 'tenants' / slug


def data_dir():
    s = _slug.get()
    return tenant_dir(s) if s else Path(settings.DATA_DIR)


def register(slug):
    al = 't_' + slug.replace('-', '_')
    if al not in connections.settings:
        d = tenant_dir(slug)
        d.mkdir(parents=True, exist_ok=True)
        conf = connections.configure_settings({'default': settings.DATABASES['default'], al: {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(d / 'hub.sqlite3')}})[al]
        connections.settings[al] = conf
    return al


def use(slug, tenant_obj=None):
    register(slug)
    return (_slug.set(slug), _tenant.set(tenant_obj))


def reset(tokens):
    if tokens:
        _slug.reset(tokens[0])
        _tenant.reset(tokens[1])


class ctx:
    """with tenancy.ctx('acme'): ...  runs code against that company's database."""
    def __init__(self, slug, tenant_obj=None):
        self.slug, self.t = slug, tenant_obj

    def __enter__(self):
        self.tok = use(self.slug, self.t)

    def __exit__(self, *a):
        reset(self.tok)


def provision(slug):
    al = register(slug)
    call_command('migrate', database=al, verbosity=0, interactive=False)
    return al
