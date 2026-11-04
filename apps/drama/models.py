"""Short dramas: vertical video series, original or adapted from a novel."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

User = settings.AUTH_USER_MODEL


class DramaQuerySet(models.QuerySet):
    def visible_in_app(self):
        return self.filter(is_published=True, is_removed=False)


class Drama(models.Model):
    KIND_DRAMA, KIND_ADAPTED = 'drama', 'adapted'
    KIND_CHOICES = [(KIND_DRAMA, 'Drama'), (KIND_ADAPTED, 'Adapted')]

    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='dramas')
    title = models.CharField(max_length=80)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    synopsis = models.TextField(blank=True)
    cover = models.ImageField(upload_to='drama_covers/', blank=True)
    cover_index = models.SmallIntegerField(default=0, help_text='Palette 0–7 when there is no cover image')
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_DRAMA)
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='dramas', help_text='Novel this drama is adapted from')
    genre = models.ForeignKey('catalog.Genre', on_delete=models.SET_NULL, null=True, blank=True)
    tags = models.ManyToManyField('catalog.Tag', blank=True)
    is_completed = models.BooleanField(default=False)
    is_published = models.BooleanField(default=False)
    is_removed = models.BooleanField(default=False)
    plays = models.PositiveIntegerField(default=0)
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = DramaQuerySet.as_manager()

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title) or 'drama'
            slug, n = base, 2
            while Drama.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f'{base}-{n}', n + 1
            self.slug = slug
        self.kind = self.KIND_ADAPTED if self.story_id else self.KIND_DRAMA
        super().save(*args, **kwargs)

    @property
    def display_title(self):
        return self.title or 'Untitled'

    def published_episodes(self):
        return self.episodes.filter(status=Episode.STATUS_PUBLISHED)

    def publish(self):
        self.is_published = True
        self.released_at = self.released_at or timezone.now()
        self.save(update_fields=['is_published', 'released_at', 'kind', 'updated_at'])


class Episode(models.Model):
    STATUS_DRAFT, STATUS_PUBLISHED = 'draft', 'published'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_PUBLISHED, 'Published')]

    drama = models.ForeignKey(Drama, on_delete=models.CASCADE, related_name='episodes')
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=80, blank=True)
    video = models.FileField(upload_to='episodes/', blank=True)
    video_url = models.URLField(blank=True, help_text='Hosted video (CDN) when no file is uploaded')
    duration_seconds = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    plays = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['drama', 'number']
        unique_together = [('drama', 'number')]

    def __str__(self):
        return f'{self.drama} · EP {self.number}'

    @property
    def is_free(self):
        return self.number <= settings.BONOVELA['FREE_EPISODES']

    @property
    def price(self):
        return 0 if self.is_free else settings.BONOVELA['EPISODE_PRICE']

    @property
    def has_video(self):
        return bool(self.video or self.video_url)

    def video_src(self, request=None):
        if self.video:
            return request.build_absolute_uri(self.video.url) if request else self.video.url
        return self.video_url

    def publish(self):
        self.status = self.STATUS_PUBLISHED
        self.published_at = self.published_at or timezone.now()
        self.save(update_fields=['status', 'published_at'])


class EpisodeUnlock(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='episode_unlocks')
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name='unlocks')
    coins_paid = models.PositiveIntegerField(default=0)
    bonus_paid = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'episode')]


class DramaSave(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_dramas')
    drama = models.ForeignKey(Drama, on_delete=models.CASCADE, related_name='saves')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('user', 'drama')]


class WatchProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='drama_progress')
    drama = models.ForeignKey(Drama, on_delete=models.CASCADE, related_name='progress')
    episode_number = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'drama')]
