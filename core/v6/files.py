"""Attachments for tasks and meetings. Files live on disk inside the company's data folder (not base64 in JSON),
are checked by content (not only the name) and are only served to people who may see the task or meeting."""
import base64
import hashlib
import re
import uuid
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse

from core import views as V
from core.models import Attachment, Record, Meeting
from .base import action, err, invalid, now, iso

MAX_BYTES = 10 * 1024 * 1024
MAX_PER_ITEM = 10
TYPES = {  # extension: (mime, magic check)
    'pdf': ('application/pdf', lambda b: b[:5] == b'%PDF-'),
    'png': ('image/png', lambda b: b[:8] == b'\x89PNG\r\n\x1a\n'),
    'jpg': ('image/jpeg', lambda b: b[:3] == b'\xff\xd8\xff'), 'jpeg': ('image/jpeg', lambda b: b[:3] == b'\xff\xd8\xff'),
    'webp': ('image/webp', lambda b: b[:4] == b'RIFF' and b[8:12] == b'WEBP'),
    'docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', lambda b: b[:4] == b'PK\x03\x04'),
    'xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', lambda b: b[:4] == b'PK\x03\x04'),
    'pptx': ('application/vnd.openxmlformats-officedocument.presentationml.presentation', lambda b: b[:4] == b'PK\x03\x04'),
    'zip': ('application/zip', lambda b: b[:4] == b'PK\x03\x04'),
    'doc': ('application/msword', lambda b: b[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'),
    'xls': ('application/vnd.ms-excel', lambda b: b[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'),
    'ppt': ('application/vnd.ms-powerpoint', lambda b: b[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'),
    'txt': ('text/plain', None), 'csv': ('text/csv', None),
}
_HTMLISH = re.compile(rb'<\s*(html|script|svg|iframe|body|!doctype)', re.I)


def base_dir():
    if settings.SAAS:
        from saas import tenancy
        return tenancy.data_dir() / 'files'
    return Path(settings.DATA_DIR) / 'files'


def check_content(ext, raw):
    if ext not in TYPES:
        return False
    chk = TYPES[ext][1]
    if chk:
        return chk(raw)
    try:
        raw[:65536].decode('utf-8')
    except UnicodeDecodeError:
        return False
    return not _HTMLISH.search(raw[:65536])


def _owner_access(u, otype, oid, write=False):
    from .scope import scope_for
    if otype == 'task':
        r = Record.objects.filter(coll='tasks', rid=oid).first()
        if not r:
            return False
        s = scope_for(u)
        if not s.sees(r.data.get('eid', '')):
            return False
        if write:
            return V.rank_of(u) >= 2 or r.data.get('eid') == s.me
        return True
    if otype == 'meeting':
        from .meetings import can_view, can_manage
        m = Meeting.objects.filter(pk=int(oid) if str(oid).isdigit() else 0).prefetch_related('participants').first()
        if not m:
            return False
        return can_manage(u, m) if write else can_view(u, m) == 'full'
    return False


def adict(a):
    return {'id': str(a.pk), 'name': a.file_name, 'size': a.size, 'mime': a.mime, 'at': iso(a.uploaded_at), 'by': a.uploaded_by.username if a.uploaded_by_id else '',
            'url': 'files/%s' % a.pk}


@action('att.upload')
def a_upload(request, u, b):
    ot, oid = str(b.get('ownerType') or ''), str(b.get('ownerId') or '')
    if ot not in ('task', 'meeting') or not _owner_access(u, ot, oid, write=True):
        err('NOT_FOUND', 'Not found.')
    name = re.sub(r'[\\/\x00-\x1f]', '', str(b.get('name') or '').strip())[-200:]
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if ext not in TYPES:
        invalid(file='Allowed files: PDF, Word, Excel, PowerPoint, pictures (PNG, JPG, WebP), text, CSV and ZIP.')
    m = re.match(r'^data:[^;,]*;base64,([A-Za-z0-9+/=]+)$', str(b.get('data') or ''))
    if not m:
        invalid(file='The file could not be read.')
    raw = base64.b64decode(m.group(1))
    if len(raw) > MAX_BYTES:
        invalid(file='Files can be at most 10 MB.')
    if not check_content(ext, raw):
        invalid(file='This file does not look like a real .%s file.' % ext)
    if Attachment.objects.filter(owner_type=ot, owner_id=oid, deleted_at__isnull=True).count() >= MAX_PER_ITEM:
        invalid(file='At most %d files per item.' % MAX_PER_ITEM)
    rel = '%s/%s.bin' % (now().strftime('%Y/%m'), uuid.uuid4().hex)
    f = base_dir() / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(raw)
    a = Attachment.objects.create(owner_type=ot, owner_id=oid, file_name=name, mime=TYPES[ext][0], size=len(raw), storage_path=rel,
                                  sha256=hashlib.sha256(raw).hexdigest(), uploaded_by=u)
    if ot == 'task':
        r = Record.objects.get(coll='tasks', rid=oid)
        r.data = dict(r.data, att=Attachment.objects.filter(owner_type='task', owner_id=oid, deleted_at__isnull=True).count())
        r.save(update_fields=['data'])
    return {'attachment': adict(a)}


@action('att.list', read=True)
def a_list(request, u, b):
    ot, oid = str(b.get('ownerType') or ''), str(b.get('ownerId') or '')
    if not _owner_access(u, ot, oid):
        err('NOT_FOUND', 'Not found.')
    return {'items': [adict(a) for a in Attachment.objects.filter(owner_type=ot, owner_id=oid, deleted_at__isnull=True).select_related('uploaded_by')]}


@action('att.delete')
def a_delete(request, u, b):
    a = Attachment.objects.filter(pk=int(b.get('id')) if str(b.get('id') or '').isdigit() else 0, deleted_at__isnull=True).first()
    if not a or not _owner_access(u, a.owner_type, a.owner_id, write=True):
        err('NOT_FOUND', 'Not found.')
    a.deleted_at = now()
    a.save(update_fields=['deleted_at'])
    if a.owner_type == 'task':
        r = Record.objects.filter(coll='tasks', rid=a.owner_id).first()
        if r:
            r.data = dict(r.data, att=Attachment.objects.filter(owner_type='task', owner_id=a.owner_id, deleted_at__isnull=True).count())
            r.save(update_fields=['data'])
    return {'ok': True}


def download(request, aid):
    u = request.user
    if not u.is_authenticated or not u.is_active:
        return HttpResponse('Please sign in.', status=401)
    a = Attachment.objects.filter(pk=aid, deleted_at__isnull=True).first()
    if not a or not _owner_access(u, a.owner_type, a.owner_id):
        return HttpResponse('Not found', status=404)
    base = base_dir().resolve()
    f = (base / a.storage_path).resolve()
    if base not in f.parents or not f.is_file():
        return HttpResponse('Not found', status=404)
    r = FileResponse(open(f, 'rb'), as_attachment=True, filename=a.file_name, content_type=a.mime)
    r['X-Content-Type-Options'] = 'nosniff'
    r['Cache-Control'] = 'private, no-store'
    return r
