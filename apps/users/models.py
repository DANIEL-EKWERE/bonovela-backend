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
    ROLE_SE = 'se'  # Senior Editor: manages books and authors, first contract review, requests promotions
    ROLE_CE = 'ce'  # Chief Editor: contracts, promotions, oversees editors
    ROLE_CREATOR = 'creator'  # Drama Creator: uploads short dramas
    ROLE_MARKETING = 'marketing'  # banners, promo codes, broadcasts
    ROLE_FINANCE = 'finance'  # revenue reports and author payouts
    ROLE_ADMIN = 'admin'
    ROLE_CHOICES = [
        (ROLE_READER, 'Reader'),
        (ROLE_AUTHOR, 'Author'),
        (ROLE_SE, 'Senior Editor'),
        (ROLE_CE, 'Chief Editor'),
        (ROLE_CREATOR, 'Drama Creator'),
        (ROLE_MARKETING, 'Marketing'),
        (ROLE_FINANCE, 'Finance'),
        (ROLE_ADMIN, 'Admin'),
    ]
    EDITOR_ROLES = (ROLE_SE, ROLE_CE)

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
    # Editorial team: a Senior Editor's supervisor is the Chief Editor.
    supervisor = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='team')
    # Editor who brought this author to Bonovela.
    recruited_by = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='recruits')
    kyc_verified = models.BooleanField(default=False, help_text='Identity checked for contract payments')
    # Senior Editors work on one desk; authors choose an editor from the desk
    # matching their book when they apply for a contract.
    DESK_NOVELS, DESK_SHORTS = 'novels', 'shorts'
    DESK_CHOICES = [(DESK_NOVELS, 'Novels'), (DESK_SHORTS, 'Short Stories')]
    desk = models.CharField(max_length=6, choices=DESK_CHOICES, default=DESK_NOVELS)

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
    def is_drama_creator(self):
        return self.role == self.ROLE_CREATOR

    @property
    def is_marketing(self):
        return self.role == self.ROLE_MARKETING

    @property
    def is_finance(self):
        return self.role == self.ROLE_FINANCE

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


class IPLog(models.Model):
    """IP addresses an account has used (sign-up, sign-in, book submissions,
    dashboard visits). Editors use it to spot one author behind several
    accounts."""

    EVENT_CHOICES = [('signup', 'Sign-up'), ('login', 'Sign-in'), ('submit', 'Book submission'), ('visit', 'Visit')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ip_logs')
    ip = models.GenericIPAddressField()
    first_event = models.CharField(max_length=10, choices=EVENT_CHOICES)
    last_event = models.CharField(max_length=10, choices=EVENT_CHOICES)
    user_agent = models.CharField(max_length=255, blank=True)
    hits = models.PositiveIntegerField(default=1)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'ip')]
        ordering = ['-last_seen']

    def __str__(self):
        return f'{self.user} · {self.ip}'
