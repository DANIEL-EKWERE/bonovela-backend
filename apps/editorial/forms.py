"""The contract application. One form validates every step for both the web
wizard and the app (POST /api/author/stories/<id>/application/)."""

from django import forms
from django.contrib.auth import get_user_model

from . import geo
from .models import ContractApplication

User = get_user_model()

MAX_ID_BYTES = 8 * 1024 * 1024
ID_TYPES = ('image/jpeg', 'image/png', 'image/webp')

# Which fields belong to which step, so callers can send authors back to the
# first step with a problem.
STEPS = {
    1: ['first_name', 'middle_name', 'surname', 'id_front', 'id_back', 'facebook_url', 'instagram_url'],
    2: ['house_address', 'street_name', 'house_number', 'country', 'state', 'phone_dial', 'phone_number', 'is_minor',
        'guardian_name', 'guardian_phone', 'guardian_email', 'guardian_address', 'guardian_relationship'],
    3: ['chosen_editor', 'character_details', 'outline'],
    5: ['confirm'],
}
GUARDIAN_FIELDS = ['guardian_name', 'guardian_phone', 'guardian_email', 'guardian_address', 'guardian_relationship']


def editors_for(story):
    """Senior Editors on the desk that matches the book."""
    desk = User.DESK_SHORTS if story.is_short else User.DESK_NOVELS
    return User.objects.filter(role=User.ROLE_SE, desk=desk, is_active=True, is_suspended=False).order_by(
        'pen_name', 'username')


class ContractApplicationForm(forms.ModelForm):
    phone_dial = forms.CharField(max_length=6)
    phone_number = forms.CharField(max_length=20)
    confirm = forms.BooleanField(error_messages={'required': 'Tick the box to confirm your information is correct.'})

    class Meta:
        model = ContractApplication
        fields = ['first_name', 'middle_name', 'surname', 'id_front', 'id_back', 'facebook_url', 'instagram_url',
                  'house_address', 'street_name', 'house_number', 'country', 'state', 'is_minor',
                  'guardian_name', 'guardian_phone', 'guardian_email', 'guardian_address', 'guardian_relationship',
                  'chosen_editor', 'character_details', 'outline']

    REQUIRED = ['first_name', 'surname', 'id_front', 'id_back', 'house_address', 'street_name', 'house_number',
                'country', 'chosen_editor', 'character_details', 'outline']

    def __init__(self, *args, story, **kwargs):
        super().__init__(*args, **kwargs)
        self.story = story
        self.fields['chosen_editor'].queryset = editors_for(story)
        self.fields['chosen_editor'].error_messages['invalid_choice'] = 'Choose one of the listed editors.'
        for name in self.REQUIRED:
            self.fields[name].required = True

    def _image(self, name):
        f = self.cleaned_data.get(name)
        if f and hasattr(f, 'content_type'):
            if f.content_type not in ID_TYPES:
                raise forms.ValidationError('Use a clear .jpg, .png or .webp photo.')
            if f.size > MAX_ID_BYTES:
                raise forms.ValidationError('Photos must be under 8 MB.')
            image = getattr(f, 'image', None)  # set by ImageField once Pillow has opened it
            if image and min(image.size) < 500:
                raise forms.ValidationError('This photo is too small to read. Take it closer, in good light.')
        return f

    def clean_id_front(self):
        return self._image('id_front')

    def clean_id_back(self):
        return self._image('id_back')

    def clean_country(self):
        code = self.cleaned_data['country'].upper()
        if code not in {c['code'] for c in geo.countries()}:
            raise forms.ValidationError('Choose your country from the list.')
        return code

    def clean(self):
        data = super().clean()
        country = data.get('country')
        if country:
            options = {s['code'] for s in geo.states(country)}
            if options and data.get('state') not in options:
                self.add_error('state', 'Choose your state from the list.')
            if not options:
                data['state'] = ''
        if data.get('phone_dial') and data.get('phone_number'):
            try:
                self.phone = geo.normalise_phone(data['phone_dial'], data['phone_number'])
            except ValueError as e:
                self.add_error('phone_number', str(e))
        if data.get('is_minor'):
            for name in GUARDIAN_FIELDS:
                if not data.get(name):
                    self.add_error(name, 'Required for authors under 18.')
            if data.get('guardian_phone'):
                try:
                    data['guardian_phone'] = geo.normalise_phone('', data['guardian_phone'])
                except ValueError:
                    self.add_error('guardian_phone', "Enter the guardian's number with its country code, e.g. +234…")
        else:
            for name in GUARDIAN_FIELDS:
                data[name] = ''
        return data

    def first_error_step(self):
        for step, names in STEPS.items():
            if any(n in self.errors for n in names):
                return step
        return 5

    def save(self, commit=True):
        app = super().save(commit=False)
        app.story = self.story
        app.phone = self.phone
        app.contract_type = 'non_exclusive'
        if commit:
            app.save()
        return app
