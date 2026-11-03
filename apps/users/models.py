from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class BonovelaUserManager(UserManager):
    """Email is the login; username is derived from it when omitted."""

    def create_user(self, username=None, email=None, password=None, **extra):
        if not username:
            username = (email or '').split('@')[0] or None
        return super().create_user(username, email, password, **extra)

    def create_superuser(self, username=None, email=None, password=None, **extra):
        if not username:
            username = (email or '').split('@')[0] or None
        extra.setdefault('role', User.ROLE_ADMIN)
        return super().create_superuser(username, email, password, **extra)


class User(AbstractUser):
    ROLE_READER = 'reader'
    ROLE_AUTHOR = 'author'
    ROLE_AE = 'ae'  # Assistant Editor: first-line chapter review
    ROLE_SE = 'se'  # Senior Editor: approves books, requests promotions
    ROLE_CE = 'ce'  # Chief Editor: contracts, promotions, oversees editors
    ROLE_ADMIN = 'admin'
    ROLE_CHOICES = [
        (ROLE_READER, 'Reader'),
        (ROLE_AUTHOR, 'Author'),
        (ROLE_AE, 'Assistant Editor'),
        (ROLE_SE, 'Senior Editor'),
        (ROLE_CE, 'Chief Editor'),
        (ROLE_ADMIN, 'Admin'),
    ]
    EDITOR_ROLES = (ROLE_AE, ROLE_SE, ROLE_CE)

    PROVIDER_CHOICES = [('email', 'Email'), ('google', 'Google'), ('apple', 'Apple'), ('facebook', 'Facebook')]

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_READER, db_index=True)
    pen_name = models.CharField(max_length=60, blank=True)
    about = models.TextField(blank=True, max_length=500)
    avatar = models.ImageField(upload_to='avatars/', blank=True)
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES, default='email')
    email_verified = models.BooleanField(default=False)
    # Onboarding preference from the app ("Choose a type").
    reader_type = models.CharField(max_length=10, blank=True, choices=[('female', 'Female'), ('male', 'Male')])
    language = models.CharField(max_length=30, default='English')
    is_suspended = models.BooleanField(default=False)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    objects = BonovelaUserManager()

    class Meta:
        ordering = ['-date_joined']

    def __str__(self):
        return self.display_name

    @property
    def display_name(self):
        return self.pen_name or self.get_full_name() or self.username

    @property
    def is_author(self):
        return self.role == self.ROLE_AUTHOR

    @property
    def is_editor(self):
        return self.role in self.EDITOR_ROLES

    @property
    def is_platform_admin(self):
        return self.role == self.ROLE_ADMIN or self.is_superuser


class Follow(models.Model):
    """A reader following an author."""

    follower = models.ForeignKey(User, on_delete=models.CASCADE, related_name='following_set')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='follower_set')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('follower', 'author')

    def __str__(self):
        return f'{self.follower} → {self.author}'
