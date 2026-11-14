from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class Wallet(models.Model):
    """Coins (bought) and vouchers (earned, spent as votes). Bonus lives in
    [BonusBatch] because each batch expires on its own date."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='wallet')
    coins = models.PositiveIntegerField(default=0)
    vouchers = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f'{self.user} · {self.coins} coins'

    @property
    def bonus(self):
        return sum(b.remaining for b in self.user.bonus_batches.all())


class BonusBatch(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bonus_batches')
    source = models.CharField(max_length=80)
    amount = models.PositiveIntegerField()
    consumed = models.PositiveIntegerField(default=0)
    received_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ['-received_at']

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def remaining(self):
        return 0 if self.is_expired else self.amount - self.consumed

    @property
    def expired(self):
        return self.amount - self.consumed if self.is_expired else 0


class VoucherEntry(models.Model):
    """Voucher history: + earned, − spent on votes."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='voucher_entries')
    source = models.CharField(max_length=80)
    amount = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'voucher entries'


class Transaction(models.Model):
    KIND_TOP_UP, KIND_SUBSCRIPTION, KIND_UNLOCK, KIND_GIFT, KIND_REWARD = (
        'top_up', 'subscription', 'unlock', 'gift', 'reward',
    )
    PROVIDER_CHOICES = [('google_play', 'Google Play'), ('app_store', 'Apple App Store'), ('paystack', 'Paystack'),
                        ('stripe', 'Stripe'), ('paypal', 'PayPal'), ('manual', 'Manual / support')]
    STATUS_SUCCEEDED, STATUS_PENDING, STATUS_FAILED, STATUS_REVERSED = 'succeeded', 'pending', 'failed', 'reversed'
    STATUS_CHOICES = [(STATUS_SUCCEEDED, 'Succeeded'), (STATUS_PENDING, 'Pending'), (STATUS_FAILED, 'Failed'),
                      (STATUS_REVERSED, 'Reversed')]
    KIND_CHOICES = [
        (KIND_TOP_UP, 'Top up'),
        (KIND_SUBSCRIPTION, 'Subscription'),
        (KIND_UNLOCK, 'Chapter unlock'),
        (KIND_GIFT, 'Gift'),
        (KIND_REWARD, 'Reward'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transactions')
    kind = models.CharField(max_length=15, choices=KIND_CHOICES)
    title = models.CharField(max_length=160)
    coins = models.IntegerField(default=0)  # + added, − spent
    bonus_used = models.PositiveIntegerField(default=0)
    price_cents = models.PositiveIntegerField(default=0)  # real money, USD cents
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True)
    chapter = models.ForeignKey('catalog.Chapter', on_delete=models.SET_NULL, null=True, blank=True)
    # Real-money payments: who took the payment, its outcome and settlement.
    # Only succeeded payments count as revenue.
    provider = models.CharField(max_length=12, choices=PROVIDER_CHOICES, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_SUCCEEDED, db_index=True)
    reference = models.CharField(max_length=120, blank=True, help_text='Store order ID or provider reference')
    settled_at = models.DateTimeField(null=True, blank=True, help_text='When the money reached a Bonovela account')
    settlement_ref = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} · {self.title}'

    @property
    def code(self):
        return f'TXN-{self.pk:06d}'

    @property
    def is_money(self):
        """Paid with real money (coin package or subscription)."""
        return self.kind in (self.KIND_TOP_UP, self.KIND_SUBSCRIPTION)

    @property
    def value_cents(self):
        """Money for purchases; for coin spending, the coins' value (1 coin = 1¢)."""
        return self.price_cents if self.is_money else abs(self.coins) + self.bonus_used

    @property
    def category(self):
        return {self.KIND_TOP_UP: 'Coin purchase', self.KIND_SUBSCRIPTION: 'Subscription',
                self.KIND_UNLOCK: 'Chapter unlock', self.KIND_GIFT: 'Reader gift',
                self.KIND_REWARD: 'Reward'}.get(self.kind, self.kind)

    @property
    def price(self):
        return f'${self.price_cents // 100}.{self.price_cents % 100:02d}' if self.price_cents else ''


class ChapterUnlock(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='unlocks')
    chapter = models.ForeignKey('catalog.Chapter', on_delete=models.CASCADE, related_name='unlocks')
    coins_paid = models.PositiveIntegerField(default=0)
    bonus_paid = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'chapter')


