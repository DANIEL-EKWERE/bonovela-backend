from django import forms
from django.conf import settings
from django.utils import timezone

from apps.catalog.models import Chapter, Genre, Story, Tag, word_count
from apps.drama.models import Drama, Episode
from apps.marketing.models import Banner, Broadcast, CampaignLink
from apps.wallet.models import PayoutMethod, RedeemCode

VIDEO_TYPES = ('video/mp4', 'video/quicktime', 'video/webm', 'video/x-m4v')
MAX_VIDEO_MB = 500

LANGUAGES = Story.LANGUAGES


class StoryBasicsForm(forms.ModelForm):
    """Novel Information · step 1."""

    language = forms.ChoiceField(choices=[(x, x) for x in LANGUAGES])

    class Meta:
        model = Story
        fields = ['title', 'cover', 'cover_index', 'language', 'audience', 'content_rating']

    def clean_cover(self):
        f = self.cleaned_data.get('cover')
        if f and hasattr(f, 'content_type'):
            if f.content_type not in ('image/jpeg', 'image/png'):
                raise forms.ValidationError('Use a .jpg or .png image.')
            if f.size > 5 * 1024 * 1024:
                raise forms.ValidationError('Covers must be under 5 MB.')
        return f


class StoryDetailsForm(forms.ModelForm):
    """Novel Information · step 2."""

    genre = forms.ModelChoiceField(queryset=Genre.objects.filter(is_active=True), required=False)
    tags = forms.ModelMultipleChoiceField(queryset=Tag.objects.all(), required=False)
    is_short = forms.TypedChoiceField(choices=[('0', 'Novel'), ('1', 'Short Story')], coerce=lambda v: v == '1',
                                      label='Story Length')

    class Meta:
        model = Story
        fields = ['novel_type', 'is_short', 'genre', 'tags', 'synopsis']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['is_short'] = '1' if self.instance.is_short else '0'
        if self.instance.is_signed:  # pricing and shelves depend on it
            self.fields['is_short'].disabled = True

    def clean_tags(self):
        tags = self.cleaned_data['tags']
        if len(tags) > 10:
            raise forms.ValidationError('Choose up to 10 tags.')
        return tags

    def clean_synopsis(self):
        text = self.cleaned_data['synopsis']
        lo, hi = settings.BONOVELA['SYNOPSIS_WORDS']
        n = word_count(text)
        if not lo <= n <= hi:
            raise forms.ValidationError(f'Synopsis must be {lo}–{hi} words (now {n}).')
        return text


class ChapterForm(forms.ModelForm):
    class Meta:
        model = Chapter
        fields = ['title', 'content', 'note']

    def clean_content(self):
        # Drafts may be short; nothing may exceed the maximum, and a live
        # chapter must stay within the range.
        text = self.cleaned_data['content']
        error = Chapter.length_error(word_count(text), publishing=self.instance.status == Chapter.STATUS_PUBLISHED)
        if error:
            raise forms.ValidationError(error)
        return text


class PayoutForm(forms.ModelForm):
    # Set by the "Please check your information" dialog.
    confirmed = forms.BooleanField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = PayoutMethod
        fields = ['method', 'country', 'currency', 'bank_name', 'swift_code', 'account_detail', 'account_name',
                  'intermediary_swift', 'nickname', 'email', 'address', 'city', 'state', 'postal_code']

    def clean(self):
        data = super().clean()
        if data.get('method') == 'bank':
            for name in PayoutMethod.BANK_FIELDS:
                if not data.get(name):
                    self.add_error(name, 'Required for bank transfers.')
        if (data.get('address') or '').strip().upper().startswith(('PO BOX', 'P.O. BOX', 'GPO BOX')):
            self.add_error('address', 'Use a street address, not a PO or GPO box.')
        if not data.get('confirmed'):
            raise forms.ValidationError('Please check your information and confirm before submitting.')
        return data


class WithdrawalForm(PayoutForm):
    """The withdrawal page: the amount plus the payment account it goes to
    (saved as the author's payment info)."""

    amount = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2)


def _check_image(f):
    if f and hasattr(f, 'content_type'):
        if f.content_type not in ('image/jpeg', 'image/png', 'image/webp'):
            raise forms.ValidationError('Use a .jpg, .png or .webp image.')
        if f.size > 5 * 1024 * 1024:
            raise forms.ValidationError('Images must be under 5 MB.')
    return f


# ── Drama Studio ─────────────────────────────────────────────────────────────
class DramaForm(forms.ModelForm):
    story = forms.ModelChoiceField(queryset=Story.objects.filter(is_signed=True, is_removed=False).order_by('title'),
                                   required=False, empty_label='— Original drama —')
    genre = forms.ModelChoiceField(queryset=Genre.objects.all(), required=False)
    tags = forms.ModelMultipleChoiceField(queryset=Tag.objects.all(), required=False)
    cover_index = forms.IntegerField(required=False, min_value=-1, max_value=7)

    class Meta:
        model = Drama
        fields = ['title', 'cover', 'cover_index', 'story', 'genre', 'tags', 'synopsis', 'is_completed']

    def clean_cover(self):
        return _check_image(self.cleaned_data.get('cover'))

    def clean_cover_index(self):
        value = self.cleaned_data.get('cover_index')
        return self.instance.cover_index if value is None else value

    def clean_tags(self):
        tags = self.cleaned_data['tags']
        if len(tags) > 10:
            raise forms.ValidationError('Choose up to 10 tags.')
        return tags


