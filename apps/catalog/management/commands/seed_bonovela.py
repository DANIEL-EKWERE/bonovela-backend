"""Load the demo catalogue, store items and demo accounts.

    python manage.py seed_bonovela            # idempotent
    python manage.py seed_bonovela --reset    # wipe catalogue first

Demo accounts (password for all: bonovela1):
    reader@bonovela.app   reader
    author@bonovela.app   author with a signed story
    se@ / se2@ / se3@ / se4@ / se5@bonovela.app   senior editors
    ce@bonovela.app                                chief editor
    creator@bonovela.app  drama creator (owns the demo dramas)
    marketing@bonovela.app / finance@bonovela.app
    admin@bonovela.app    admin (also Django superuser)

--demo-sales adds a month of sample top-ups, subscriptions and earnings so
the Finance and Marketing dashboards have numbers to show.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

import random

from apps.catalog.models import Chapter, Contest, Genre, Story, Tag
from apps.drama.models import Drama, Episode
from apps.marketing.models import Banner
from apps.users.models import User
from apps.wallet.models import (AuthorEarning, CoinPackage, GiftItem, RedeemCode, Subscription,
                                SubscriptionPlan, Transaction)

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
        parser.add_argument('--episodes', type=int, default=100, help='Max episodes to create per drama')
        parser.add_argument('--demo-sales', action='store_true', help='Add sample sales for the dashboards')

    @transaction.atomic
    def handle(self, *args, **opts):
        if opts['reset']:
            Story.objects.all().delete()
        self._taxonomy()
        self._store()
        self._accounts()
        self._catalogue(opts['chapters'])
        self._editorial_teams()
        self._resubmission_demo()
        self._dramas(opts['episodes'])
        self._banners()
        if opts['demo_sales']:
            self._demo_sales()
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
        self._user('se@bonovela.app', User.ROLE_SE, 'Seun (SE)')
        self._user('ce@bonovela.app', User.ROLE_CE, 'Chidi (CE)')
        self._user('se2@bonovela.app', User.ROLE_SE, 'Bisi (SE)')
        self._user('se3@bonovela.app', User.ROLE_SE, 'Amara (SE)')
        self._user('se4@bonovela.app', User.ROLE_SE, 'Tomi (SE)')
        self._user('se5@bonovela.app', User.ROLE_SE, 'Kemi (SE)')
        self._user('creator@bonovela.app', User.ROLE_CREATOR, 'Bonovela Studios')
        self._user('marketing@bonovela.app', User.ROLE_MARKETING, 'Mira (Marketing)')
        self._user('finance@bonovela.app', User.ROLE_FINANCE, 'Femi (Finance)')
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

    def _dramas(self, max_episodes):
        creator = User.objects.get(email='creator@bonovela.app')
        now = timezone.now()
        for d in json.loads((DATA / 'dramas.json').read_text()):
            drama, created = Drama.objects.get_or_create(
                title=d['title'], creator=creator,
                defaults=dict(
                    synopsis=d['synopsis'], cover_index=d['id'] % 8,
                    story=Story.objects.filter(title=d['book']).first() if d.get('book') else None,
                    genre=Genre.objects.filter(name=d['genre']).first(),
                    is_completed=d.get('completed', True), is_published=True, plays=d.get('plays', 2000),
                    released_at=now - timedelta(days=d.get('daysAgo', 10), hours=d['id']),
                ),
            )
            if not created:
                continue
            for t in d['tags']:
                tag, _ = Tag.objects.get_or_create(name=t, defaults={'group': Tag.GROUP_SETTING})
                drama.tags.add(tag)
            count = min(d.get('episodes', 60), max_episodes)
            Episode.objects.bulk_create([
                Episode(drama=drama, number=n, title=f'Episode {n}', status=Episode.STATUS_PUBLISHED,
                        duration_seconds=60 + (d['id'] * 31 + n * 7919) % 51,
                        plays=max(0, drama.plays - n * drama.plays // (count + 10)), published_at=drama.released_at)
                for n in range(1, count + 1)
            ])

    def _banners(self):
        if Banner.objects.exists():
            return
        top = Story.objects.order_by('-views')[:2]
        for i, s in enumerate(top):
            Banner.objects.create(title=s.title, subtitle='Trending this week', placement='discover', target='story',
                                  story=s, order=i, color_index=i)
        d = Drama.objects.order_by('-plays').first()
        if d:
            Banner.objects.create(title=d.title, subtitle='New episodes daily', placement='drama', target='drama',
                                  drama=d, color_index=3)
        Banner.objects.create(title='First top-up bonus', subtitle='+200 bonus on your first 50 coins',
                              placement='store', target='store', color_index=5)

    def _demo_sales(self):
        """Sample money movements over the last 30 days (demo data only)."""
        if Transaction.objects.filter(title__startswith='[demo]').exists():
            return
        rng = random.Random(7)
        reader = User.objects.get(email='reader@bonovela.app')
        now = timezone.now()
        packages = list(CoinPackage.objects.all())
        plans = list(SubscriptionPlan.objects.all())
        stories = list(Story.objects.filter(is_signed=True)[:12])
        rows = []
        for day in range(30):
            when = now - timedelta(days=day, hours=rng.randint(0, 20))
            for _ in range(rng.randint(2, 7)):
                p = rng.choice(packages)
                rows.append(Transaction(user=reader, kind=Transaction.KIND_TOP_UP, title=f'[demo] Top up {p.coins} coins',
                                        coins=p.coins, price_cents=p.price_cents))
            if rng.random() < 0.5:
                plan = rng.choice(plans)
                rows.append(Transaction(user=reader, kind=Transaction.KIND_SUBSCRIPTION,
                                        title=f'[demo] Bonovela Plus · {plan.name}', price_cents=plan.price_cents))
            for _ in range(rng.randint(5, 15)):
                st = rng.choice(stories)
                rows.append(Transaction(user=reader, kind=Transaction.KIND_UNLOCK, title=f'[demo] {st.title} · unlock',
                                        coins=-15, story=st))
        created = Transaction.objects.bulk_create(rows)
        # auto_now_add ignores the value we pass, so spread the dates afterwards.
        for i, tx in enumerate(created):
            Transaction.objects.filter(pk=tx.pk).update(created_at=now - timedelta(days=i * 30 // len(created), minutes=i))
        Subscription.objects.get_or_create(user=reader, plan=plans[0],
                                           defaults={'ends_at': now + timedelta(days=plans[0].days)})
        payees = [User.objects.get(email='author@bonovela.app'), User.objects.get(email='creator@bonovela.app')]
        payees += [s.author for s in stories[:6]]
        AuthorEarning.objects.bulk_create([
            AuthorEarning(author=a, type=t, amount_cents=rng.randint(800, 9000), note='[demo]',
                          created_at=now - timedelta(days=rng.randint(0, 29)))
            for a in payees for t in ('profit_share', 'reward_share', 'attendance')
        ])

    def _editorial_teams(self):
        """Five Senior Editors under the Chief Editor, book assignments,
        chapter reviews and a little editorial activity."""
        from apps.editorial.models import (CompletionApplication, Escalation, Recommendation, Target, TeamMessage,
                                           month_start)
        get = lambda e: User.objects.get(email=f'{e}@bonovela.app')  # noqa: E731
        ce, se, se2, se3, se4, se5 = (get(x) for x in ('ce', 'se', 'se2', 'se3', 'se4', 'se5'))
        User.objects.filter(role=User.ROLE_SE).update(supervisor=ce)
        if Story.objects.filter(editor__isnull=False).exists():
            return
        now = timezone.now()
        rota = [se, se2, se3, se4, se5]
        stories = list(Story.objects.exclude(status=Story.STATUS_DRAFT).order_by('pk'))
        for i, story in enumerate(stories):
            editor = None if i % 10 == 9 else rota[i % len(rota)]  # a few unassigned
            Story.objects.filter(pk=story.pk).update(editor=editor, assigned_at=now - timedelta(days=60) if editor else None)
            if editor and not story.author.recruited_by_id and i % 3:
                User.objects.filter(pk=story.author_id, recruited_by__isnull=True).update(
                    recruited_by=editor, kyc_verified=bool(i % 4))
            pub = list(story.chapters.filter(status=Chapter.STATUS_PUBLISHED).order_by('number'))
            reviewer = editor or ce
            for ch in pub[:-2]:
                Chapter.objects.filter(pk=ch.pk).update(
                    review_status=Chapter.REVIEW_APPROVED, reviewed_by=reviewer,
                    reviewed_at=(ch.published_at or now) + timedelta(hours=6 + ch.number % 60))
        # Make a few books go quiet so risk detection has something to show.
        for story in stories[3:6]:
            story.chapters.filter(status=Chapter.STATUS_PUBLISHED).update(published_at=now - timedelta(days=20))
        if stories:
            Story.objects.filter(pk=stories[1].pk).update(needs_intervention=True,
                                                         intervention_note='Readers dropping after chapter 3')
        m = month_start()
        for ed, vals in [(se, {'recruitment': 10, 'contracts': 5}), (se2, {'recruitment': 8, 'contracts': 4}),
                         (se3, {'recruitment': 6}), (se4, {'recruitment': 6}), (se5, {'recruitment': 5})]:
            for metric, value in vals.items():
                Target.objects.get_or_create(user=ed, month=m, metric=metric, defaults={'value': value, 'set_by': ce})
        TeamMessage.objects.create(sender=ce, kind='guideline', title='Editorial guidelines for Q4',
                                   body='Review new chapters within 48 hours. Flag anything that copies another book.')
        TeamMessage.objects.create(sender=ce, kind='target', title='This month\'s focus',
                                   body='Recruit two new romance authors each and clear the chapter review queue.')
        TeamMessage.objects.create(sender=ce, recipient=se3, kind='feedback', title='Great work on reviews',
                                   body='Your review turnaround is the best on the desk.')
        if stories:
            Escalation.objects.create(raised_by=se, category='complaint', subject='Author disputes chapter removal',
                                      body='The author says the removed chapter was original. Please advise.',
                                      story=stories[2])
            Recommendation.objects.create(kind='promote_author', about_user=stories[0].author, recommended_by=se,
                                          note='Daily updates for three months and strong retention.')
        done = Story.objects.filter(status=Story.STATUS_COMPLETED, is_signed=True).first()
        if done:
            CompletionApplication.objects.get_or_create(story=done, defaults={'message': 'The finale is up!'})

    def _resubmission_demo(self):
        """A book rejected by se2's team, then sent again from a second
        account on the same IP with a new title, so it lands with se's team
        flagged."""
        from apps.editorial.history import flag
        from apps.editorial.models import ContractApplication
        from apps.users.models import IPLog

        if User.objects.filter(email='lara.quinn@authors.bonovela.app').exists():
            return
        now = timezone.now()
        se, se2 = (User.objects.get(email=f'{x}@bonovela.app') for x in ('se', 'se2'))
        genre = Genre.objects.filter(name='Romance').first()
        text = [
            'Marisol counted the lanterns on the Okonkwo jetty twice before she admitted the ferry was not coming. '
            'Her brother had promised the boatman eleven silver coins and a jar of plum wine, and he had paid neither.',
            'The cartographer from Lisbon found her there at dusk, ink on his cuffs and a torn map of the delta in his '
            'teeth, asking whether the old salt road still ran past the drowned chapel of Saint Benedetta.',
            'By the third night they had burned the map, stolen a fisherman\'s skiff and sworn on the chapel bell that '
            'neither of them would mention the duke\'s ring hidden in the hollow of the lantern post.',
        ]

        def make(author, title, editor, days):
            story = Story.objects.create(
                author=author, title=title, synopsis=' '.join(['A runaway heiress and a forger cross the delta.'] * 3),
                cover_index=6, audience='female', content_rating='16+', genre=genre, status=Story.STATUS_ONGOING,
                editor=editor, assigned_at=now - timedelta(days=days))
            for n, para in enumerate(text, 1):
                Chapter.objects.create(story=story, number=n, title=f'Chapter {n}', content=(para + '\n\n') * 6,
                                       status=Chapter.STATUS_PUBLISHED, published_at=now - timedelta(days=days - n))
            # Keep the demo books out of the "recently updated" top of lists.
            Story.objects.filter(pk=story.pk).update(updated_at=now - timedelta(days=days))
            return story

        first = self._user('maris.vale@authors.bonovela.app', User.ROLE_AUTHOR, 'Maris Vale')
        second = self._user('lara.quinn@authors.bonovela.app', User.ROLE_AUTHOR, 'Lara Quinn')
        for u in (first, second):
            IPLog.objects.get_or_create(user=u, ip='102.89.34.17', defaults={'first_event': 'signup', 'last_event': 'submit'})
        old = make(first, 'The Lantern Bride', se2, 40)
        old_app = ContractApplication.objects.create(
            story=old, status=ContractApplication.STATUS_REJECTED, se=se2, rejected_by=se2,
            rejected_at=now - timedelta(days=25), submitted_ip='102.89.34.17',
            rejection_reason='Opening chapters lift whole scenes from another author\'s book, and the plot stalls '
                             'after chapter 2. Not suitable for signing.',
            feedback='Not selected for signing.')
        ContractApplication.objects.filter(pk=old_app.pk).update(created_at=now - timedelta(days=30))
        new = make(second, 'Bride of the Lantern Delta', se, 6)
        flag(ContractApplication.objects.create(story=new, submitted_ip='102.89.34.17',
                                                message='My first book on Bonovela!'))
