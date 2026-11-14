import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


def word_count(text: str) -> int:
    text = re.sub(r'<[^>]+>', ' ', text or '')
    return len(text.split())


class Genre(models.Model):
    name = models.CharField(max_length=40, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    # Genres shown as Discover / Rank tabs in the app.
    show_in_app = models.BooleanField(default=True)
    # Authors can choose it for new books; retired genres stay on old books.
    is_active = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def save(self, *args, **kwargs):
        self.slug = self.slug or slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Tag(models.Model):
    GROUP_STYLE, GROUP_CHARACTER, GROUP_SETTING = 'style', 'character', 'setting'
    GROUP_CHOICES = [(GROUP_STYLE, 'Style'), (GROUP_CHARACTER, 'Character'), (GROUP_SETTING, 'Setting')]

    name = models.CharField(max_length=40, unique=True)
    group = models.CharField(max_length=10, choices=GROUP_CHOICES, default=GROUP_STYLE)

    class Meta:
        ordering = ['group', 'name']

    def __str__(self):
        return self.name


class Contest(models.Model):
    """Writing contest authors can enter from Novel Information."""

    title = models.CharField(max_length=120)
    brief = models.TextField(blank=True)
    prize = models.CharField(max_length=30)
    deadline = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['deadline']

    def __str__(self):
        return self.title


class StoryQuerySet(models.QuerySet):
    def visible_in_app(self):
        """Signed, live stories. Unsigned books only show on the web, and
        novels need MIN_LIVE_CHAPTERS published chapters (shorts don't)."""
        long_enough = (
            Chapter.objects.filter(status=Chapter.STATUS_PUBLISHED).values('story')
            .annotate(n=models.Count('pk')).filter(n__gte=settings.BONOVELA['MIN_LIVE_CHAPTERS']).values('story')
        )
        return (self.filter(is_signed=True, is_removed=False).exclude(status=Story.STATUS_DRAFT)
                .filter(models.Q(is_short=True) | models.Q(pk__in=long_enough)))


class Story(models.Model):
    STATUS_DRAFT, STATUS_ONGOING, STATUS_COMPLETED = 'draft', 'ongoing', 'completed'
    STATUS_CHOICES = [(STATUS_DRAFT, 'Draft'), (STATUS_ONGOING, 'Ongoing'), (STATUS_COMPLETED, 'Completed')]

    AUDIENCE_CHOICES = [('female', 'Female'), ('male', 'Male')]
    RATING_CHOICES = [('4+', '4+'), ('12+', '12+'), ('16+', '16+'), ('18+', '18+')]
    TYPE_CHOICES = [('original', 'Original'), ('fan_fiction', 'Fan-fiction'), ('translation', 'Translation')]
    LANGUAGES = ['English']  # more once Bonovela publishes in other languages

    # Content-rating explanations shown in Novel Information.
    RATING_INFO = {
        '4+': ('All', 'Content for readers of all ages.'),
        '12+': ('Youth and above', 'Light violence, kissing, light cursing, but not too explicit.'),
        '16+': ('Mature', 'Violence, sex, alcohol or gambling, but not too explicitly. May occur in limited chapters.'),
        '18+': ('Explicit', 'Content involving sex or violence taking up to 20% of the book.'),
    }

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='stories')
    title = models.CharField(max_length=60, blank=True)
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    synopsis = models.TextField(blank=True)
    cover = models.ImageField(upload_to='covers/', blank=True)
    # Recommended-cover palette index (0–7) when no image is uploaded; -1 = none.
    cover_index = models.SmallIntegerField(default=-1)
    language = models.CharField(max_length=30, default='English')
    audience = models.CharField(max_length=10, choices=AUDIENCE_CHOICES, blank=True)
    content_rating = models.CharField(max_length=4, choices=RATING_CHOICES, blank=True)
    novel_type = models.CharField(max_length=15, choices=TYPE_CHOICES, default='original')
    genre = models.ForeignKey(Genre, on_delete=models.SET_NULL, null=True, blank=True, related_name='stories')
    tags = models.ManyToManyField(Tag, blank=True, related_name='stories')
    contests = models.ManyToManyField(Contest, blank=True, related_name='entries')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    is_short = models.BooleanField(default=False)

    # Editorial state
    is_signed = models.BooleanField(default=False)
    signed_at = models.DateTimeField(null=True, blank=True)
    is_removed = models.BooleanField(default=False)
    # Senior Editor responsible for this book.
    editor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='edited_stories')
    assigned_at = models.DateTimeField(null=True, blank=True)
    needs_intervention = models.BooleanField(default=False, help_text='Flagged by an editor for urgent attention')
    intervention_note = models.CharField(max_length=200, blank=True)

    # Counters (denormalised for listing speed)
    views = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = StoryQuerySet.as_manager()

    class Meta:
        ordering = ['-updated_at']
        verbose_name_plural = 'stories'

    @property
    def book_code(self):
        """The Book ID Marketing uses in campaigns and links."""
        return f'BV-{1000 + self.pk}'

    def __str__(self):
        return self.display_title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title) or 'story'
            slug, n = base, 2
            while Story.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f'{base}-{n}', n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def display_title(self):
        return self.title or 'Unnamed'

    # Novel Information completeness (app step 1 + step 2).
    @property
    def has_basics(self):
        return bool(self.title.strip() and (self.cover or self.cover_index >= 0) and self.audience and self.content_rating)

    @property
    def synopsis_ok(self):
        lo, hi = settings.BONOVELA['SYNOPSIS_WORDS']
        return lo <= word_count(self.synopsis) <= hi

    @property
    def has_details(self):
        return bool(self.genre_id and self.synopsis_ok and self.pk and self.tags.exists())

    @property
    def is_info_complete(self):
        return self.has_basics and self.has_details

    def published_chapters(self):
        return self.chapters.filter(status=Chapter.STATUS_PUBLISHED)

    @property
    def total_words(self):
        return sum(c.word_count for c in self.published_chapters())

    @property
    def can_apply_for_contract(self):
        return self.is_info_complete and self.total_words >= settings.BONOVELA['CONTRACT_MIN_WORDS']


