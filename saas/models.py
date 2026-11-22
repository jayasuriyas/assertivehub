from django.db import models


class Tenant(models.Model):
    """One customer company. Lives in the platform database; its data lives in its own database file."""
    slug = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=80)
    owner_name = models.CharField(max_length=80)
    owner_email = models.CharField(max_length=120)
    owner_phone = models.CharField(max_length=15, blank=True, default='')
    state = models.CharField(max_length=60, blank=True, default='')
    gstin = models.CharField(max_length=15, blank=True, default='')
    plan = models.CharField(max_length=12, default='starter')
    status = models.CharField(max_length=12, default='active')  # active, suspended, cancelled
    created = models.DateField(auto_now_add=True)
    trial_ends = models.DateField()
    paid_until = models.DateField(null=True, blank=True)
    last_notice = models.CharField(max_length=60, blank=True, default='')
    notes = models.TextField(blank=True, default='')


class Payment(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='payments')
    order_id = models.CharField(max_length=40, unique=True)
    payment_id = models.CharField(max_length=40, null=True, blank=True, unique=True)
    plan = models.CharField(max_length=12)
    period = models.CharField(max_length=8)          # month, year, manual
    base = models.IntegerField()                      # paise, before GST
    cgst = models.IntegerField(default=0)
    sgst = models.IntegerField(default=0)
    igst = models.IntegerField(default=0)
    total = models.IntegerField()
    status = models.CharField(max_length=8, default='created')  # created, paid
    method = models.CharField(max_length=12, default='razorpay')
    created = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    invoice_no = models.CharField(max_length=20, null=True, blank=True, unique=True)
    bill_name = models.CharField(max_length=80, blank=True, default='')
    bill_gstin = models.CharField(max_length=15, blank=True, default='')
    bill_state = models.CharField(max_length=60, blank=True, default='')
    covers_to = models.DateField(null=True, blank=True)


class SignupAttempt(models.Model):
    ip = models.CharField(max_length=45, db_index=True)
    at = models.FloatField()


class OwnerLog(models.Model):
    at = models.DateTimeField(auto_now_add=True)
    who = models.CharField(max_length=60)
    action = models.CharField(max_length=60)
    slug = models.CharField(max_length=30, blank=True, default='')
