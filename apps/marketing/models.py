"""Marketing: in-app banners and broadcast messages. Promo codes reuse
wallet.RedeemCode."""

from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class BannerQuerySet(models.QuerySet):
    def live(self, placement=None):
        now = timezone.now()
        qs = self.filter(is_active=True, starts_at__lte=now).filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now))
        return qs.filter(placement=placement) if placement else qs


class Banner(models.Model):
    PLACEMENT_CHOICES = [
        ('discover', 'Discover (top carousel)'),
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
    impressions = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
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
        return self.is_active and self.starts_at <= now and (self.ends_at is None or self.ends_at > now)


class Broadcast(models.Model):
    """A message sent to every user in an audience; lands in their inbox."""

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
    recipients = models.PositiveIntegerField(default=0)
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return self.title
