"""Secrets at rest (provider credentials, OAuth tokens). Never returned to the browser, never in settings or backups.

Keys: HUB_ENC_KEYS="key1,key2" (first one encrypts, all decrypt: rotation). Without it a key file is created once in
the data folder (like secret.key). Losing the key means providers and calendars must be reconnected.
"""
import json
import os

from django.conf import settings

_F = None


def _fernet():
    global _F
    if _F is not None:
        return _F
    try:
        from cryptography.fernet import Fernet, MultiFernet
    except ImportError:  # pragma: no cover
        from .base import err
        err('SERVER_CONFIG', 'Secure storage is not set up on the server (install the "cryptography" package).')
    keys = [k.strip() for k in os.environ.get('HUB_ENC_KEYS', '').split(',') if k.strip()]
    if not keys:
        f = settings.DATA_DIR / 'enc.key'
        if not f.exists():
            f.write_text(Fernet.generate_key().decode())
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
        keys = [f.read_text().strip()]
    _F = MultiFernet([Fernet(k.encode()) for k in keys])
    return _F


def enc(obj):
    if obj in (None, '', {}):
        return ''
    return _fernet().encrypt(json.dumps(obj).encode()).decode()


def dec(token, default=None):
    if not token:
        return default
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except Exception:
        return default


def mask(s):
    s = str(s or '')
    return ('••••' + s[-4:]) if len(s) > 4 else ('••••' if s else '')
