from django import forms

from apps.catalog.models import Chapter, Contest, Genre, Story, Tag, word_count
from apps.drama.models import Drama, Episode
from apps.marketing.models import Banner, Broadcast
from apps.wallet.models import PayoutMethod, RedeemCode

VIDEO_TYPES = ('video/mp4', 'video/quicktime', 'video/webm', 'video/x-m4v')
MAX_VIDEO_MB = 500

LANGUAGES = ['English', 'Français', 'Español', 'Português', 'Deutsch', 'Bahasa Indonesia', 'Filipino']


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

    genre = forms.ModelChoiceField(queryset=Genre.objects.all(), required=False)
    tags = forms.ModelMultipleChoiceField(queryset=Tag.objects.all(), required=False)
    contests = forms.ModelMultipleChoiceField(queryset=Contest.objects.filter(is_active=True), required=False)

    class Meta:
        model = Story
        fields = ['novel_type', 'genre', 'tags', 'synopsis', 'contests']

    def clean_tags(self):
        tags = self.cleaned_data['tags']
        if len(tags) > 10:
            raise forms.ValidationError('Choose up to 10 tags.')
        return tags

    def clean_synopsis(self):
        text = self.cleaned_data['synopsis']
        n = word_count(text)
        if text and not 20 <= n <= 300:
            raise forms.ValidationError(f'Synopsis must be 20–300 words (now {n}).')
        return text


class ChapterForm(forms.ModelForm):
    class Meta:
        model = Chapter
        fields = ['title', 'content', 'note']


class PayoutForm(forms.ModelForm):
    # Set by the "Please check your information" dialog.
    confirmed = forms.BooleanField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = PayoutMethod
        fields = ['method', 'country', 'currency', 'bank_name', 'swift_code', 'account_detail', 'account_name',
                  'address', 'city', 'state', 'postal_code']

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
class BannerForm(forms.ModelForm):
    starts_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    ends_at = forms.DateTimeField(required=False, widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}))
    story = forms.ModelChoiceField(queryset=Story.objects.visible_in_app().order_by('title'), required=False)
    drama = forms.ModelChoiceField(queryset=Drama.objects.visible_in_app().order_by('title'), required=False)

    class Meta:
        model = Banner
        fields = ['title', 'subtitle', 'image', 'color_index', 'placement', 'target', 'story', 'drama', 'url',
                  'order', 'starts_at', 'ends_at', 'is_active']

    def clean_image(self):
        return _check_image(self.cleaned_data.get('image'))

    def clean(self):
        data = super().clean()
        target = data.get('target')
        need = {'story': 'story', 'drama': 'drama', 'url': 'url'}.get(target)
        if need and not data.get(need):
            self.add_error(need, 'Required for this target.')
        if data.get('ends_at') and data.get('starts_at') and data['ends_at'] <= data['starts_at']:
            self.add_error('ends_at', 'Must be after the start.')
        return data


class RedeemCodeForm(forms.ModelForm):
    days = forms.IntegerField(min_value=1, max_value=365, required=False, help_text='Leave blank for no expiry')

    class Meta:
        model = RedeemCode
        fields = ['code', 'bonus', 'campaign', 'max_uses']

    def clean_code(self):
        code = self.cleaned_data['code'].strip().upper()
        if not code.isalnum():
            raise forms.ValidationError('Letters and numbers only.')
        return code

    def clean_bonus(self):
        bonus = self.cleaned_data['bonus']
        if not 1 <= bonus <= 1000:
            raise forms.ValidationError('Between 1 and 1000 bonus.')
        return bonus


class BroadcastForm(forms.ModelForm):
    story = forms.ModelChoiceField(queryset=Story.objects.visible_in_app().order_by('title'), required=False)

    class Meta:
        model = Broadcast
        fields = ['title', 'body', 'audience', 'story', 'bonus']

    def clean_bonus(self):
        bonus = self.cleaned_data['bonus'] or 0
        if bonus > 200:
            raise forms.ValidationError('Keep gifts to 200 bonus or less.')
        return bonus
