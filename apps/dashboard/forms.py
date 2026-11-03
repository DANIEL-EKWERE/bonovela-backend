from django import forms

from apps.catalog.models import Chapter, Contest, Genre, Story, Tag, word_count
from apps.wallet.models import PayoutMethod

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
    class Meta:
        model = PayoutMethod
        fields = ['method', 'account_name', 'bank_name', 'account_detail']