class EpisodeForm(forms.ModelForm):
    class Meta:
        model = Episode
        fields = ['number', 'title', 'video', 'video_url', 'duration_seconds']

    def clean_video(self):
        f = self.cleaned_data.get('video')
        if f and hasattr(f, 'content_type'):
            if f.content_type not in VIDEO_TYPES:
                raise forms.ValidationError('Upload an .mp4, .mov or .webm video.')
            if f.size > MAX_VIDEO_MB * 1024 * 1024:
                raise forms.ValidationError(f'Videos must be under {MAX_VIDEO_MB} MB.')
        return f

    def clean_number(self):
        n = self.cleaned_data['number']
        clash = Episode.objects.filter(drama=self.instance.drama, number=n).exclude(pk=self.instance.pk)
        if n < 1 or clash.exists():
            raise forms.ValidationError(f'Episode {n} already exists.' if n >= 1 else 'Must be 1 or more.')
        return n


# ── Marketing ────────────────────────────────────────────────────────────────
class _BookChoice(forms.ModelChoiceField):
    def label_from_instance(self, story):
        return f'{story.display_title} · {story.book_code}'


def _when(required=True):
    return forms.DateTimeField(required=required, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))


class BannerForm(forms.ModelForm):
    """Every Marketing promotion shown in the app: Discover placements,
    banners, pop-ups and opening screens. [placements] limits where it
    can show (one page per kind)."""

    starts_at = _when()
    ends_at = _when(False)
    story = _BookChoice(queryset=Story.objects.none(), required=False)
    drama = forms.ModelChoiceField(queryset=Drama.objects.visible_in_app().order_by('title'), required=False)
    audience = forms.ChoiceField(choices=[])

    class Meta:
        model = Banner
        fields = ['title', 'subtitle', 'image', 'color_index', 'placement', 'target', 'story', 'drama', 'url',
                  'audience', 'label', 'condition', 'order', 'starts_at', 'ends_at', 'is_active', 'is_draft']

    def __init__(self, *args, placements=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.marketing.segments import choices
        # Built per form: app visibility depends on settings (chapter minimum).
        self.fields['story'].queryset = Story.objects.visible_in_app().order_by('title')
        self.fields['audience'].choices = choices()
        if placements:
            self.fields['placement'].choices = [c for c in Banner.PLACEMENT_CHOICES if c[0] in placements]

    def clean_image(self):
        return _check_image(self.cleaned_data.get('image'))

    def clean(self):
        data = super().clean()
        target = data.get('target')
        need = {'story': 'story', 'drama': 'drama', 'url': 'url'}.get(target)
        if need and not data.get(need):
            self.add_error(need, 'Every book promotion needs a valid Book ID.' if need == 'story' else 'Required for this target.')
        if data.get('ends_at') and data.get('starts_at') and data['ends_at'] <= data['starts_at']:
            self.add_error('ends_at', 'Must be after the start.')
        if data.get('placement') == 'splash' and not data.get('image') and not self.instance.image:
            self.add_error('image', 'An opening screen needs a full-screen cover image.')
        return data


class RedeemCodeForm(forms.ModelForm):
    days = forms.IntegerField(min_value=1, max_value=365, required=False, help_text='Leave blank for no expiry')
    starts_at = _when(False)
    segment = forms.ChoiceField(choices=[], required=False)

    class Meta:
        model = RedeemCode
        fields = ['code', 'reward', 'bonus', 'campaign', 'max_uses', 'starts_at', 'segment', 'countries']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.marketing.segments import choices
        self.fields['segment'].choices = [('', 'All eligible readers')] + choices()[1:]

    def clean_code(self):
        code = self.cleaned_data['code'].strip().upper()
        if not code.isalnum():
            raise forms.ValidationError('Letters and numbers only.')
        return code

    def clean(self):
        data = super().clean()
        bonus, reward = data.get('bonus') or 0, data.get('reward')
        top = 20 if reward == 'vouchers' else 1000
        if bonus and not 1 <= bonus <= top:
            self.add_error('bonus', f'Between 1 and {top}.')
        return data


class BroadcastForm(forms.ModelForm):
    """Broadcast Centre and Notification Promotions."""

    story = _BookChoice(queryset=Story.objects.none(), required=False)
    stories = forms.ModelMultipleChoiceField(queryset=Story.objects.none(), required=False)
    segment = forms.ChoiceField(choices=[], required=False)
    channels = forms.MultipleChoiceField(choices=Broadcast.CHANNELS, initial=['inapp'], required=False,
                                         widget=forms.CheckboxSelectMultiple)
    scheduled_at = _when(False)

    class Meta:
        model = Broadcast
        fields = ['name', 'title', 'body', 'audience', 'segment', 'country', 'story', 'stories', 'image',
                  'channels', 'scheduled_at', 'bonus']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.marketing.segments import choices
        books = Story.objects.visible_in_app().order_by('title')
        self.fields['story'].queryset = books
        self.fields['stories'].queryset = books
        self.fields['segment'].choices = [('', '— Use the audience above —')] + choices()
        self.fields['bonus'].required = False

    def clean_image(self):
        return _check_image(self.cleaned_data.get('image'))

    def clean_bonus(self):
        bonus = self.cleaned_data['bonus'] or 0
        if bonus > 200:
            raise forms.ValidationError('Keep gifts to 200 bonus or less.')
        return bonus

    def clean_channels(self):
        return ','.join(self.cleaned_data.get('channels') or ['inapp'])

    def clean_scheduled_at(self):
        when = self.cleaned_data.get('scheduled_at')
        if when and when <= timezone.now():
            raise forms.ValidationError('Pick a time in the future, or leave it empty to send now.')
        return when


class CampaignLinkForm(forms.ModelForm):
    story = _BookChoice(queryset=Story.objects.none())

    class Meta:
        model = CampaignLink
        fields = ['story', 'source', 'campaign', 'preview']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['story'].queryset = Story.objects.visible_in_app().order_by('title')

    def clean_campaign(self):
        return self.cleaned_data['campaign'].strip().upper()
