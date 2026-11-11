"""Every database gets the genres and tags authors choose from, not only
ones seeded with demo data. Adds what's missing; never changes existing rows."""

import json
from pathlib import Path

from django.db import migrations
from django.utils.text import slugify

TAXONOMY = Path(__file__).resolve().parent.parent / 'seed_data' / 'taxonomy.json'
APP_TABS = {'Werewolf', 'Romance', 'Mafia', 'Fantasy', 'LGBTQ+'}


def load(apps, schema_editor):
    Genre = apps.get_model('catalog', 'Genre')
    Tag = apps.get_model('catalog', 'Tag')
    tax = json.loads(TAXONOMY.read_text())
    retired = set(tax.get('retired_genres', []))
    for i, name in enumerate(tax['genres']):
        Genre.objects.get_or_create(name=name, defaults={
            'slug': slugify(name), 'order': i, 'show_in_app': name in APP_TABS, 'is_active': name not in retired,
        })
    existing = set(Tag.objects.values_list('name', flat=True))
    Tag.objects.bulk_create([
        Tag(name=name, group=group) for group, names in tax['tags'].items() for name in names if name not in existing
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0005_retire_genres_add_erotic'),
    ]

    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
