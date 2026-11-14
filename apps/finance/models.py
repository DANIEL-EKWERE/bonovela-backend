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
                                                       help_text='Authors can withdraw once their balance reaches this amount.')
    chargeback_reserve_pct = models.DecimalField('Chargeback reserve', max_digits=5, decimal_places=2, default=5,
                                                 help_text='Share of new revenue held back for disputes.')
    weekly_payouts = models.BooleanField(default=False, help_text='Pay authors every Friday instead of monthly.')
    two_person_approval = models.BooleanField(default=True,
                                              help_text='Payments of $1,000 or more need two different approvers.')
    max_withdrawal_cents = models.PositiveIntegerField('Maximum single withdrawal', default=1000000)
    store_fee_pct = models.DecimalField('App store fee', max_digits=5, decimal_places=2, default=15,
                                        help_text='Google Play / App Store commission on in-app purchases.')
    auto_approve_small_refunds = models.BooleanField(default=False,
                                                     help_text='Refunds under $5 are approved automatically.')
    updated_at = models.DateTimeField(auto_now=True)

    TWO_PERSON_THRESHOLD_CENTS = 100000  # $1,000 or more
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


# ── Short Story Subscriptions ────────────────────────────────────────────────
class SubscriberRead(models.Model):
    """A Plus subscriber reading a short-story chapter they didn't pay coins
    for; the subscription pool is shared by these reads."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    chapter = models.ForeignKey('catalog.Chapter', on_delete=models.CASCADE, related_name='+')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='+')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    month = models.DateField(help_text='First day of the month', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'chapter', 'month'], name='one_read_per_month')]


class SubscriptionShare(models.Model):
    """An author's share of one month's subscription pool, decided by Finance."""

    STATUS_WAITING, STATUS_APPROVED, STATUS_SKIPPED = 'waiting', 'approved', 'skipped'
    STATUS_CHOICES = [(STATUS_WAITING, 'Waiting for decision'), (STATUS_APPROVED, 'Approved'),
                      (STATUS_SKIPPED, "Don't pay")]

    month = models.DateField()
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    reads = models.PositiveIntegerField(default=0)
    suggested_cents = models.PositiveIntegerField(default=0)
    approved_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_WAITING)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-reads']
        constraints = [models.UniqueConstraint(fields=['month', 'author'], name='one_share_per_author_month')]


# ── Refunds & Chargebacks ────────────────────────────────────────────────────
PROCESSORS = [(p, p) for p in ('Google Play', 'Apple', 'Stripe', 'PayPal', 'Bank', 'Other')]


class RefundCase(models.Model):
    KIND_REFUND, KIND_CHARGEBACK = 'refund', 'chargeback'
    KIND_CHOICES = [(KIND_REFUND, 'Refund'), (KIND_CHARGEBACK, 'Chargeback')]
    STATUS_OPEN, STATUS_REFUNDED, STATUS_REJECTED = 'open', 'refunded', 'rejected'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_REFUNDED, 'Refunded'), (STATUS_REJECTED, 'Rejected')]

    reader = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    transaction = models.ForeignKey('wallet.Transaction', on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_REFUND)
    reason = models.CharField(max_length=160)
    processor = models.CharField(max_length=12, choices=PROCESSORS, default='Google Play')
    amount_cents = models.PositiveIntegerField()
    refunded_cents = models.PositiveIntegerField(default=0)
    coins_removed = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    resolution = models.CharField(max_length=200, blank=True)
    opened_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def code(self):
        return f"{'CB' if self.kind == self.KIND_CHARGEBACK else 'RF'}-{self.pk}"


# ── Reader Support ───────────────────────────────────────────────────────────
class SupportTicket(models.Model):
    CATEGORY_CHOICES = [('coins', 'Coins'), ('subscription', 'Subscription'), ('other', 'Other')]
    ISSUE_CHOICES = [('not_delivered', 'Paid, not delivered'), ('failed', 'Payment failed'),
                     ('no_payment', 'No payment found'), ('credited', 'Already credited'), ('other', 'Other')]
    STATUS_OPEN, STATUS_RESOLVED = 'open', 'resolved'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_RESOLVED, 'Resolved')]

    reader = models.ForeignKey(User, on_delete=models.CASCADE, related_name='support_tickets')
    category = models.CharField(max_length=12, choices=CATEGORY_CHOICES, default='coins')
    issue = models.CharField(max_length=14, choices=ISSUE_CHOICES, default='not_delivered')
    message = models.TextField()
    payment_ref = models.CharField(max_length=80, blank=True, help_text='Store order ID or receipt the reader gave')
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    resolution = models.CharField(max_length=200, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def code(self):
        return f'ST-{self.pk}'


# ── Bonuses & Incentives ─────────────────────────────────────────────────────
class BonusProgram(models.Model):
    AUDIENCE_CHOICES = [('authors', 'Authors (cash to their income)'), ('readers', 'Readers (coins)')]

    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200)
    audience = models.CharField(max_length=8, choices=AUDIENCE_CHOICES, default='authors')
    monthly_budget_cents = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_active', 'name']


class BonusAward(models.Model):
    STATUS_SCHEDULED, STATUS_PAID, STATUS_CANCELLED = 'scheduled', 'paid', 'cancelled'
    STATUS_CHOICES = [(STATUS_SCHEDULED, 'Scheduled'), (STATUS_PAID, 'Paid'), (STATUS_CANCELLED, 'Cancelled')]

    program = models.ForeignKey(BonusProgram, on_delete=models.PROTECT, related_name='awards')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    amount_cents = models.PositiveIntegerField(help_text='USD cents; for readers, coins (1 coin = 1¢)')
    date = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_SCHEDULED)
    note = models.CharField(max_length=160, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date', '-pk']

    @property
    def code(self):
        return f'BN-{self.pk}'


# ── Reconciliation ───────────────────────────────────────────────────────────
class VerifiedBalance(models.Model):
    """A Treasury account balance checked against a bank or provider
    statement. The latest one per account is shown next to the recorded
    (ledger) balance."""

    account = models.CharField(max_length=20)
    cents = models.BigIntegerField()
    statement_ref = models.CharField(max_length=80)
    as_of = models.DateField()
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-as_of', '-pk']


class ProcessorPayout(models.Model):
    """A payout a processor or bank reported, matched against the ledger."""

    STATUS_MATCHED, STATUS_REVIEW, STATUS_RESOLVED = 'matched', 'review', 'resolved'
    STATUS_CHOICES = [(STATUS_MATCHED, 'Matched'), (STATUS_REVIEW, 'Needs review'), (STATUS_RESOLVED, 'Resolved')]

    source = models.CharField(max_length=12, choices=PROCESSORS)
    reference = models.CharField(max_length=80)
    period_start = models.DateField()
    period_end = models.DateField()
    processor_cents = models.IntegerField(help_text="What the processor's statement says")
    ledger_cents = models.IntegerField(help_text="What Bonovela's ledger says for the same period")
    status = models.CharField(max_length=8, choices=STATUS_CHOICES, default=STATUS_REVIEW)
    resolution = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period_end', '-pk']

    @property
    def difference(self):
        return self.processor_cents - self.ledger_cents
