"""Bonovela Finance: settings, the append-only audit log, the finance team
and operating expenses. Amounts are USD cents."""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class FinanceSettings(models.Model):
    """One row. Read with FinanceSettings.current(); changes are audited."""

    author_share_pct = models.DecimalField('Author share', max_digits=5, decimal_places=2, default=50,
                                           help_text='Share of net revenue paid to authors.')
    processing_fee_pct = models.DecimalField('Processing fee', max_digits=5, decimal_places=2, default=3,
                                             help_text='Average fee taken by payment processors.')
    minimum_payout_cents = models.PositiveIntegerField('Minimum payout', default=10000,
                                                       help_text='Authors are paid once earnings reach this amount.')
    chargeback_reserve_pct = models.DecimalField('Chargeback reserve', max_digits=5, decimal_places=2, default=5,
                                                 help_text='Share of new revenue held back for disputes.')
    weekly_payouts = models.BooleanField(default=False, help_text='Pay authors every Friday instead of monthly.')
    two_person_approval = models.BooleanField(default=True, help_text='Payouts over $5,000 need a second approver.')
    auto_approve_small_refunds = models.BooleanField(default=False,
                                                     help_text='Refunds under $5 are approved automatically.')
    updated_at = models.DateTimeField(auto_now=True)

    TWO_PERSON_THRESHOLD_CENTS = 500000
    SMALL_REFUND_CENTS = 500

    class Meta:
        verbose_name_plural = 'finance settings'

    @classmethod
    def current(cls):
        return cls.objects.first() or cls.objects.create()


class AuditEntry(models.Model):
    """Every finance action, written once and never changed."""

    CATEGORY_CHOICES = [(c, c) for c in (
        'Approval', 'Payout', 'Setting', 'Refund', 'Access', 'Expense', 'Bonus', 'Support', 'Subscription',
        'Reconciliation', 'Report',
    )]

    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    actor_label = models.CharField(max_length=120, help_text='Name (role) at the time, or System (Automation)')
    action = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    reference = models.CharField(max_length=60, blank=True, help_text='e.g. PO-5521')
    detail = models.CharField(max_length=200, blank=True, help_text='e.g. $3,240.00 or 58% → 60%')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name_plural = 'audit entries'

    @property
    def code(self):
        return f'AE-{self.pk}'

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionDenied('Audit entries are locked once written.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied('Audit entries are locked once written.')


class FinanceMember(models.Model):
    """Someone allowed into Bonovela Finance, and what they may do."""

    ROLE_MANAGEMENT, ROLE_LEAD, ROLE_ANALYST, ROLE_VIEWER = 'management', 'lead', 'analyst', 'viewer'
    ROLE_CHOICES = [(ROLE_MANAGEMENT, 'Management'), (ROLE_LEAD, 'Finance Lead'), (ROLE_ANALYST, 'Analyst'),
                    (ROLE_VIEWER, 'Viewer')]
    # permission → roles that have it (the "What each role can do" table)
    PERMISSIONS = {
        'view_all': ('View all', {ROLE_MANAGEMENT, ROLE_LEAD, ROLE_ANALYST, ROLE_VIEWER}),
        'approve_payouts': ('Approve payouts', {ROLE_MANAGEMENT, ROLE_LEAD}),
        'approve_refunds': ('Approve refunds', {ROLE_MANAGEMENT, ROLE_LEAD, ROLE_ANALYST}),
        'change_settings': ('Change settings', {ROLE_MANAGEMENT}),
        'manage_team': ('Manage team', {ROLE_MANAGEMENT, ROLE_LEAD}),
    }

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='finance_member')
    role = models.CharField(max_length=12, choices=ROLE_CHOICES, default=ROLE_ANALYST)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    last_active = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['role', 'user__email']

    def can(self, permission):
        return self.role in self.PERMISSIONS[permission][1]


class Expense(models.Model):
    """What it costs to run Bonovela."""

    CATEGORY_CHOICES = [(c, c) for c in ('Hosting', 'Marketing', 'Payroll', 'Software', 'Legal', 'Office', 'Other')]
    STATUS_PAID, STATUS_DUE = 'paid', 'due'
    STATUS_CHOICES = [(STATUS_PAID, 'Paid'), (STATUS_DUE, 'Due')]

    title = models.CharField('Vendor / description', max_length=120)
    category = models.CharField(max_length=12, choices=CATEGORY_CHOICES)
    amount_cents = models.PositiveIntegerField()
    date = models.DateField(default=timezone.localdate, help_text='Invoice or due date')
    status = models.CharField(max_length=4, choices=STATUS_CHOICES, default=STATUS_DUE)
    paid_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-pk']

    @property
    def code(self):
        return f'EX-{self.pk}'
