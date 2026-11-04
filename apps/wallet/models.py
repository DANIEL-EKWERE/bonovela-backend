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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user} · {self.title}'

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
        ('editor_commission', 'Editorial Commission'),
    ]

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='earnings')
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount_cents = models.IntegerField()
    note = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']


class Payout(models.Model):
    """Money sent to an author or drama creator. Finance creates these from
    unpaid earnings and marks them paid once the transfer has gone out."""

    STATUS_PENDING, STATUS_PAID, STATUS_FAILED = 'pending', 'paid', 'failed'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_PAID, 'Paid'), (STATUS_FAILED, 'Failed')]

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payouts')
    amount_cents = models.PositiveIntegerField()
    period_end = models.DateField(help_text='Earnings up to and including this day')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    method = models.CharField(max_length=160, blank=True, help_text='Payout method at the time of payment')
    reference = models.CharField(max_length=120, blank=True, help_text='Bank / PayPal transaction id')
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.author} · {self.amount_cents / 100:.2f} · {self.get_status_display()}'


class PayoutMethod(models.Model):
    author = models.OneToOneField(User, on_delete=models.CASCADE, related_name='payout_method')
    method = models.CharField(max_length=20, choices=[('bank', 'Bank transfer'), ('paypal', 'PayPal')])
    account_name = models.CharField(max_length=120)
    account_detail = models.CharField(max_length=120, help_text='Account number or PayPal email')
    bank_name = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class RedeemCode(models.Model):
    code = models.CharField(max_length=30, unique=True)
    bonus = models.PositiveIntegerField()
    is_active = models.BooleanField(default=True)
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
        if not self.is_active or (self.expires_at and self.expires_at <= timezone.now()):
            return False
        return self.max_uses is None or self.uses < self.max_uses

    def __str__(self):
        return self.code
