"""Marketing: promotions (banners, pop-ups, opening screens, Discover
placements), broadcasts, campaign links, ad campaigns imported from the ad
platforms, reader attribution, settings, team and audit log. Promo codes
reuse wallet.RedeemCode."""

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class BannerQuerySet(models.QuerySet):
    def live(self, placement=None):
        now = timezone.now()
        qs = self.filter(is_active=True, is_draft=False, starts_at__lte=now).filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now))
        return qs.filter(placement=placement) if placement else qs


class Banner(models.Model):
    PLACEMENT_CHOICES = [
        ('discover', 'Discover (top carousel)'),
        ('free', 'Free tab (top carousel)'),
        ('drama', 'Drama tab'),
        ('store', 'Top Up / Store'),
        ('rewards', 'Rewards'),
        # Shown once per device when the app opens: image, title and a View More button.
        ('popup', 'Pop-up when the app opens'),
        # Full-screen image before the app loads, with a Skip countdown and View More.
        ('splash', 'Open-screen ad (full screen)'),
    ]
    TARGET_CHOICES = [('story', 'Book'), ('drama', 'Drama'), ('url', 'Link'), ('store', 'Top Up page'),
                      ('plus', 'Plus subscription')]

    title = models.CharField(max_length=80)
    subtitle = models.CharField(max_length=120, blank=True)
    image = models.ImageField(upload_to='banners/', blank=True)
    color_index = models.SmallIntegerField(default=0, help_text='Background palette 0–7 when there is no image')
    placement = models.CharField(max_length=10, choices=PLACEMENT_CHOICES, default='discover')
    target = models.CharField(max_length=10, choices=TARGET_CHOICES, default='story')
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True)
    drama = models.ForeignKey('drama.Drama', on_delete=models.SET_NULL, null=True, blank=True)
    url = models.URLField(blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    # Promotion Centre: who sees it and, for Discover, the card's label.
    audience = models.CharField(max_length=40, default='all', help_text='Reader segment (marketing.segments)')
    label = models.CharField(max_length=30, blank=True, help_text="Discover card label, e.g. Trending, Editor's pick")
    condition = models.CharField(max_length=12, default='open', choices=[
        ('open', 'When the app opens'), ('chapter', 'After finishing a chapter'), ('book', 'After finishing a book'),
    ], help_text='Pop-ups only')
    is_draft = models.BooleanField(default=False)
    impressions = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    book_opens = models.PositiveIntegerField(default=0, help_text='Clicks that opened the advertised book')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = BannerQuerySet.as_manager()

    class Meta:
        ordering = ['placement', 'order', '-created_at']

    def __str__(self):
        return self.title

    @property
    def ctr(self):
        return round(self.clicks * 100 / self.impressions, 1) if self.impressions else 0

    @property
    def is_live(self):
        now = timezone.now()
        return self.is_active and not self.is_draft and self.starts_at <= now and (self.ends_at is None or self.ends_at > now)

    @property
    def code(self):
        return f'PRM-{self.pk:04d}'

    @property
    def kind_label(self):
        return {'discover': 'Discovery page', 'popup': 'Pop-up', 'splash': 'Opening screen'}.get(
            self.placement, f'Banner · {self.get_placement_display().split(" (")[0]}')

    @property
    def status(self):
        now = timezone.now()
        if self.is_draft:
            return 'draft'
        if self.ends_at and self.ends_at <= now:
            return 'expired'
        if not self.is_active:
            return 'paused'
        return 'scheduled' if self.starts_at > now else 'active'


class Broadcast(models.Model):
    """A message sent to every user in an audience; lands in their inbox.
    Notification promotions are broadcasts of kind 'notification'. Push and
    email go out once those services are connected; until then every
    message is delivered in the app's inbox."""

    KIND_CHOICES = [('broadcast', 'Broadcast'), ('notification', 'Notification promotion')]
    STATUS_CHOICES = [('sent', 'Sent'), ('scheduled', 'Scheduled'), ('cancelled', 'Cancelled')]
    CHANNELS = [('inapp', 'In-app'), ('push', 'Push'), ('email', 'Email')]

    AUDIENCE_CHOICES = [
        ('all', 'Everyone'),
        ('readers', 'Readers'),
        ('authors', 'Authors'),
        ('plus', 'Plus members'),
        ('lapsed', 'Not active for 14 days'),
        ('new', 'Joined in the last 7 days'),
    ]

    title = models.CharField(max_length=120)
    body = models.TextField()
    audience = models.CharField(max_length=10, choices=AUDIENCE_CHOICES, default='all')
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True,
                              help_text='Optional book to link')
    bonus = models.PositiveIntegerField(default=0, help_text='Bonus coins added for every recipient')
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default='broadcast')
    name = models.CharField(max_length=120, blank=True, help_text='Internal name')
    segment = models.CharField(max_length=40, blank=True, help_text='Reader segment; overrides audience when set')
    country = models.CharField(max_length=60, blank=True)
    stories = models.ManyToManyField('catalog.Story', blank=True, related_name='+', help_text='Books to feature')
    image = models.ImageField(upload_to='broadcasts/', blank=True)
    channels = models.CharField(max_length=30, default='inapp')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='sent')
    scheduled_at = models.DateTimeField(null=True, blank=True)
    recipients = models.PositiveIntegerField(default=0)
    excluded = models.PositiveIntegerField(default=0, help_text='Left out by consent or frequency limits')
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return self.title

    @property
    def code(self):
        return f'BRC-{self.pk:04d}'

    @property
    def channel_list(self):
        return [c for c in self.channels.split(',') if c]


