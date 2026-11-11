"""The FREE tab in the app: a novel category whose shelves the Chief Editor
curates like the others (empty spaces fill from the whole catalogue)."""

from django.db import migrations
from django.db.models import F

SHELVES = [
    ('Free for now', 'cover_row'),
    ('Free Reads', 'cover_row'),
    ('Rising Stars', 'paged_list'),
    ('Featured for you', 'cover_row'),
    ('New Arrivals', 'cover_row'),
]


def create(apps, schema_editor):
    ShelfCategory = apps.get_model('editorial', 'ShelfCategory')
    Shelf = apps.get_model('editorial', 'Shelf')
    if ShelfCategory.objects.filter(kind='novel', name='Free').exists():
        return
    ShelfCategory.objects.filter(kind='novel').update(order=F('order') + 1)
    cat = ShelfCategory.objects.create(kind='novel', name='Free', order=0)
    for i, (title, layout) in enumerate(SHELVES):
        Shelf.objects.create(category=cat, title=title, layout=layout, order=i)


def remove(apps, schema_editor):
    apps.get_model('editorial', 'ShelfCategory').objects.filter(kind='novel', name='Free').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('editorial', '0009_shelf_paged_layout'),
    ]

    operations = [migrations.RunPython(create, remove)]
