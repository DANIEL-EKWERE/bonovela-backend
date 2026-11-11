import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models
from django.utils import timezone
from django.utils.functional import LazyObject

User = settings.AUTH_USER_MODEL


class _PrivateStorage(LazyObject):
    """Uploads outside MEDIA_ROOT, so they're never served publicly."""

    def _setup(self):
        self._wrapped = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT, base_url=None)


_private = _PrivateStorage()


def private_storage():
    # A callable, so migrations record this function, not a machine's path.
    return _private


def _id_upload(instance, filename):
    ext = (filename.rsplit('.', 1)[-1] if '.' in filename else 'jpg').lower()[:5]
    return f'id-documents/{uuid.uuid4().hex}.{ext}'


class ContractApplication(models.Model):
    """An author applying to sign a story.

    Flow: author applies → SE reviews (approve / revision / reject) →
    rejects → CE sends contract → author signs → story is signed and appears
    in the app.
    """

    STATUS_PENDING = 'pending'  # waiting for the Senior Editor
    STATUS_REVISION = 'revision'  # sent back to the author
    STATUS_SE_APPROVED = 'se_approved'  # waiting for the CE to send the contract
    STATUS_CONTRACT_SENT = 'contract_sent'  # waiting for the author's signature
    STATUS_SIGNED = 'signed'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending review'),
        (STATUS_REVISION, 'Needs revision'),
        (STATUS_SE_APPROVED, 'Approved'),
        (STATUS_CONTRACT_SENT, 'Contract sent'),
        (STATUS_SIGNED, 'Signed'),
        (STATUS_REJECTED, 'Rejected'),
    ]
    OPEN_STATUSES = (STATUS_PENDING, STATUS_SE_APPROVED, STATUS_CONTRACT_SENT)

    CONTRACT_CHOICES = [('exclusive', 'Exclusive'), ('non_exclusive', 'Non-exclusive')]

    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='applications')
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    message = models.TextField(blank=True, help_text="Author's note to the editors")
    contract_type = models.CharField(max_length=15, choices=CONTRACT_CHOICES, default='non_exclusive')
    signing_bonus_cents = models.PositiveIntegerField(default=0)
    se = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    ce = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    feedback = models.TextField(blank=True, help_text='Shown to the author')
    submitted_ip = models.GenericIPAddressField(null=True, blank=True)
    # Who turned the book down (rejected, or sent back for revision) and why.
    rejected_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    # Set when this looks like a book that an editor turned down before:
    # '' (clean), 'rejected' or 'revision'. prior_matches keeps the evidence.
    FLAG_REJECTED, FLAG_REVISION = 'rejected', 'revision'
    prior_flag = models.CharField(max_length=10, blank=True, db_index=True)
    prior_matches = models.JSONField(default=list, blank=True)

    # Step 1 · legal name, ID and socials
    first_name = models.CharField(max_length=60, blank=True)
    middle_name = models.CharField(max_length=60, blank=True)
    surname = models.CharField(max_length=60, blank=True)
    id_front = models.ImageField(upload_to=_id_upload, storage=private_storage, blank=True)
    id_back = models.ImageField(upload_to=_id_upload, storage=private_storage, blank=True)
    facebook_url = models.URLField(blank=True)
    instagram_url = models.URLField(blank=True)
    # Step 2 · residential address and contact
    house_address = models.CharField(max_length=200, blank=True)
    street_name = models.CharField(max_length=120, blank=True)
    house_number = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=2, blank=True, help_text='ISO 3166 code')
    state = models.CharField(max_length=10, blank=True, help_text='ISO 3166-2 code')
    phone = models.CharField(max_length=24, blank=True, help_text='E.164, e.g. +2348012345678')
    is_minor = models.BooleanField(default=False)
    guardian_name = models.CharField(max_length=120, blank=True)
    guardian_phone = models.CharField(max_length=24, blank=True)
    guardian_email = models.EmailField(blank=True)
    guardian_address = models.CharField(max_length=200, blank=True)
    guardian_relationship = models.TextField(blank=True)
    # Step 3 · book
    chosen_editor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    character_details = models.TextField(blank=True)
    outline = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.story} · {self.get_status_display()}'

    @property
    def legal_name(self):
        return ' '.join(p for p in (self.first_name, self.middle_name, self.surname) if p)

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


def month_start(d=None):
    d = d or timezone.localdate()
    return d.replace(day=1)


class Target(models.Model):
    """Monthly target for an editor or a whole SE team. CEs set targets for
    SEs; SEs set them for their editors."""

    METRIC_CHOICES = [
        ('recruitment', 'Author recruitment'),
        ('contracts', 'Contracted books'),
        ('book_reviews', 'Completed book reviews'),
        ('chapter_review_pct', 'Chapter review efficiency (%)'),
        ('active_authors_pct', 'Active authors (%)'),
        ('quality_score', 'Team quality score (%)'),
    ]
    DEFAULTS = {'recruitment': 10, 'contracts': 5, 'book_reviews': 20, 'chapter_review_pct': 95,
                'active_authors_pct': 80, 'quality_score': 90}

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='targets')
    month = models.DateField(help_text='First day of the month')
    metric = models.CharField(max_length=20, choices=METRIC_CHOICES)
    value = models.PositiveIntegerField()
    set_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'month', 'metric')]


