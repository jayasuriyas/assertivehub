"""Which company is this request for? One database file per company, picked from the web address."""
import contextvars
import copy
import re
import threading

from django.conf import settings
from django.db import connections

_alias = contextvars.ContextVar('tenant_alias', default=None)
_info = contextvars.ContextVar('tenant_info', default=None)
_lock = threading.Lock()

RESERVED = {'www', 'app', 'api', 'admin', 'owner', 'static', 'mail', 'email', 'support', 'help', 'billing', 'status',
            'login', 'signup', 'demo', 'test', 'staging', 'cdn', 'ftp', 'smtp', 'blog', 'docs', 'assertive'}
SLUG_RE = re.compile(r'^[a-z0-9](?:[a-z0-9-]{1,28})[a-z0-9]$')


def saas_domain():
    return (getattr(settings, 'SAAS_DOMAIN', '') or '').lower()


def enabled():
    return bool(saas_domain())


def current_alias():
    return _alias.get()


def current():
    return _info.get()


def use(alias, info):
    return (_alias.set(alias), _info.set(info))


def reset(tokens):
    _alias.reset(tokens[0])
    _info.reset(tokens[1])


def tenant_dir(slug):
    return settings.DATA_DIR / 'tenants' / slug


def data_dir():
    i = _info.get()
    return tenant_dir(i['slug']) if i else settings.DATA_DIR


def valid_slug(slug):
    return bool(SLUG_RE.match(slug or '')) and slug not in RESERVED


def register(slug):
    """Make the company's database known to Django (safe to call many times)."""
    alias = 't_' + slug
    if alias not in connections.databases:
        with _lock:
            if alias not in connections.databases:
                d = copy.deepcopy(connections['default'].settings_dict)
                tenant_dir(slug).mkdir(parents=True, exist_ok=True)
                d['NAME'] = str(tenant_dir(slug) / 'hub.sqlite3')
                connections.databases[alias] = d
    return alias


def info_for(t):
    p = t.plan
    return {
        'slug': t.slug, 'company': t.company, 'plan': p.name, 'plan_key': p.key,
        'modules': list(p.modules), 'max_employees': p.max_employees,
        'status': t.effective_status(), 'days_left': t.days_left(),
        'trial_ends': t.trial_ends.isoformat() if t.trial_ends else '',
    }


class use_tenant:
    """with use_tenant(tenant): ... run code against that company's database"""
    def __init__(self, t):
        self.t = t

    def __enter__(self):
        self.tok = use(register(self.t.slug), info_for(self.t))
        return self

    def __exit__(self, *a):
        reset(self.tok)


class TenantRouter:
    def _db(self, model):
        if model._meta.app_label == 'tenancy':
            return 'default'
        return _alias.get() or 'default'

    def db_for_read(self, model, **hints):
        return self._db(model)

    def db_for_write(self, model, **hints):
        return self._db(model)

    def allow_relation(self, a, b, **hints):
        return True

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == 'tenancy':
            return db == 'default'
        return True
