"""Load the demo catalogue, store items and demo accounts.

    python manage.py seed_bonovela            # idempotent
    python manage.py seed_bonovela --reset    # wipe catalogue first

Demo accounts (password for all: bonovela1):
    reader@bonovela.app   reader
    author@bonovela.app   author with a signed story
    ae@ / se@ / ce@bonovela.app   editors
    admin@bonovela.app    admin (also Django superuser)
"""

import json
from datetime import date, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.catalog.models import Chapter, Contest, Genre, Story, Tag
from apps.users.models import User
from apps.wallet.models import CoinPackage, GiftItem, RedeemCode, SubscriptionPlan

DATA = Path(__file__).resolve().parents[2] / 'seed_data'
PASSWORD = 'bonovela1'

# Same prose as the app's MockData.chapterText, so chapters read identically.
PARAGRAPHS = [
    'Rain hammered against the tall windows, and the city lights smeared into long gold streaks across the glass. '
    'She stood very still, the way you stand when one wrong move will shatter everything.',
    '"You knew," she said quietly. It was not a question.',
    "He didn't answer at once. The silence stretched until it had weight, until she could feel it pressing on her "
    'shoulders like a hand.',
    'Somewhere below, a car horn sounded, ordinary and far away. It seemed impossible that the rest of the world was '
    'carrying on as if nothing had changed.',
    '"I was going to tell you," he finally said. "I was waiting for the right moment."',
    '"There is no right moment for this." She laughed, but there was no humour in it. "There never was."',
    'She picked up her coat. Her hands were steady, which surprised her. She had always imagined that this moment, '
    'if it came, would leave her shaking.',
    'At the door she paused, not because she expected him to stop her, but because she wanted to remember exactly '
    'how it felt to walk away first.',
    'The hallway was cold and smelled faintly of lilies from the vase someone replaced every Monday. She had never '
    'noticed before how much she hated lilies.',
    'By the time the elevator doors closed, she had already decided. Whatever came next, she would not be the one '
    'left waiting again.',
]


def chapter_text(title: str, number: int) -> str:
    start = number % len(PARAGRAPHS)
    ordered = PARAGRAPHS[start:] + PARAGRAPHS[:start]
    return f'{title} continues.\n\n' + '\n\n'.join(ordered + ordered[:6])


