from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.catalog.models import Chapter, Story, Tag
from apps.editorial import services as editorial
from apps.editorial.models import ContractApplication, Promotion, PromotionRequest
from apps.users.models import User
from apps.wallet.models import GiftItem, Wallet

# Tests seed only a few chapters per novel.
FEW_CHAPTERS = override_settings(BONOVELA={**settings.BONOVELA, 'MIN_LIVE_CHAPTERS': 1})


@FEW_CHAPTERS
class BonovelaAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_bonovela', chapters=12, verbosity=0)

    def login(self, email='reader@bonovela.app'):
        client = APIClient()
        r = client.post('/api/auth/token/', {'email': email, 'password': 'bonovela1'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        return client

    # ── Reader flows ─────────────────────────────────────────────────────────
    def test_register_gives_starter_wallet(self):
        c = APIClient()
        r = c.post('/api/auth/register/', {'email': 'new@x.com', 'password': 'pink_reads27', 'name': 'New'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        c.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        w = c.get('/api/wallet/').data
        self.assertEqual((w['coins'], w['bonus']), (60, 20))
        self.assertEqual(c.get('/api/inbox/').data['unread'], 1)

    def test_register_enforces_password_rules(self):
        r = APIClient().post('/api/auth/register/', {'email': 'a@x.com', 'password': 'abc-def'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_romance_discover_sections(self):
        sections = APIClient().get('/api/books/discover/?tab=Romance').data
        self.assertEqual([s['title'] for s in sections], ['Love on Top', 'Best Romance', "Editor's Picks", 'Recommended'])
        self.assertEqual(sections[0]['layout'], 'cover_grid')

    def test_shelf_picks_need_ce_approval_and_come_first(self):
        from apps.editorial.models import Shelf, ShelfBook
        shelf = Shelf.objects.get(title='Best Romance')
        story = Story.objects.visible_in_app().filter(is_short=False).exclude(genre__name='Romance').first()
        se = User.objects.get(email='se@bonovela.app')
        ce = User.objects.get(email='ce@bonovela.app')

        def shelf_ids():
            data = APIClient().get('/api/books/discover/?tab=Romance').data
            return [b['id'] for b in next(s for s in data if s['title'] == 'Best Romance')['books']]

        entry = editorial.add_to_shelf(se, shelf, story)
        self.assertEqual(entry.status, ShelfBook.STATUS_PENDING)
        self.assertNotIn(story.id, shelf_ids())
        editorial.decide_shelf_book(ce, entry, approve=True)
        self.assertEqual(shelf_ids()[0], story.id)

    def test_free_tab_sections(self):
        sections = APIClient().get('/api/books/discover/?tab=Free').data
        self.assertEqual([s['title'] for s in sections],
                         ['Free for now', 'Free Reads', 'Rising Stars', 'Featured for you', 'New Arrivals'])
        self.assertEqual(sections[2]['layout'], 'paged_list')
        # Earlier shelves don't crowd out later ones, and repeats come last.
        self.assertTrue(all(s['books'] for s in sections))
        first = {b['id'] for b in sections[0]['books']}
        second = [b['id'] for b in sections[1]['books']]
        fresh = [i for i in second if i not in first]
        self.assertEqual(second[:len(fresh)], fresh)

    def test_shelf_feeds_use_few_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from apps.catalog import feeds
        with CaptureQueriesContext(connection) as q:
            cats = feeds.short_collections()
        self.assertEqual(len(cats), 6)
        self.assertLess(len(q), 12)  # not several per shelf (18 shelves)
        with CaptureQueriesContext(connection) as q:
            feeds.discover_feed('Mafia')
        self.assertLess(len(q), 12)

    def test_tag_shelves_top_up_from_the_category_genre(self):
        mafia = APIClient().get('/api/books/discover/?tab=Mafia').data
        dark = next(s for s in mafia if s['title'] == 'Dark Romance')
        mafia_books = Story.objects.visible_in_app().filter(genre__name='Mafia', is_short=False).count()
        self.assertGreaterEqual(len(dark['books']), min(8, mafia_books))

    def test_reader_support_report(self):
        c = self.login()
        self.assertEqual(c.post('/api/support/', {'message': ''}, format='json').status_code, 400)
        r = c.post('/api/support/', {'category': 'coins', 'message': 'Paid for 1,000 coins, got nothing',
                                     'payment_ref': 'GPA.1234'}, format='json')
        self.assertEqual((r.status_code, r.data[0]['status']), (201, 'open'))
        from apps.finance.models import SupportTicket
        self.assertEqual(SupportTicket.objects.get().payment_ref, 'GPA.1234')

    def test_geo_lists(self):
        countries = APIClient().get('/api/geo/countries/').data
        self.assertIn({'code': 'NG', 'name': 'Nigeria', 'dial': '+234'}, countries)
        self.assertGreater(len(countries), 240)
        self.assertIn('Lagos', [s['name'] for s in APIClient().get('/api/geo/countries/NG/states/').data])

    def test_payout_method_needs_confirmation_and_bank_details(self):
        c = self.login('author@bonovela.app')
        bank = {'method': 'bank', 'country': 'Nigeria', 'currency': 'USD', 'bank_name': 'FCMB',
                'swift_code': 'FCMBNGLA', 'account_detail': '1047547276', 'account_name': 'Ada Okafor',
                'address': '12 Allen Avenue', 'city': 'Lagos', 'state': 'Lagos', 'postal_code': '100001'}
        self.assertEqual(c.put('/api/author/payout-method/', bank, format='json').status_code, 400)  # not confirmed
        r = c.put('/api/author/payout-method/', {**bank, 'city': '', 'confirmed': True}, format='json')
        self.assertIn('city', r.data['errors'])
        r = c.put('/api/author/payout-method/', {**bank, 'confirmed': True}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(c.get('/api/author/payout-method/').data['swift_code'], 'FCMBNGLA')

    def test_income_has_available_to_withdraw_not_mab(self):
        data = self.login('author@bonovela.app').get('/api/author/income/').data
        self.assertIn('available_cents', data)
        self.assertNotIn('attendance', [t['type'] for t in data['types']])
        self.assertEqual([t['type'] for t in data['types']],
                         ['profit_share', 'completion', 'one_time', 'reward_share', 'carry_over'])
        self.assertIn('minimum_cents', data['withdraw'])

    def test_author_withdrawals_api(self):
        from apps.wallet.models import AuthorEarning, Payout
        c = self.login('author@bonovela.app')
        from apps.users.models import User
        author = User.objects.get(email='author@bonovela.app')
        Payout.objects.filter(author=author).delete()
        AuthorEarning.objects.create(author=author, type='profit_share', amount_cents=30000)
        bank = {'method': 'bank', 'country': 'Nigeria', 'currency': 'USD', 'bank_name': 'FCMB',
                'swift_code': 'FCMBNGLA', 'account_detail': '1047547276', 'account_name': 'Joy Mercy Osemeke',
                'address': 'Agbara Branch', 'city': 'Lagos', 'state': 'Edo', 'postal_code': '100001'}
        r = c.post('/api/author/withdrawals/', {**bank, 'amount_cents': 5000, 'confirmed': True}, format='json')
        self.assertEqual(r.status_code, 400)  # under the minimum
        r = c.post('/api/author/withdrawals/', {**bank, 'amount_cents': 20000}, format='json')
        self.assertEqual(r.status_code, 400)  # not confirmed
        r = c.post('/api/author/withdrawals/', {**bank, 'amount_cents': 20000, 'confirmed': True}, format='json')
        self.assertEqual((r.status_code, r.data['status']), (201, 'Pending'))
        r = c.post('/api/author/withdrawals/', {**bank, 'amount_cents': 10000, 'confirmed': True}, format='json')
        self.assertEqual(r.data['code'], 'withdrawal')  # one a month
        data = c.get('/api/author/withdrawals/').data
        self.assertEqual(len(data['withdrawals']), 1)
        self.assertTrue(data['rules']['placed_this_month'])

    def test_short_collections(self):
        data = APIClient().get('/api/shorts/collections/').data
        self.assertEqual(data[0]['title'], 'Love at First Sight')
        self.assertEqual(data[0]['sections'][0]['title'], 'Hearts in Bloom')
        self.assertTrue(all(b['is_short'] for b in data[0]['sections'][0]['books']))

    def test_book_fields_match_app_model(self):
        book = APIClient().get('/api/books/').data['results'][0]
        for key in ('id', 'title', 'author', 'synopsis', 'genre', 'tags', 'cover_url', 'rating', 'views',
                    'chapter_count', 'word_count', 'is_completed', 'is_short', 'age_rating', 'updated_at'):
            self.assertIn(key, book)

    def test_locked_chapter_unlock_spends_bonus_first(self):
        c = self.login()
        story = Story.objects.filter(is_short=False).first()
        locked = c.get(f'/api/books/{story.id}/chapters/11/').data
        self.assertTrue(locked['is_locked'])
        self.assertEqual(locked['content'], '')
        r = c.post(f'/api/books/{story.id}/chapters/11/unlock/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data['is_locked'])
        self.assertTrue(r.data['content'])
        w = c.get('/api/wallet/').data
        self.assertEqual((w['coins'], w['bonus']), (60, 5))  # 15 coins: all from the 20 bonus

    def test_insufficient_balance_is_402(self):
        c = self.login()
        Wallet.objects.filter(user__email='reader@bonovela.app').update(coins=0)
        story = Story.objects.filter(is_short=False).first()
        c.post(f'/api/books/{story.id}/chapters/11/unlock/')  # uses remaining bonus 20 → 5
        r = c.post(f'/api/books/{story.id}/chapters/12/unlock/')
        self.assertEqual(r.status_code, 402)

    def test_gift_sends_votes(self):
        c = self.login()
        story = Story.objects.first()
        donut = GiftItem.objects.get(name='Donut')
        Wallet.objects.filter(user__email='reader@bonovela.app').update(coins=500)
        self.assertEqual(c.post(f'/api/books/{story.id}/gift/', {'item': donut.id}).status_code, 201)
        self.assertEqual(c.get(f'/api/books/{story.id}/').data['votes'], 1)

    def test_check_in_once_per_day(self):
        c = self.login()
        self.assertEqual(c.post('/api/rewards/check-in/').data['bonus'], 15)
        self.assertEqual(c.post('/api/rewards/check-in/').status_code, 400)

    def test_onboarding_fills_shelf(self):
        c = self.login()
        c.post('/api/auth/onboarding/', {'reader_type': 'female'}, format='json')
        lib = c.get('/api/library/').data
        self.assertEqual(len(lib), 9)
        self.assertTrue(all(b['is_recommended'] for b in lib))

    # ── Author → editorial → app ─────────────────────────────────────────────
    def test_author_story_signing_flow(self):
        c = self.login('author@bonovela.app')
        r = c.post('/api/author/stories/', {
            'title': 'Love and Lies', 'cover_index': 2, 'audience': 'female', 'content_rating': '16+',
            'genre': 'Romance', 'tags': ['Contemporary', 'Divorce'], 'synopsis': ' '.join(['word'] * 60),
            'language': 'English',
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        sid = r.data['id']
        self.assertTrue(r.data['is_info_complete'])
        self.assertFalse(r.data['can_apply_for_contract'])
        # Chapters: drafts may be short, nothing over 2,000 words, publishing needs 600+.
        short = c.post(f'/api/author/stories/{sid}/chapters/', {'title': 'S', 'content': 'text ' * 100}, format='json').data
        self.assertEqual(c.post(f'/api/author/stories/{sid}/chapters/{short["id"]}/publish/').data['code'],
                         'chapter_length')
        self.assertEqual(c.post(f'/api/author/stories/{sid}/chapters/', {'title': 'L', 'content': 'text ' * 2001},
                                format='json').status_code, 400)
        self.assertEqual(c.post(f'/api/author/stories/{sid}/application/', {}).status_code, 400)  # < 5,000 words
        for _ in range(3):
            ch = c.post(f'/api/author/stories/{sid}/chapters/', {'title': 'T', 'content': 'text ' * 1800}, format='json').data
            self.assertEqual(c.post(f'/api/author/stories/{sid}/chapters/{ch["id"]}/publish/').status_code, 200)

        # Unsigned books don't show in the app.
        self.assertEqual(APIClient().get(f'/api/books/{sid}/').status_code, 404)

        from apps.editorial.testdata import application_data
        editors = c.get(f'/api/author/stories/{sid}/editors/').data
        self.assertNotIn(User.objects.get(email='se5@bonovela.app').id, [e['id'] for e in editors])  # shorts desk
        se = User.objects.get(pk=editors[0]['id'])
        r = c.post(f'/api/author/stories/{sid}/application/', application_data(se, house_number=''), format='multipart')
        self.assertEqual((r.status_code, r.data['step']), (400, 2))
        r = c.post(f'/api/author/stories/{sid}/application/', application_data(se), format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['status_label'], 'Pending review')
        app = ContractApplication.objects.get(story_id=sid)
        editorial.se_decide(app, User.objects.get(email='se@bonovela.app'), 'approve')
        editorial.ce_send_contract(app, User.objects.get(email='ce@bonovela.app'), signing_bonus_cents=5000)
        self.assertEqual(c.post(f'/api/author/stories/{sid}/sign/').status_code, 200)

        self.assertEqual(APIClient().get(f'/api/books/{sid}/').status_code, 200)
        self.assertEqual(c.get('/api/author/income/').data['month_cents'], 5000)

    def test_se_cannot_promote_directly_ce_approves_request(self):
        story = Story.objects.first()
        se = User.objects.get(email='se@bonovela.app')
        ce = User.objects.get(email='ce@bonovela.app')
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            editorial.promote(se, story, 'editors_pick', 7)
        req = editorial.request_promotion(se, story, ['editors_pick', 'trending'], 7, 'Strong reads')
        editorial.decide_promotion(ce, req, approve=True)
        self.assertEqual(Promotion.objects.filter(story=story).count(), 2)
        self.assertEqual(req.status, PromotionRequest.STATUS_APPROVED)
        romance = APIClient().get(f'/api/books/discover/?tab={story.genre.name}').data
        picks = next(s for s in romance if s['title'] == "Editor's Pick")
        self.assertEqual(picks['books'][0]['id'], story.id)


@FEW_CHAPTERS
class DramaAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_bonovela', chapters=2, episodes=8, verbosity=0)

    def login(self, email='reader@bonovela.app'):
        client = APIClient()
        r = client.post('/api/auth/token/', {'email': email, 'password': 'bonovela1'}, format='json')
        client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        return client

    def test_all_dramas_list(self):
        data = APIClient().get('/api/dramas/?all=1').data
        self.assertIsInstance(data, list)
        self.assertTrue(data and 'episode_count' in data[0])

    def test_feed_sections(self):
        r = APIClient().get('/api/dramas/')
        self.assertEqual([s['title'] for s in r.data['sections']],
                         ['Popular Picks', 'New Release', 'You May Also Like', 'Trending'])
        first = r.data['sections'][0]['dramas'][0]
        self.assertIn(first['kind'], ('drama', 'adapted'))
        self.assertEqual(first['episode_count'], 8)

    def test_locked_episode_unlock_and_creator_share(self):
        from apps.drama.models import Drama
        from apps.wallet.models import AuthorEarning
        d = Drama.objects.first()
        anon = APIClient().get(f'/api/dramas/{d.pk}/episodes/6/').data
        self.assertTrue(anon['is_locked'])
        self.assertEqual(anon['video_url'], '')
        self.assertFalse(APIClient().get(f'/api/dramas/{d.pk}/episodes/1/').data['is_locked'])

        c = self.login()
        coins = c.get('/api/wallet/').data['coins']
        r = c.post(f'/api/dramas/{d.pk}/episodes/6/unlock/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(r.data['is_locked'])
        w = c.get('/api/wallet/').data
        self.assertEqual(coins + 20, w['coins'] + w['bonus'] + 10)  # bonus first; 10 coins spent overall
        self.assertTrue(AuthorEarning.objects.filter(author=d.creator, note__contains='EP 6').exists())
        # Unlocking twice doesn't charge again.
        c.post(f'/api/dramas/{d.pk}/episodes/6/unlock/')
        w2 = c.get('/api/wallet/').data
        self.assertEqual((w['coins'], w['bonus']), (w2['coins'], w2['bonus']))

    def test_save_and_banners(self):
        from apps.drama.models import Drama
        d = Drama.objects.first()
        c = self.login()
        c.post(f'/api/dramas/{d.pk}/save/')
        self.assertEqual([x['id'] for x in c.get('/api/dramas/saved/').data], [d.pk])
        self.assertTrue(c.get(f'/api/dramas/{d.pk}/').data['is_saved'])
        banners = APIClient().get('/api/banners/?placement=discover').data
        self.assertTrue(banners)
        APIClient().post(f'/api/banners/{banners[0]["id"]}/click/')
        from apps.marketing.models import Banner
        self.assertEqual(Banner.objects.get(pk=banners[0]['id']).clicks, 1)
