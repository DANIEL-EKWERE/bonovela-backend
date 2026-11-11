"""A complete, valid Apply for Contract submission, for tests."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image


def id_photo(name='id.png', size=(800, 520)):
    buf = io.BytesIO()
    Image.new('RGB', size, (230, 200, 210)).save(buf, 'PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def application_data(editor, **overrides):
    data = {
        'first_name': 'Ada', 'middle_name': 'Ngozi', 'surname': 'Okafor',
        'id_front': id_photo('front.png'), 'id_back': id_photo('back.png'),
        'facebook_url': '', 'instagram_url': 'https://instagram.com/ada.writes',
        'house_address': 'Flat 3, Rose Court', 'street_name': 'Allen Avenue', 'house_number': '12',
        'country': 'NG', 'state': 'NG-LA', 'phone_dial': '+234', 'phone_number': '0801 234 5678',
        'chosen_editor': editor.pk, 'character_details': 'Ada, 24, a stubborn florist.',
        'outline': 'Ada inherits a shop; a rival florist opens next door; they fall in love.', 'confirm': 'on',
    }
    data.update(overrides)
    return data