class Command(BaseCommand):
    help = 'Seed Bonovela with the demo catalogue, store items and accounts.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true', help='Delete existing stories first')
        parser.add_argument(
            '--chapters', type=int, default=24, help='Chapters to create per novel (shorts get all of theirs)'
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts['reset']:
            Story.objects.all().delete()
        self._taxonomy()
        self._store()
        self._accounts()
        self._catalogue(opts['chapters'])
        self.stdout.write(self.style.SUCCESS(f'Seeded {Story.objects.count()} stories. Demo password: {PASSWORD}'))

    def _taxonomy(self):
        tax = json.loads((DATA / 'taxonomy.json').read_text())
        app_tabs = {'Werewolf', 'Romance', 'Mafia', 'Fantasy', 'Urban'}
        for i, name in enumerate(tax['genres']):
            Genre.objects.update_or_create(
                name=name, defaults={'slug': slugify(name), 'order': i, 'show_in_app': name in app_tabs}
            )
        for group, names in tax['tags'].items():
            for name in names:
                Tag.objects.get_or_create(name=name, defaults={'group': group})
        for title, prize, days, brief in [
            ('#55 They Regret Losing Me, Too Late!', '$1,200', 45,
             'Stories where the one who walked away realises too late what they lost.'),
            ('#54 The Queen Stands Unconquered', '$1,200', 30, 'A heroine who rises after everyone counted her out.'),
            ('#53 Moonlit Bonds', '$800', 20, 'Werewolf and paranormal romance with fated mates and pack politics.'),
        ]:
            Contest.objects.get_or_create(
                title=title, defaults={'prize': prize, 'brief': brief, 'deadline': date.today() + timedelta(days=days)}
            )

    def _store(self):
        for i, (coins, bonus, cents, tag) in enumerate([
            (50, 200, 55, 'Limited-Time Offer'), (100, 0, 105, ''), (500, 50, 525, ''),
            (1000, 200, 1050, ''), (2000, 600, 2100, ''), (5000, 2000, 5250, ''),
        ]):
            CoinPackage.objects.update_or_create(
                coins=coins, defaults={'bonus': bonus, 'price_cents': cents, 'tag': tag, 'order': i}
            )
        for i, (name, cents, old, period, days, note, badge) in enumerate([
            ('Weekly', 290, 1122, 'week', 7, '$2.90 for 7 days, then $11.22/week', '70% OFF'),
            ('Monthly', 2442, None, 'month', 30, 'Billed monthly', ''),
            ('Yearly', 22285, None, 'year', 365, 'Billed yearly', ''),
        ]):
            SubscriptionPlan.objects.update_or_create(
                name=name,
                defaults={'price_cents': cents, 'old_price_cents': old, 'period': period, 'days': days,
                          'billing_note': note, 'badge': badge, 'order': i},
            )
        for i, (name, emoji, price, votes) in enumerate([
            ('Donut', '🍩', 100, 1), ('Hamburger', '🍔', 200, 2), ('Cake', '🎂', 500, 6),
            ('Bouquet', '💐', 1000, 15), ('Diamond Ring', '💍', 1888, 26), ('Crown', '👑', 5000, 68),
        ]):
            GiftItem.objects.update_or_create(name=name, defaults={'emoji': emoji, 'price': price, 'votes': votes, 'order': i})
        for code, bonus in [('BONOVELA', 50), ('WELCOME20', 20), ('PINKLOVE', 30)]:
            RedeemCode.objects.get_or_create(code=code, defaults={'bonus': bonus})

    def _user(self, email, role, name, **extra):
        user, created = User.objects.get_or_create(
            email=email, defaults={'username': email.split('@')[0], 'role': role, 'pen_name': name,
                                   'email_verified': True, **extra},
        )
        if created:
            user.set_password(PASSWORD)
            user.save()
        return user

    def _accounts(self):
        self._user('reader@bonovela.app', User.ROLE_READER, 'Bonovela Reader')
        self._user('author@bonovela.app', User.ROLE_AUTHOR, 'Demo Author')
        self._user('ae@bonovela.app', User.ROLE_AE, 'Amara (AE)')
        self._user('se@bonovela.app', User.ROLE_SE, 'Seun (SE)')
        self._user('ce@bonovela.app', User.ROLE_CE, 'Chidi (CE)')
        admin = self._user('admin@bonovela.app', User.ROLE_ADMIN, 'Bonovela Admin', is_staff=True, is_superuser=True)
        if not admin.is_superuser:
            admin.is_staff = admin.is_superuser = True
            admin.save()

    def _catalogue(self, max_chapters):
        books = json.loads((DATA / 'books.json').read_text())
        now = timezone.now()
        for b in books:
            author_email = f'{slugify(b["author"])}@authors.bonovela.app'
            author = self._user(author_email, User.ROLE_AUTHOR, b['author'])
            genre = Genre.objects.filter(name=b['genre']).first()
            short = b.get('short', False)
            completed = b.get('completed', False)
            story, created = Story.objects.get_or_create(
                title=b['title'],
                author=author,
                defaults=dict(
                    synopsis=b['synopsis'],
                    cover_index=int(b['id']) % 8,
                    audience='male' if b['genre'] in ('Urban', 'Fantasy') else 'female',
                    content_rating=f'{b.get("age", 16)}+',
                    genre=genre,
                    status=Story.STATUS_COMPLETED if completed else Story.STATUS_ONGOING,
                    is_short=short,
                    is_signed=True,
                    signed_at=now,
                    views=b.get('views', 10000),
                ),
            )
            if not created:
                continue
            for t in b['tags']:
                tag, _ = Tag.objects.get_or_create(name=t, defaults={'group': Tag.GROUP_SETTING})
                story.tags.add(tag)
            total = b.get('chapters', 120)
            count = total if short else min(total, max_chapters)
            days_ago = b.get('daysAgo', 1)
            Chapter.objects.bulk_create([
                Chapter(
                    story=story, number=n, title=f'Chapter {n}', content=chapter_text(story.title, n),
                    status=Chapter.STATUS_PUBLISHED, word_count=len(chapter_text(story.title, n).split()),
                    published_at=now - timedelta(days=days_ago + (count - n)),
                )
                for n in range(1, count + 1)
            ])
            Story.objects.filter(pk=story.pk).update(updated_at=now - timedelta(days=days_ago))