class MarketingSettings(models.Model):
    """Segment rules and frequency caps. One row."""

    paying_lapse_days = models.PositiveSmallIntegerField(default=30, help_text='Former payer: no purchase for this long')
    active_days = models.PositiveSmallIntegerField(default=7)
    inactive_days = models.PositiveSmallIntegerField(default=14)
    new_days = models.PositiveSmallIntegerField(default=7)
    stopped_chapters = models.PositiveSmallIntegerField(default=3, help_text='Stopped reading: fewer chapters than this')
    popup_per_day = models.PositiveSmallIntegerField(default=1)
    splash_per_day = models.PositiveSmallIntegerField(default=1)
    push_per_week = models.PositiveSmallIntegerField(default=3)
    quiet_start = models.PositiveSmallIntegerField(default=22, help_text='Quiet hours start (0–23)')
    quiet_end = models.PositiveSmallIntegerField(default=8)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls):
        return cls.objects.first() or cls.objects.create()


class MarketingMember(models.Model):
    """Someone allowed into Bonovela Marketing, and what they may do."""

    ROLE_MANAGER, ROLE_PROMOTIONS, ROLE_ANALYST, ROLE_VIEWER = 'manager', 'promotions', 'analyst', 'viewer'
    ROLE_CHOICES = [(ROLE_MANAGER, 'Marketing Manager'), (ROLE_PROMOTIONS, 'Promotions'),
                    (ROLE_ANALYST, 'Analyst'), (ROLE_VIEWER, 'Viewer')]
    PERMISSIONS = {
        'view_all': ('View all reports', {ROLE_MANAGER, ROLE_PROMOTIONS, ROLE_ANALYST, ROLE_VIEWER}),
        'promote': ('Create and edit promotions', {ROLE_MANAGER, ROLE_PROMOTIONS}),
        'broadcast': ('Send broadcasts', {ROLE_MANAGER, ROLE_PROMOTIONS}),
        'codes': ('Manage promo codes', {ROLE_MANAGER, ROLE_PROMOTIONS}),
        'import': ('Import ad data', {ROLE_MANAGER, ROLE_ANALYST}),
        'export': ('Export reports', {ROLE_MANAGER, ROLE_ANALYST}),
        'settings': ('Change settings and integrations', {ROLE_MANAGER}),
        'team': ('Manage the team', {ROLE_MANAGER}),
    }

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='marketing_member')
    role = models.CharField(max_length=12, choices=ROLE_CHOICES, default=ROLE_PROMOTIONS)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    last_active = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['role', 'user__email']

    def can(self, permission):
        return self.role in self.PERMISSIONS[permission][1]


