"""Structured audit trail for v6 entities (who, what, before/after). The legacy Config['audit'] list stays as it is."""
from core.models import AuditEvent

_SKIP = {'secrets', 'secrets_enc', 'refresh_token_enc', 'access_token_enc', 'verifier_enc', 'code_hash'}


def _clean(d):
    if not isinstance(d, dict):
        return d
    return {k: v for k, v in d.items() if k not in _SKIP}


def diff(before, after):
    b, a = before or {}, after or {}
    keys = set(b) | set(a)
    return ({k: b.get(k) for k in keys if b.get(k) != a.get(k)}, {k: a.get(k) for k in keys if b.get(k) != a.get(k)})


def record(u, action, entity_type, entity_id='', before=None, after=None, branch_id=None, request=None):
    if before is not None and after is not None:
        before, after = diff(before, after)
    ip = ''
    if request is not None:
        from core.views import client_ip
        ip = client_ip(request)
    AuditEvent.objects.create(actor=u if getattr(u, 'pk', None) else None, actor_name=getattr(u, 'username', '') or 'system', action=action[:40],
                              entity_type=entity_type[:30], entity_id=str(entity_id)[:60], branch_id=int(branch_id) if str(branch_id or '').isdigit() else None,
                              before=_clean(before), after=_clean(after), ip=ip)
