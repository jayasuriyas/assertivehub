from django.conf import settings
from django.db import models


class Profile(models.Model):
    """Role and employee link for each login."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=12, default='employee')  # superadmin, admin, manager, employee
    emp_id = models.CharField(max_length=60, blank=True, default='')  # id of the linked employee record
    prefs = models.JSONField(default=dict, blank=True)  # theme and light/dark choice


class Record(models.Model):
    """One employee, task, client, review, event, note and so on."""
    coll = models.CharField(max_length=20, db_index=True)
    rid = models.CharField(max_length=60)
    data = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['coll', 'rid'], name='uniq_record')]
        ordering = ['id']


class Attendance(models.Model):
    day = models.CharField(max_length=10, db_index=True)
    eid = models.CharField(max_length=60)
    data = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['day', 'eid'], name='uniq_att')]


class Config(models.Model):
    """company name, shared settings and the data revision number."""
    key = models.CharField(max_length=30, unique=True)
    value = models.JSONField(default=dict)


class LoginFail(models.Model):
    name = models.CharField(max_length=150, db_index=True)
    at = models.FloatField()


# =====================================================================================
# v6 "Connected Operations": branches, meetings, task time tracking, notifications.
# Relational tables (not Record JSON) because they need constraints, range queries and
# SQL reports. They are never part of the `load` payload. See docs/release-v6/06-data-model.md
# =====================================================================================
import uuid as _uuid

from django.db.models import Q as _Q

_U = settings.AUTH_USER_MODEL


class Branch(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=80)
    address1 = models.CharField(max_length=120, blank=True, default='')
    address2 = models.CharField(max_length=120, blank=True, default='')
    city = models.CharField(max_length=60, blank=True, default='')
    state = models.CharField(max_length=60, blank=True, default='')
    country = models.CharField(max_length=2, default='IN')
    postal_code = models.CharField(max_length=10, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    email = models.CharField(max_length=120, blank=True, default='')
    manager_emp = models.CharField(max_length=60, blank=True, default='', db_index=True)
    timezone = models.CharField(max_length=40, default='Asia/Kolkata')
    work_start = models.CharField(max_length=5, default='09:00')  # HH:MM, same format as settings.workStart
    work_end = models.CharField(max_length=5, default='18:00')
    break_min = models.IntegerField(default=60)
    work_days = models.JSONField(default=list)  # JS weekday numbers, 0 = Sunday
    is_head_office = models.BooleanField(default=False)
    status = models.CharField(max_length=10, default='active', db_index=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_reason = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_head_office', 'name']


class UserBranchScope(models.Model):
    user = models.ForeignKey(_U, on_delete=models.CASCADE, related_name='branch_scopes')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='user_scopes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'branch'], name='uniq_user_branch')]


class Transfer(models.Model):
    emp_id = models.CharField(max_length=60, db_index=True)
    from_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='+')
    to_branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='+')
    effective_date = models.DateField(db_index=True)
    new_dept = models.CharField(max_length=80, blank=True, default='')
    new_designation = models.CharField(max_length=80, blank=True, default='')
    new_reports_to = models.CharField(max_length=60, blank=True, default='')
    reason = models.CharField(max_length=300)
    notes = models.CharField(max_length=500, blank=True, default='')
    status = models.CharField(max_length=10, default='scheduled')  # scheduled, applied, cancelled
    side_effects = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(_U, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)


class EmployeeBranchAssignment(models.Model):
    emp_id = models.CharField(max_length=60)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name='assignments')
    kind = models.CharField(max_length=10, default='primary')  # primary, secondary
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    dept = models.CharField(max_length=80, blank=True, default='')
    designation = models.CharField(max_length=80, blank=True, default='')
    reports_to = models.CharField(max_length=60, blank=True, default='')
    reason = models.CharField(max_length=300, blank=True, default='')
    transfer = models.ForeignKey(Transfer, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, default='active')  # active, ended, cancelled
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['emp_id', 'kind', 'effective_from']), models.Index(fields=['branch', 'kind', 'effective_from'])]
        constraints = [models.UniqueConstraint(fields=['emp_id'], condition=_Q(kind='primary', status='active', effective_to__isnull=True), name='one_open_primary')]


class Department(models.Model):
    name = models.CharField(max_length=80, unique=True)
    code = models.CharField(max_length=10, blank=True, default='')
    status = models.CharField(max_length=10, default='active')

    class Meta:
        ordering = ['name']


class BranchDepartment(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='departments')
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='branches')
    head_emp = models.CharField(max_length=60, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch', 'department'], name='uniq_branch_dept')]


class Designation(models.Model):
    name = models.CharField(max_length=80, unique=True)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=10, default='active')

    class Meta:
        ordering = ['name']


class MeetingRoom(models.Model):
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='rooms')
    name = models.CharField(max_length=60)
    capacity = models.IntegerField(default=6)
    facilities = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, default='active')

    class Meta:
        ordering = ['name']
        constraints = [models.UniqueConstraint(fields=['branch', 'name'], name='uniq_room')]


# ---------- meetings ----------
class MeetingSeries(models.Model):
    rule = models.JSONField(default=dict)
    timezone = models.CharField(max_length=40, default='Asia/Kolkata')
    created_at = models.DateTimeField(auto_now_add=True)


class Meeting(models.Model):
    uid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False)
    series = models.ForeignKey(MeetingSeries, null=True, blank=True, on_delete=models.SET_NULL, related_name='meetings')
    occurrence_index = models.IntegerField(default=0)
    title = models.CharField(max_length=150)
    category = models.CharField(max_length=20)
    custom_type = models.CharField(max_length=50, blank=True, default='')
    status = models.CharField(max_length=12, default='scheduled')  # scheduled, completed, cancelled
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    timezone = models.CharField(max_length=40, default='Asia/Kolkata')
    organizer_emp = models.CharField(max_length=60, blank=True, default='', db_index=True)
    organizer_user = models.ForeignKey(_U, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.PROTECT, related_name='meetings')
    departments = models.JSONField(default=list, blank=True)
    location_type = models.CharField(max_length=10, default='office')  # online, office, external, hybrid
    room = models.ForeignKey(MeetingRoom, null=True, blank=True, on_delete=models.PROTECT, related_name='meetings')
    location_text = models.CharField(max_length=200, blank=True, default='')
    platform = models.CharField(max_length=20, blank=True, default='')
    join_url = models.CharField(max_length=500, blank=True, default='')
    client_rid = models.CharField(max_length=60, blank=True, default='')
    client_name = models.CharField(max_length=120, blank=True, default='')
    client_company = models.CharField(max_length=120, blank=True, default='')
    client_location = models.CharField(max_length=120, blank=True, default='')
    candidate_rid = models.CharField(max_length=60, blank=True, default='')
    agenda = models.JSONField(default=list, blank=True)
    description = models.TextField(blank=True, default='')
    visibility = models.CharField(max_length=8, default='normal')
    reminder_offsets = models.JSONField(default=list, blank=True)
    record_only = models.BooleanField(default=False)
    sequence = models.IntegerField(default=0)
    cancel_reason = models.CharField(max_length=300, blank=True, default='')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(_U, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=['branch', 'start_at']), models.Index(fields=['status', 'start_at']), models.Index(fields=['room', 'start_at'])]


class MeetingParticipant(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='participants')
    emp_id = models.CharField(max_length=60, blank=True, default='', db_index=True)
    ext_name = models.CharField(max_length=120, blank=True, default='')
    ext_email = models.CharField(max_length=120, blank=True, default='')
    ext_phone = models.CharField(max_length=20, blank=True, default='')
    ext_company = models.CharField(max_length=120, blank=True, default='')
    role = models.CharField(max_length=10, default='required')  # organizer, required, optional
    rsvp = models.CharField(max_length=10, default='pending')
    rsvp_at = models.DateTimeField(null=True, blank=True)
    rsvp_note = models.CharField(max_length=300, blank=True, default='')
    attendance = models.CharField(max_length=8, default='unknown')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['meeting', 'emp_id'], condition=~_Q(emp_id=''), name='uniq_mtg_emp'),
            models.UniqueConstraint(fields=['meeting', 'ext_email'], condition=~_Q(ext_email=''), name='uniq_mtg_ext'),
        ]


class MeetingMinutes(models.Model):
    meeting = models.OneToOneField(Meeting, on_delete=models.CASCADE, related_name='minutes')
    summary = models.TextField(blank=True, default='')
    discussion = models.JSONField(default=list, blank=True)
    decisions = models.JSONField(default=list, blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, default='draft')
    published_at = models.DateTimeField(null=True, blank=True)
    version = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class MeetingActionItem(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='action_items')
    title = models.CharField(max_length=150)
    assignee_emp = models.CharField(max_length=60, blank=True, default='')
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=4, default='med')
    est_min = models.IntegerField(null=True, blank=True)
    task_rid = models.CharField(max_length=60, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


# ---------- task time tracking ----------
class WorkSession(models.Model):
    task_rid = models.CharField(max_length=60, db_index=True)
    emp_id = models.CharField(max_length=60)
    user = models.ForeignKey(_U, null=True, on_delete=models.SET_NULL, related_name='+')
    branch = models.ForeignKey(Branch, null=True, on_delete=models.PROTECT, related_name='+')
    dept = models.CharField(max_length=80, blank=True, default='')
    kind = models.CharField(max_length=8, default='timer')  # timer, manual
    status = models.CharField(max_length=12, default='running')  # running, paused, stopped, auto_stopped, void
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    last_transition_at = models.DateTimeField()
    work_seconds = models.IntegerField(default=0)
    pause_seconds = models.IntegerField(default=0)
    work_date = models.DateField()
    remarks = models.CharField(max_length=500, blank=True, default='')
    reason = models.CharField(max_length=300, blank=True, default='')
    category = models.CharField(max_length=30, blank=True, default='')
    approval = models.CharField(max_length=10, default='none')  # none, pending, approved, rejected, withdrawn
    approver = models.ForeignKey(_U, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True, default='')
    needs_review = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['emp_id', 'work_date']), models.Index(fields=['branch', 'work_date']), models.Index(fields=['status']), models.Index(fields=['approval'])]
        constraints = [
            models.UniqueConstraint(fields=['emp_id'], condition=_Q(status='running'), name='one_running_per_emp'),
            models.UniqueConstraint(fields=['emp_id', 'task_rid'], condition=_Q(status__in=['running', 'paused']), name='one_open_per_emp_task'),
        ]


class WorkSessionEvent(models.Model):
    session = models.ForeignKey(WorkSession, on_delete=models.CASCADE, related_name='events')
    type = models.CharField(max_length=20)
    at = models.DateTimeField()
    actor = models.ForeignKey(_U, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reason_code = models.CharField(max_length=30, blank=True, default='')
    note = models.CharField(max_length=300, blank=True, default='')
    req_id = models.CharField(max_length=40, blank=True, default='')
    client_ts = models.CharField(max_length=40, blank=True, default='')
    ip = models.CharField(max_length=45, blank=True, default='')

    class Meta:
        ordering = ['at', 'id']
        constraints = [models.UniqueConstraint(fields=['session', 'req_id'], condition=~_Q(req_id=''), name='uniq_session_req')]


class TimeAdjustment(models.Model):
    session = models.ForeignKey(WorkSession, on_delete=models.CASCADE, related_name='adjustments')
    requested_by = models.ForeignKey(_U, null=True, on_delete=models.SET_NULL, related_name='+')
    old_started_at = models.DateTimeField()
    old_ended_at = models.DateTimeField(null=True)
    old_work_seconds = models.IntegerField()
    new_started_at = models.DateTimeField()
    new_ended_at = models.DateTimeField()
    new_work_seconds = models.IntegerField()
    reason = models.CharField(max_length=300)
    status = models.CharField(max_length=10, default='pending')
    approver = models.ForeignKey(_U, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)


class TimePolicy(models.Model):
    """scope 'company' (branch null) or a branch override. Missing values fall back to the company row."""
    branch = models.OneToOneField(Branch, null=True, blank=True, on_delete=models.CASCADE, related_name='time_policy')
    values = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['branch'], condition=_Q(branch__isnull=True), name='one_company_policy')]


# ---------- notifications ----------
class NotificationProvider(models.Model):
    channel = models.CharField(max_length=10)  # whatsapp, sms
    provider = models.CharField(max_length=20)  # meta_cloud, msg91
    enabled = models.BooleanField(default=False)
    config_public = models.JSONField(default=dict, blank=True)
    secrets_enc = models.TextField(blank=True, default='')
    webhook_token = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=12, default='unverified')
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['channel'], name='one_provider_per_channel')]


class NotificationEventConfig(models.Model):
    event_key = models.CharField(max_length=40, unique=True)
    enabled = models.BooleanField(default=True)
    channels = models.JSONField(default=list)
    mandatory = models.BooleanField(default=False)
    fallback_sms = models.BooleanField(default=True)
    fallback_after_min = models.IntegerField(default=10)
    timing = models.JSONField(default=dict, blank=True)
    quiet_hours = models.BooleanField(default=True)
    branch_overrides = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationTemplate(models.Model):
    event_key = models.CharField(max_length=40, db_index=True)
    channel = models.CharField(max_length=10)  # whatsapp, sms, email, inapp
    language = models.CharField(max_length=5, default='en')
    name = models.CharField(max_length=80)
    subject = models.CharField(max_length=150, blank=True, default='')
    body = models.TextField()
    variables = models.JSONField(default=list, blank=True)
    category = models.CharField(max_length=15, default='utility')
    provider_template_name = models.CharField(max_length=120, blank=True, default='')
    provider_template_id = models.CharField(max_length=60, blank=True, default='')
    approval_status = models.CharField(max_length=10, default='draft')
    version = models.IntegerField(default=1)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event_key', 'channel', 'language', 'version'], name='uniq_template_version')]


class NotificationCampaign(models.Model):
    title = models.CharField(max_length=150)
    audience = models.JSONField(default=dict)
    channel = models.CharField(max_length=10, default='whatsapp')
    template = models.ForeignKey(NotificationTemplate, null=True, on_delete=models.PROTECT)
    variables = models.JSONField(default=dict, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, default='scheduled')  # scheduled, sending, sent, cancelled
    recipient_count = models.IntegerField(default=0)
    skipped = models.JSONField(default=dict, blank=True)
    notice_rid = models.CharField(max_length=60, blank=True, default='')
    created_by = models.ForeignKey(_U, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)


class NotificationJob(models.Model):
    event_key = models.CharField(max_length=40)
    entity_type = models.CharField(max_length=20, blank=True, default='')
    entity_id = models.CharField(max_length=60, blank=True, default='')
    occurrence_key = models.CharField(max_length=120, blank=True, default='')
    recipients = models.JSONField(default=list)
    context = models.JSONField(default=dict, blank=True)
    scheduled_for = models.DateTimeField()
    status = models.CharField(max_length=12, default='queued')  # queued, done, cancelled
    campaign = models.ForeignKey(NotificationCampaign, null=True, blank=True, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'scheduled_for'])]
        constraints = [models.UniqueConstraint(fields=['event_key', 'entity_id', 'occurrence_key'], name='uniq_notif_job')]


class NotificationDelivery(models.Model):
    job = models.ForeignKey(NotificationJob, on_delete=models.CASCADE, related_name='deliveries')
    emp_id = models.CharField(max_length=60, db_index=True)
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    channel = models.CharField(max_length=10)
    to_address = models.CharField(max_length=120, blank=True, default='')
    template = models.ForeignKey(NotificationTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    title = models.CharField(max_length=150, blank=True, default='')
    rendered = models.TextField(blank=True, default='')
    link = models.CharField(max_length=200, blank=True, default='')
    provider_msg_id = models.CharField(max_length=120, blank=True, default='', db_index=True)
    status = models.CharField(max_length=16, default='queued')
    skip_reason = models.CharField(max_length=20, blank=True, default='')
    error_code = models.CharField(max_length=40, blank=True, default='')
    error_message = models.CharField(max_length=300, blank=True, default='')
    attempts = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    queued_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    read_in_app_at = models.DateTimeField(null=True, blank=True)
    fallback_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='fallbacks')
    dedupe_key = models.CharField(max_length=200, unique=True)

    class Meta:
        indexes = [models.Index(fields=['emp_id', 'channel', 'queued_at']), models.Index(fields=['status', 'next_attempt_at'])]


class ContactProfile(models.Model):
    emp_id = models.CharField(max_length=60, unique=True)
    phone_e164 = models.CharField(max_length=16, blank=True, default='')
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    verify_method = models.CharField(max_length=10, blank=True, default='')
    wa_opt_in = models.BooleanField(default=False)
    sms_opt_in = models.BooleanField(default=False)
    prefs = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class ConsentEvent(models.Model):
    contact = models.ForeignKey(ContactProfile, on_delete=models.CASCADE, related_name='consents')
    channel = models.CharField(max_length=10)
    action = models.CharField(max_length=10)  # opt_in, opt_out, verify, attest, reset
    source = models.CharField(max_length=12)
    notice_version = models.CharField(max_length=20, blank=True, default='')
    actor = models.ForeignKey(_U, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=300, blank=True, default='')


class OtpChallenge(models.Model):
    contact = models.ForeignKey(ContactProfile, on_delete=models.CASCADE, related_name='otps')
    phone = models.CharField(max_length=16)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.IntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class WebhookLog(models.Model):
    provider = models.CharField(max_length=20)
    received_at = models.DateTimeField(auto_now_add=True)
    signature_ok = models.BooleanField(default=False)
    payload = models.TextField(blank=True, default='')
    error = models.CharField(max_length=300, blank=True, default='')


# ---------- calendar ----------
class CalendarConnection(models.Model):
    user = models.ForeignKey(_U, on_delete=models.CASCADE, related_name='calendar_connections')
    provider = models.CharField(max_length=10)  # google, microsoft
    account_email = models.CharField(max_length=200, blank=True, default='')
    refresh_token_enc = models.TextField(blank=True, default='')
    access_token_enc = models.TextField(blank=True, default='')
    token_expiry = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=14, default='active')
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'provider'], name='uniq_cal_conn')]


class CalendarEventLink(models.Model):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='calendar_links')
    connection = models.ForeignKey(CalendarConnection, on_delete=models.CASCADE, related_name='links')
    provider_event_id = models.CharField(max_length=200, blank=True, default='')
    etag = models.CharField(max_length=200, blank=True, default='')
    status = models.CharField(max_length=20, default='pending')
    synced_sequence = models.IntegerField(default=-1)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['meeting', 'connection'], name='uniq_cal_link')]


class OAuthPending(models.Model):
    nonce = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(_U, on_delete=models.CASCADE, related_name='+')
    provider = models.CharField(max_length=10)
    verifier_enc = models.TextField()
    expires_at = models.DateTimeField()


# ---------- shared platform ----------
class Job(models.Model):
    kind = models.CharField(max_length=40)
    payload = models.JSONField(default=dict, blank=True)
    run_at = models.DateTimeField()
    status = models.CharField(max_length=10, default='queued')  # queued, running, done, failed, cancelled
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    unique_key = models.CharField(max_length=200, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['status', 'run_at'])]


class AuditEvent(models.Model):
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(_U, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    actor_name = models.CharField(max_length=150, blank=True, default='')
    action = models.CharField(max_length=40)
    entity_type = models.CharField(max_length=30)
    entity_id = models.CharField(max_length=60, blank=True, default='')
    branch = models.ForeignKey(Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.CharField(max_length=45, blank=True, default='')

    class Meta:
        indexes = [models.Index(fields=['entity_type', 'entity_id']), models.Index(fields=['branch', 'at'])]


class Attachment(models.Model):
    owner_type = models.CharField(max_length=10)  # task, meeting
    owner_id = models.CharField(max_length=60)
    file_name = models.CharField(max_length=200)
    mime = models.CharField(max_length=100)
    size = models.IntegerField()
    storage_path = models.CharField(max_length=300)
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(_U, null=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=['owner_type', 'owner_id'])]