class MarketingAudit(models.Model):
    """Every marketing change, written once and never changed."""

    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    actor_label = models.CharField(max_length=120)
    action = models.CharField(max_length=160)
    category = models.CharField(max_length=20)
    reference = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionDenied('Audit entries are locked once written.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionDenied('Audit entries are locked once written.')


PLATFORM_CHOICES = [('meta', 'Meta Ads'), ('google', 'Google Ads'), ('tiktok', 'TikTok Ads'), ('apple', 'Apple Ads')]
SOURCE_CHOICES = PLATFORM_CHOICES + [('organic', 'Organic'), ('referral', 'Referral'), ('other', 'Other')]


class Integration(models.Model):
    """An ad platform or measurement partner. Shown as Not connected until
    a real, authorised connection syncs data (connected_at is set only by
    that sync)."""

    KEY_CHOICES = PLATFORM_CHOICES + [('mmp', 'Mobile Measurement Partner'), ('push', 'Push notifications'),
                                      ('email', 'Email delivery')]

    key = models.CharField(max_length=10, choices=KEY_CHOICES, unique=True)
    account_id = models.CharField(max_length=80, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    last_sync = models.DateTimeField(null=True, blank=True)
    error_log = models.TextField(blank=True)
    requested_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    requested_at = models.DateTimeField(null=True, blank=True)

    @property
    def connected(self):
        return bool(self.connected_at)


class AdCampaign(models.Model):
    """A campaign on an ad platform, tied to the Book ID it advertises.
    Figures arrive by CSV import until the platform is connected."""

    STATUS_CHOICES = [('active', 'Active'), ('paused', 'Paused'), ('ended', 'Ended')]

    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    code = models.CharField(max_length=40, unique=True, help_text='e.g. META-2026-0011')
    name = models.CharField(max_length=120)
    channel = models.CharField(max_length=40, blank=True, help_text='Facebook, Search, In-Feed…')
    ad_set = models.CharField(max_length=120, blank=True)
    ad_name = models.CharField(max_length=120, blank=True)
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True, related_name='ad_campaigns')
    budget_cents = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['platform', 'code']

    def __str__(self):
        return self.code


class AdStat(models.Model):
    """One day of one campaign."""

    campaign = models.ForeignKey(AdCampaign, on_delete=models.CASCADE, related_name='stats')
    date = models.DateField()
    spend_cents = models.PositiveIntegerField(default=0)
    impressions = models.PositiveIntegerField(default=0)
    reach = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    installs = models.PositiveIntegerField(default=0)
    registrations = models.PositiveIntegerField(default=0)
    book_opens = models.PositiveIntegerField(default=0)
    paying = models.PositiveIntegerField(default=0)
    revenue_cents = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('campaign', 'date')
        ordering = ['-date']


class ReaderProfile(models.Model):
    """Where a reader came from and how they can be reached. Sent by the
    app on sign-up and launch."""

    PLATFORM_CHOICES = [('android', 'Android'), ('ios', 'iOS'), ('web', 'Web')]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='reader_profile')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='organic')
    campaign = models.CharField(max_length=40, blank=True)
    link = models.ForeignKey('CampaignLink', on_delete=models.SET_NULL, null=True, blank=True, related_name='readers')
    country = models.CharField(max_length=60, blank=True)
    platform = models.CharField(max_length=8, choices=PLATFORM_CHOICES, blank=True)
    app_version = models.CharField(max_length=20, blank=True)
    marketing_consent = models.BooleanField(default=True)
    push_consent = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class ReaderActivity(models.Model):
    """A reader used the app on this day (retention and active readers)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activity_days')
    date = models.DateField(db_index=True)

    class Meta:
        unique_together = ('user', 'date')


class CampaignLink(models.Model):
    """A book link for an ad: opens a preview page for that exact book, then
    the store, and the app opens the book after install."""

    PREVIEW_CHOICES = [(1, 'Chapter 1'), (5, 'Chapters 1–5')]

    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='campaign_links')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='meta')
    campaign = models.CharField(max_length=40, blank=True)
    preview = models.PositiveSmallIntegerField(choices=PREVIEW_CHOICES, default=1)
    clicks = models.PositiveIntegerField(default=0)
    previews = models.PositiveIntegerField(default=0, help_text='Preview chapters read')
    store_visits = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def code(self):
        return f'LNK-{self.pk + 499}'

    @property
    def installs(self):
        return self.readers.count()
