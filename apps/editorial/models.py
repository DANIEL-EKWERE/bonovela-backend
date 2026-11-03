from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


class ContractApplication(models.Model):
    """An author applying to sign a story.

    Flow: author applies → AE reviews (approve / revision) → SE approves or
    rejects → CE sends contract → author signs → story is signed and appears
    in the app.
    """

    STATUS_PENDING = 'pending'  # waiting for an AE
    STATUS_AE_APPROVED = 'ae_approved'  # waiting for an SE
    STATUS_REVISION = 'revision'  # sent back to the author
    STATUS_SE_APPROVED = 'se_approved'  # waiting for the CE to send the contract
    STATUS_CONTRACT_SENT = 'contract_sent'  # waiting for the author's signature
    STATUS_SIGNED = 'signed'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Under review'),
        (STATUS_AE_APPROVED, 'Passed first review'),
        (STATUS_REVISION, 'Needs revision'),
        (STATUS_SE_APPROVED, 'Approved'),
        (STATUS_CONTRACT_SENT, 'Contract sent'),
        (STATUS_SIGNED, 'Signed'),
        (STATUS_REJECTED, 'Rejected'),
    ]
    OPEN_STATUSES = (STATUS_PENDING, STATUS_AE_APPROVED, STATUS_SE_APPROVED, STATUS_CONTRACT_SENT)

    CONTRACT_CHOICES = [('exclusive', 'Exclusive'), ('non_exclusive', 'Non-exclusive')]

    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='applications')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    message = models.TextField(blank=True, help_text="Author's note to the editors")
    contract_type = models.CharField(max_length=15, choices=CONTRACT_CHOICES, default='non_exclusive')
    signing_bonus_cents = models.PositiveIntegerField(default=0)
    ae = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    se = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    ce = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    feedback = models.TextField(blank=True, help_text='Shown to the author')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.story} · {self.get_status_display()}'

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class EditorialNote(models.Model):
    """Internal note on a story or chapter, visible to editors only."""

    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='editorial_notes')
    chapter = models.ForeignKey('catalog.Chapter', on_delete=models.CASCADE, null=True, blank=True)
    editor = models.ForeignKey(User, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Promotion(models.Model):
    """A book placed in a promoted app section for a limited time. Only the
    CE creates these (directly or by approving an SE's request)."""

    SECTION_CHOICES = [
        ('featured', 'Featured banner'),
        ('editors_pick', "Editor's Pick"),
        ('trending', 'Trending'),
        ('new_arrival', 'New Arrival'),
        ('recommended', 'Recommended For You'),
        ('love_on_top', 'Love On Top'),
    ]

    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='promotions')
    section = models.CharField(max_length=20, choices=SECTION_CHOICES)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    request = models.ForeignKey('PromotionRequest', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-starts_at']

    @property
    def is_active(self):
        return self.starts_at <= timezone.now() < self.ends_at


class PromotionRequest(models.Model):
    """SE asks the CE to promote a book in one or more sections."""

    STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED = 'pending', 'approved', 'rejected'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_APPROVED, 'Approved'), (STATUS_REJECTED, 'Rejected')]

    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='promotion_requests')
    sections = models.JSONField(default=list, help_text='Promotion.SECTION_CHOICES keys')
    days = models.PositiveSmallIntegerField(default=7)
    reason = models.TextField()
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='promotion_requests')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', '-created_at']

    @property
    def section_labels(self):
        names = dict(Promotion.SECTION_CHOICES)
        return [names.get(s, s) for s in self.sections]