class AutoUnlock(models.Model):
    """'Auto Subscription': unlock the next chapters of a book automatically."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='auto_unlocks')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE)

    class Meta:
        unique_together = ('user', 'story')


class CoinPackage(models.Model):
    coins = models.PositiveIntegerField()
    bonus = models.PositiveIntegerField(default=0)
    price_cents = models.PositiveIntegerField()
    tag = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'coins']

    def __str__(self):
        return f'{self.coins} coins · ${self.price_cents / 100:.2f}'


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=40)
    price_cents = models.PositiveIntegerField()
    old_price_cents = models.PositiveIntegerField(null=True, blank=True)
    period = models.CharField(max_length=10, default='month')  # week | month | year
    days = models.PositiveIntegerField()
    billing_note = models.CharField(max_length=80, blank=True)
    badge = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


class Subscription(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='subscriptions')
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()

    class Meta:
        ordering = ['-ends_at']

    @property
    def is_active(self):
        return self.starts_at <= timezone.now() < self.ends_at


class GiftItem(models.Model):
    name = models.CharField(max_length=40)
    emoji = models.CharField(max_length=8)
    price = models.PositiveIntegerField(help_text='Coins')
    votes = models.PositiveIntegerField(help_text='Votes sent to the book with this gift')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'price']

    def __str__(self):
        return f'{self.emoji} {self.name}'


class Gift(models.Model):
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='gifts_sent')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='gifts')
    item = models.ForeignKey(GiftItem, on_delete=models.PROTECT)
    price = models.PositiveIntegerField()
    votes = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Vote(models.Model):
    """Votes for a book: from vouchers, or carried by a gift."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='votes')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='votes')
    count = models.PositiveIntegerField()
    from_gift = models.ForeignKey(Gift, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AuthorEarning(models.Model):
    """Money owed to an author, in USD cents, grouped by income type."""

    TYPE_CHOICES = [
        ('signing_bonus', 'Signing Bonus'),
        ('profit_share', 'Profit Share'),
        ('attendance', 'Monthly Attendance Bonus (MAB)'),
        ('completion', 'Completion Bonus'),
        ('one_time', 'One-time Payment'),
        ('others', 'Others'),
        ('carry_over', 'Carry over'),
        ('reward_share', 'Reward Share'),
        ('subscription_share', 'Short Story Subscription'),
        ('editor_commission', 'Editorial Commission'),
    ]
    # No card on the Income page: no longer paid (signing, completion) or
    # replaced by "Available to withdraw" (MAB). Old rows still count in totals.
    RETIRED_TYPES = {'signing_bonus', 'attendance'}
    # Cards on the author Income page, in this order (then Available to
    # withdraw and Withdrawal history). Others and Short Story
    # Subscription still count in the totals but have no card.
    INCOME_CARDS = ['profit_share', 'completion', 'one_time', 'reward_share', 'carry_over']

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='earnings')
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount_cents = models.IntegerField()
    note = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']


class Payout(models.Model):
    """A payment request to an author or staff member: an author's
    withdrawal, earnings Finance pays out, or a staff commission. Finance
    approves an amount (never more than requested), then marks it paid
    with the payout reference. Rejections are internal: nobody is told."""

    (STATUS_PENDING, STATUS_REVIEW, STATUS_HOLD, STATUS_APPROVED, STATUS_PAID, STATUS_REJECTED,
     STATUS_FAILED) = 'pending', 'review', 'hold', 'approved', 'paid', 'rejected', 'failed'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_REVIEW, 'Under review'), (STATUS_HOLD, 'On hold'),
                      (STATUS_APPROVED, 'Approved'), (STATUS_PAID, 'Paid'), (STATUS_REJECTED, 'Rejected'),
                      (STATUS_FAILED, 'Failed')]
    OPEN = (STATUS_PENDING, STATUS_REVIEW, STATUS_HOLD)  # needs a decision
    # Money set aside for the person (not back in their balance).
    COMMITTED = OPEN + (STATUS_APPROVED, STATUS_PAID)

    CAT_EARNINGS, CAT_WITHDRAWAL, CAT_STAFF = 'earnings', 'withdrawal', 'staff'
    CATEGORY_CHOICES = [(CAT_EARNINGS, 'Author earnings'), (CAT_WITHDRAWAL, 'Withdrawal'),
                        (CAT_STAFF, 'Staff commission')]

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payouts', help_text='Beneficiary')
    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES, default=CAT_EARNINGS)
    requested_cents = models.PositiveIntegerField(default=0, help_text='What was asked for')
    amount_cents = models.PositiveIntegerField(help_text='Requested until approved; then the approved amount')
    period_end = models.DateField(help_text='Earnings up to and including this day')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    method = models.CharField(max_length=160, blank=True, help_text='Payout method at the time of the request')
    reference = models.CharField(max_length=120, blank=True, help_text='Payout confirmation reference')
    note = models.CharField(max_length=200, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True, help_text='Internal only')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # Approval; large amounts need a second, different approver.
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    second_approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    second_approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.author} · {self.amount_cents / 100:.2f} · {self.get_status_display()}'

    def save(self, *args, **kwargs):
        if not self.requested_cents:
            self.requested_cents = self.amount_cents
        super().save(*args, **kwargs)

    @property
    def code(self):
        return f'PAY-{self.pk:04d}'

    @property
    def approved_cents(self):
        return self.amount_cents if self.status in (self.STATUS_APPROVED, self.STATUS_PAID) else None


