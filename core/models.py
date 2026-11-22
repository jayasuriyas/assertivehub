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