class Chapter(models.Model):
    STATUS_DRAFT = 'draft'
    STATUS_PUBLISHED = 'published'
    STATUS_SCHEDULED = 'scheduled'  # auto-publish at [scheduled_for]
    STATUS_REVISION = 'revision'  # sent back by an editor
    STATUS_TRASH = 'trash'
    STATUS_REMOVED = 'removed'  # taken down by an editor
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_SCHEDULED, 'Scheduled'),
        (STATUS_PUBLISHED, 'Published'),
        (STATUS_REVISION, 'Needs revision'),
        (STATUS_TRASH, 'Trash'),
        (STATUS_REMOVED, 'Removed'),
    ]

    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='chapters')
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=120, blank=True)
    content = models.TextField(blank=True)
    note = models.TextField(blank=True, help_text="Author's note shown after the chapter.")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT, db_index=True)
    previous_status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    word_count = models.PositiveIntegerField(default=0)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    # Editorial chapter review (after publishing).
    REVIEW_PENDING, REVIEW_APPROVED, REVIEW_FLAGGED = 'pending', 'approved', 'flagged'
    REVIEW_CHOICES = [(REVIEW_PENDING, 'Pending review'), (REVIEW_APPROVED, 'Approved'), (REVIEW_FLAGGED, 'Flagged')]
    review_status = models.CharField(max_length=10, choices=REVIEW_CHOICES, default=REVIEW_PENDING, db_index=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['story', 'number']
        unique_together = ('story', 'number')

    def __str__(self):
        return f'{self.story} · {self.label}'

    def save(self, *args, **kwargs):
        self.word_count = word_count(self.content)
        super().save(*args, **kwargs)

    @property
    def label(self):
        return f'{self.number:03d}'

    @property
    def display_title(self):
        return self.title or f'Chapter {self.number}'

    @property
    def is_free(self):
        rules = settings.BONOVELA
        limit = rules['FREE_SHORT_CHAPTERS'] if self.story.is_short else rules['FREE_CHAPTERS']
        return self.number <= limit

    @property
    def price(self):
        rules = settings.BONOVELA
        return 0 if self.is_free else (rules['SHORT_CHAPTER_PRICE'] if self.story.is_short else rules['CHAPTER_PRICE'])

    @staticmethod
    def length_error(words: int, publishing=True):
        """Why a chapter of [words] can't be saved (over the maximum) or
        published (outside the range), or ''."""
        lo, hi = settings.BONOVELA['CHAPTER_WORDS']
        if words > hi or (publishing and words < lo):
            return f'Chapters must be {lo:,}–{hi:,} words. This one has {words:,}.'
        return ''

    def publish(self):
        """Raises ValidationError when the chapter is outside the word range."""
        error = self.length_error(word_count(self.content))
        if error:
            raise ValidationError(error)
        self.status = self.STATUS_PUBLISHED
        self.published_at = self.published_at or timezone.now()
        self.scheduled_for = None
        self.save()
        if self.story.status == Story.STATUS_DRAFT:
            self.story.status = Story.STATUS_ONGOING
            self.story.save(update_fields=['status', 'updated_at'])

    def move_to_trash(self):
        self.previous_status = self.status
        self.status = self.STATUS_TRASH
        self.save()

    def restore(self):
        self.status = self.previous_status if self.previous_status != self.STATUS_TRASH else self.STATUS_DRAFT
        self.save()
