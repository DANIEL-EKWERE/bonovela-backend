"""Authors can no longer pick Apocalypse, Urban, Eastern or History (books
that already use them keep them); Erotic is added after LGBTQ+."""

from django.db import migrations
from django.db.models import F

RETIRED = ['Apocalypse', 'Urban', 'Eastern', 'History']


def forwards(apps, schema_editor):
    Genre = apps.get_model('catalog', 'Genre')
    Genre.objects.filter(name__in=RETIRED).update(is_active=False, show_in_app=False)
    lgbt = Genre.objects.filter(name='LGBTQ+').first()
    after = lgbt.order + 1 if lgbt else (Genre.objects.order_by('-order').values_list('order', flat=True).first() or 0) + 1
    if not Genre.objects.filter(name='Erotic').exists():
        Genre.objects.filter(order__gte=after).update(order=F('order') + 1)
        Genre.objects.create(name='Erotic', slug='erotic', order=after, show_in_app=False)


def backwards(apps, schema_editor):
    # Erotic stays: books may already use it.
    apps.get_model('catalog', 'Genre').objects.filter(name__in=RETIRED).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0004_genre_is_active'),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