class PayoutEvent(models.Model):
    """The history of a payment request (never edited)."""

    payout = models.ForeignKey(Payout, on_delete=models.CASCADE, related_name='events')
    action = models.CharField(max_length=120)
    note = models.CharField(max_length=200, blank=True)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']


class PayoutMethod(models.Model):
    """Where an author's earnings go. The account may be the author's own or
    a third party's."""

    CURRENCY_CHOICES = [('USD', 'USD'), ('NGN', 'NGN')]
    # Required on top of the base fields when paying by bank.
    BANK_FIELDS = ('bank_name', 'swift_code', 'country', 'currency', 'address', 'city', 'state', 'postal_code')

    author = models.OneToOneField(User, on_delete=models.CASCADE, related_name='payout_method')
    method = models.CharField(max_length=20, choices=[('bank', 'Bank transfer'), ('paypal', 'PayPal')])
    account_name = models.CharField(max_length=120, help_text='Account holder, exactly as on the account')
    account_detail = models.CharField(max_length=120, help_text='Account number or PayPal email')
    bank_name = models.CharField(max_length=120, blank=True)
    swift_code = models.CharField('SWIFT code / BIC', max_length=11, blank=True)
    country = models.CharField('Account country / region', max_length=60, blank=True)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, blank=True)
    address = models.CharField(max_length=160, blank=True, help_text='Not a PO or GPO box')
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField('State / Province', max_length=80, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    intermediary_swift = models.CharField('Intermediary bank SWIFT code / BIC', max_length=11, blank=True)
    nickname = models.CharField(max_length=60, blank=True, help_text='For reference only. Not sent to the recipient.')
    email = models.EmailField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class RedeemCode(models.Model):
    REWARD_CHOICES = [('coins', 'Bonus coins'), ('vouchers', 'Free chapter unlocks')]

    code = models.CharField(max_length=30, unique=True)
    reward = models.CharField(max_length=10, choices=REWARD_CHOICES, default='coins')
    bonus = models.PositiveIntegerField(help_text='Coins, or chapters for free chapter unlocks')
    is_active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    segment = models.CharField(max_length=40, blank=True, help_text='Only readers in this segment (blank: all)')
    countries = models.CharField(max_length=200, blank=True, help_text='Comma-separated; blank: all')
    rejected = models.PositiveIntegerField(default=0)
    redeemed_by = models.ManyToManyField(User, blank=True, related_name='redeemed_codes')
    # Set by Marketing; blank means no limit.
    campaign = models.CharField(max_length=80, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    @property
    def uses(self):
        return self.redeemed_by.count()

    @property
    def is_usable(self):
        now = timezone.now()
        if not self.is_active or (self.expires_at and self.expires_at <= now) or (self.starts_at and self.starts_at > now):
            return False
        return self.max_uses is None or self.uses < self.max_uses

    def __str__(self):
        return self.code

    @property
    def status(self):
        now = timezone.now()
        if self.expires_at and self.expires_at <= now:
            return 'expired'
        if not self.is_active:
            return 'paused'
        return 'scheduled' if self.starts_at and self.starts_at > now else 'active'

    @property
    def country_list(self):
        return [c.strip() for c in self.countries.split(',') if c.strip()]


class CodeRedemption(models.Model):
    """When a reader redeemed a code (cost and revenue after it)."""

    code = models.ForeignKey(RedeemCode, on_delete=models.CASCADE, related_name='redemptions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
