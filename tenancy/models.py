import datetime

from django.db import models

CORE_MODULES = ['attendance', 'leave', 'tasks', 'clients', 'calendar', 'performance', 'review', 'workrep', 'ratings']
ALL_MODULES = CORE_MODULES + ['notices', 'documents', 'assets', 'payroll', 'checklists', 'recruitment', 'invoices']


class Plan(models.Model):
    """A price plan. The module list is what a company on this plan may switch on."""
    key = models.SlugField(max_length=20, unique=True)
    name = models.CharField(max_length=40)
    price_month = models.PositiveIntegerField(default=0)  # rupees, before GST
    price_year = models.PositiveIntegerField(default=0)
    max_employees = models.PositiveIntegerField(default=15)
    modules = models.JSONField(default=list)
    sort = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort', 'id']

    def __str__(self):
        return self.name


class Tenant(models.Model):
    """One company. Its data lives in its own database file."""
    TRIAL, ACTIVE, SUSPENDED, CANCELLED = 'trial', 'active', 'suspended', 'cancelled'
    slug = models.SlugField(max_length=30, unique=True)
    company = models.CharField(max_length=80)
    owner_name = models.CharField(max_length=80, blank=True, default='')
    owner_email = models.EmailField(blank=True, default='')
    owner_phone = models.CharField(max_length=20, blank=True, default='')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, default='trial')
    trial_ends = models.DateField(null=True, blank=True)
    paid_until = models.DateField(null=True, blank=True)  # filled by billing later
    gst_number = models.CharField(max_length=20, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def effective_status(self):
        today = datetime.date.today()
        if self.status == self.TRIAL and self.trial_ends and self.trial_ends < today:
            return 'expired'
        if self.status == self.ACTIVE and self.paid_until and self.paid_until < today - datetime.timedelta(days=3):
            return 'expired'
        return self.status

    def usable(self):
        return self.effective_status() in ('trial', 'active')

    def days_left(self):
        if self.status == self.TRIAL and self.trial_ends:
            return (self.trial_ends - datetime.date.today()).days
        return None

    def __str__(self):
        return self.slug
