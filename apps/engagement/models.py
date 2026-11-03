from django.conf import settings
from django.db import models

User = settings.AUTH_USER_MODEL


class LibraryEntry(models.Model):
    """A book on a reader's shelf."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='library')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='shelved_by')
    is_recommended = models.BooleanField(default=False, help_text='Added by onboarding')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'story')
        ordering = ['-created_at']
        verbose_name_plural = 'library entries'


class ReadingProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='progress')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='progress')
    chapter_number = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'story')
        ordering = ['-updated_at']


class ReadingTime(models.Model):
    """Minutes read per day — drives the daily reading rewards."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reading_time')
    date = models.DateField()
    minutes = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('user', 'date')


class Review(models.Model):
    """A reader's rating + comment on a book (app "Comments" / "Reviews")."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='reviews')
    chapter = models.ForeignKey('catalog.Chapter', on_delete=models.SET_NULL, null=True, blank=True)
    stars = models.PositiveSmallIntegerField(default=5)
    text = models.TextField(max_length=2000)
    likes = models.ManyToManyField(User, blank=True, related_name='liked_reviews')
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ReviewReply(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='replies')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    text = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name_plural = 'review replies'


class InboxMessage(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='inbox')
    title = models.CharField(max_length=120)
    body = models.TextField()
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class CheckIn(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='checkins')
    date = models.DateField()
    streak_day = models.PositiveSmallIntegerField()  # 1–7
    bonus = models.PositiveIntegerField()

    class Meta:
        unique_together = ('user', 'date')
        ordering = ['-date']


class TaskClaim(models.Model):
    """A completed reward task. Daily tasks repeat per [date]; one-off tasks
    are stored with date = None."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='task_claims')
    key = models.CharField(max_length=40)
    date = models.DateField(null=True, blank=True)
    bonus = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)


class Report(models.Model):
    """Reader report on a book or an author."""

    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports_made')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, null=True, blank=True)
    reported_user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='reports')
    reason = models.CharField(max_length=200)
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['is_resolved', '-created_at']
