"""The curated shelves the app launches with, and LGBTQ+ replacing Urban as
a Discover tab. The Chief Editor can rename and reorder all of this later."""

from django.db import migrations
from django.utils.text import slugify

# (category, genres that fill empty shelves, [shelf titles]); the first novel
# shelf of each category uses the 4-per-row cover grid.
NOVELS = [
    ('Romance', ['Romance'], ['Love on Top', 'Best Romance', "Editor's Picks", 'Recommended']),
    ('Mafia', ['Mafia'], ['Dark Romance', 'Forbidden Heart', 'Cold As You', "Mafia's Obsession"]),
    ('Fantasy', ['Fantasy'], ['Kingdom of Magic', 'Top Fantasy', 'Rising Stars', 'Start Your Adventure']),
    ('LGBTQ+', ['LGBTQ+', 'MM Romance'], ['Erotica', 'Hearts Without Limits (BL + GL)', 'MM Romance',
                                          'Forbidden Desires']),
]
SHORTS = [
    ('Love at First Sight', ['Hearts in Bloom', 'Love in Bed', 'A Kiss to Remember']),
    ('Family Drama', ['Betrayal & Awakening', 'Werewolf Revenge', 'Bitter Love', 'Forced Love']),
    ('Forbidden Desires', ['Beautifully Dangerous', 'Love Me Like a Villain']),
    ('Mafia & Dangerous Love', ["The Don's Secret", 'Owned by the Mafia', 'A Dangerous Affair']),
    ('Fantasy & Magic', ['Enchanted Hearts', 'The Cursed Love', 'Beyond the Mortal Realm']),
    ('Secrets, Drama & Revenge', ['Sweet Revenge', 'Lies Behind the Love', 'The Secret I Kept']),
]
APP_TABS = ['Werewolf', 'Romance', 'Mafia', 'Fantasy', 'LGBTQ+']


def create(apps, schema_editor):
    Genre = apps.get_model('catalog', 'Genre')
    ShelfCategory = apps.get_model('editorial', 'ShelfCategory')
    Shelf = apps.get_model('editorial', 'Shelf')

    for i, (name, genres, shelves) in enumerate(NOVELS):
        cat = ShelfCategory.objects.create(kind='novel', name=name, order=i)
        for g in genres:
            genre, _ = Genre.objects.get_or_create(name=g, defaults={'slug': slugify(g)})
            cat.genres.add(genre)
        for j, title in enumerate(shelves):
            Shelf.objects.create(category=cat, title=title, order=j, layout='cover_grid' if j == 0 else 'cover_row')
    for i, (name, shelves) in enumerate(SHORTS):
        cat = ShelfCategory.objects.create(kind='short', name=name, order=i)
        for j, title in enumerate(shelves):
            Shelf.objects.create(category=cat, title=title, order=j)

    Genre.objects.filter(name='Urban').update(show_in_app=False)
    for g in APP_TABS:
        Genre.objects.filter(name=g).update(show_in_app=True)


def remove(apps, schema_editor):
    apps.get_model('editorial', 'ShelfCategory').objects.all().delete()
    Genre = apps.get_model('catalog', 'Genre')
    Genre.objects.filter(name='Urban').update(show_in_app=True)
    Genre.objects.filter(name='LGBTQ+').update(show_in_app=False)


class Migration(migrations.Migration):

    dependencies = [
        ('editorial', '0006_shelves'),
        ('catalog', '0003_chapter_review_status_chapter_reviewed_at_and_more'),
    ]

    operations = [migrations.RunPython(create, remove)]