class Escalation(models.Model):
    """An issue an editor sends up the chain (SE → CE)."""

    CATEGORY_CHOICES = [
        ('complaint', 'Difficult author complaint'),
        ('misconduct', 'Editor misconduct'),
        ('contract', 'Contract disagreement'),
        ('plagiarism', 'Plagiarism case'),
        ('suspicious', 'Suspicious account activity'),
        ('intervention', 'Book needs special intervention'),
        ('payment', 'Payment complaint'),
        ('content', 'Serious content issue'),
        ('other', 'Other'),
    ]
    STATUS_OPEN, STATUS_RESOLVED = 'open', 'resolved'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_RESOLVED, 'Resolved')]

    raised_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='escalations')
    category = models.CharField(max_length=15, choices=CATEGORY_CHOICES)
    subject = models.CharField(max_length=140)
    body = models.TextField()
    story = models.ForeignKey('catalog.Story', on_delete=models.SET_NULL, null=True, blank=True)
    about_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    is_confidential = models.BooleanField(default=False, help_text='Only the Chief Editor can read it')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', '-created_at']


class Recommendation(models.Model):
    """Something an SE (or editor) can only recommend; the CE decides."""

    KIND_CHOICES = [
        ('suspend_book', 'Suspend book'),
        ('transfer_out', 'Transfer book outside the team'),
        ('promote_author', 'Promote author'),
        ('approve_contract', 'Approve contract'),
    ]
    STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED = 'pending', 'approved', 'rejected'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_APPROVED, 'Approved'), (STATUS_REJECTED, 'Rejected')]

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, null=True, blank=True)
    about_user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    target_editor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
                                      help_text='For transfers: who should take the book')
    note = models.TextField(blank=True)
    recommended_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='recommendations')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['status', '-created_at']


class TeamMessage(models.Model):
    """Internal editorial messages. No recipient + a team lead = announcement
    to that lead's team; neither = message to every editor (CE only)."""

    KIND_CHOICES = [
        ('announcement', 'Announcement'),
        ('message', 'Private message'),
        ('guideline', 'Editorial guideline'),
        ('target', 'Monthly targets'),
        ('feedback', 'Performance feedback'),
        ('instruction', 'Instruction'),
    ]

    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_team_messages')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True,
                                  related_name='team_messages')
    team_lead = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='+',
                                  help_text='Announcement to this SE\'s team')
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default='message')
    title = models.CharField(max_length=140)
    body = models.TextField()
    read_by = models.ManyToManyField(User, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class CompletionApplication(models.Model):
    """Author applies for a completion review (and Completion Bonus) once a
    book is finished. Editors run the manuscript analysis and recommend; the
    CE makes the final call."""

    STATUS_PENDING = 'pending'
    STATUS_RECOMMENDED = 'recommended'
    STATUS_CHANGES = 'changes'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Waiting for review'),
        (STATUS_RECOMMENDED, 'Recommended to CE'),
        (STATUS_CHANGES, 'Needs changes'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='completion_applications')
    message = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    analysis = models.JSONField(default=dict, blank=True)
    ai_review = models.TextField(blank=True)
    analysed_at = models.DateTimeField(null=True, blank=True)
    reviewer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    reviewer_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    bonus_cents = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']


class BookTransfer(models.Model):
    """History of book assignments."""

    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='transfers')
    from_editor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    to_editor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ── Curated shelves ──────────────────────────────────────────────────────────
class ShelfCategory(models.Model):
    """A heading in the app that holds shelves: a Discover tab for novels
    (Romance, Mafia, …) or a group in Short Stories (Love at First Sight, …)."""

    KIND_NOVEL, KIND_SHORT = 'novel', 'short'
    KIND_CHOICES = [(KIND_NOVEL, 'Novels'), (KIND_SHORT, 'Short Stories')]

    kind = models.CharField(max_length=5, choices=KIND_CHOICES)
    name = models.CharField(max_length=60)
    # Novel categories fill empty shelves from these genres.
    genres = models.ManyToManyField('catalog.Genre', blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['kind', 'order', 'pk']
        verbose_name_plural = 'shelf categories'

    def __str__(self):
        return f'{self.get_kind_display()} › {self.name}'


class Shelf(models.Model):
    """A named row of books inside a category. The Chief Editor renames,
    reorders and fills shelves; editors suggest books for them."""

    LAYOUT_CHOICES = [
        ('cover_grid', 'Cover grid (4 per row)'),
        ('cover_row', 'Cover row (swipe)'),
        ('paged_list', 'Paged list: cover, title, blurb (swipe)'),
        ('compact_grid', 'Compact grid'),
        ('detail_list', 'List with synopsis'),
        ('rated_list', 'List with rating'),
    ]

    category = models.ForeignKey(ShelfCategory, on_delete=models.CASCADE, related_name='shelves')
    title = models.CharField(max_length=60)
    layout = models.CharField(max_length=20, choices=LAYOUT_CHOICES, default='cover_row')
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['category', 'order', 'pk']

    def __str__(self):
        return self.title


class ShelfBook(models.Model):
    """A book on a shelf. Editors' picks wait for the Chief Editor."""

    STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED = 'pending', 'approved', 'rejected'
    STATUS_CHOICES = [(STATUS_PENDING, 'Pending'), (STATUS_APPROVED, 'Approved'), (STATUS_REJECTED, 'Rejected')]

    shelf = models.ForeignKey(Shelf, on_delete=models.CASCADE, related_name='entries')
    story = models.ForeignKey('catalog.Story', on_delete=models.CASCADE, related_name='shelf_entries')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    order = models.PositiveSmallIntegerField(default=0)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='+')
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'pk']
        constraints = [models.UniqueConstraint(fields=['shelf', 'story'], name='unique_book_per_shelf')]
