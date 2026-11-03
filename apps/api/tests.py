from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from apps.catalog.models import Chapter, Story, Tag
from apps.editorial import services as editorial
from apps.editorial.models import ContractApplication, Promotion, PromotionRequest
from apps.users.models import User
from apps.wallet.models import GiftItem, Wallet


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
        titles = [s['title'] for s in APIClient().get('/api/books/discover/?tab=Romance').data]
        self.assertEqual(
            titles, ['Recommended For You', "Editor's Pick", 'Completed', 'Love On Top', 'Erotica', 'MM Romance']
        )

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
            'genre': 'Romance', 'tags': ['Contemporary', 'Divorce'], 'synopsis': ' '.join(['word'] * 25),
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        sid = r.data['id']
        self.assertTrue(r.data['is_info_complete'])
        for _ in range(3):
            ch = c.post(f'/api/author/stories/{sid}/chapters/', {'title': 'T', 'content': 'text ' * 700}, format='json').data
            self.assertEqual(c.post(f'/api/author/stories/{sid}/chapters/{ch["id"]}/publish/').status_code, 200)

        # Unsigned books don't show in the app.
        self.assertEqual(APIClient().get(f'/api/books/{sid}/').status_code, 404)

        self.assertEqual(c.post(f'/api/author/stories/{sid}/application/', {'message': 'Hi'}).status_code, 201)
        app = ContractApplication.objects.get(story_id=sid)
        editorial.ae_review(app, User.objects.get(email='ae@bonovela.app'), approve=True)
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
