from django.core.management import call_command
from django.test import TestCase

from apps.catalog.models import Chapter, Genre, Story, Tag
from apps.editorial.models import ContractApplication
from apps.engagement.models import Report
from apps.users.models import User

PASSWORD = 'bonovela1'


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('seed_bonovela', chapters=4, verbosity=0)

    def login(self, email):
        self.assertTrue(self.client.login(username=email, password=PASSWORD))
        return User.objects.get(email=email)

    def assertPages(self, urls):
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, f'{url} → {r.status_code}')

    # ── Routing ──────────────────────────────────────────────────────────────
    def test_login_page_and_role_redirects(self):
        self.assertEqual(self.client.get('/login/').status_code, 200)
        self.assertRedirects(self.client.get('/author/'), '/login/?next=/author/')
        self.login('author@bonovela.app')
        self.assertRedirects(self.client.get('/'), '/author/')
        self.client.logout()
        self.login('admin@bonovela.app')
        self.assertRedirects(self.client.get('/'), '/admin-panel/')

    def test_reader_sees_intro_and_can_become_author(self):
        reader = self.login('reader@bonovela.app')
        self.assertContains(self.client.get('/author/'), 'author', status_code=200)
        self.client.post('/author/become/')
        reader.refresh_from_db()
        self.assertEqual(reader.role, User.ROLE_AUTHOR)

    def test_non_admin_cannot_open_admin(self):
        self.login('author@bonovela.app')
        self.assertEqual(self.client.get('/admin-panel/').status_code, 403)

    def test_suspended_user_cannot_log_in(self):
        u = User.objects.get(email='reader@bonovela.app')
        u.is_suspended = True
        u.save()
        r = self.client.post('/login/', {'username': u.email, 'password': PASSWORD})
        self.assertRedirects(r, '/login/')
        self.assertNotIn('_auth_user_id', self.client.session)

    # ── Author Center ────────────────────────────────────────────────────────
    def test_author_pages_render(self):
        author = self.login('author@bonovela.app')
        story = Story.objects.filter(author=author).first() or Story.objects.first()
        if story.author != author:
            story.author = author
            story.save()
        ch = story.chapters.first()
        self.assertPages([
            '/author/', '/author/stories/new/', f'/author/stories/{story.pk}/info/?step=1',
            f'/author/stories/{story.pk}/info/?step=2', f'/author/stories/{story.pk}/info/?step=3',
            f'/author/stories/{story.pk}/chapters/', f'/author/stories/{story.pk}/chapters/?tab=published',
            f'/author/stories/{story.pk}/chapters/?tab=trash', f'/author/stories/{story.pk}/chapters/new/',
            f'/author/stories/{story.pk}/chapters/{ch.pk}/', f'/author/stories/{story.pk}/stats/',
            f'/author/stories/{story.pk}/signing/', '/author/discover/', '/author/calendar/',
            '/author/comments/', '/author/income/',
        ])

    def test_author_creates_story_publishes_and_applies(self):
        self.login('author@bonovela.app')
        r = self.client.post('/author/stories/new/?step=1', {
            'title': 'Pink Moon', 'cover_index': 3, 'language': 'English', 'audience': 'female',
            'content_rating': '16+', 'action': 'next'})
        story = Story.objects.get(title='Pink Moon')
        self.assertRedirects(r, f'/author/stories/{story.pk}/info/?step=2')

        tag = Tag.objects.first()
        r = self.client.post(f'/author/stories/{story.pk}/info/?step=2', {
            'novel_type': 'original', 'genre': Genre.objects.first().pk, 'tags': [tag.pk],
            'synopsis': ' '.join(['word'] * 30), 'action': 'next'})
        self.assertRedirects(r, f'/author/stories/{story.pk}/info/?step=3')
        story.refresh_from_db()
        self.assertTrue(story.is_info_complete)

        for i in range(3):
            self.client.post(f'/author/stories/{story.pk}/chapters/new/', {
                'title': f'Chapter {i + 1}', 'content': 'She ran. ' * 50, 'action': 'release'})
        self.assertEqual(story.chapters.filter(status=Chapter.STATUS_PUBLISHED).count(), 3)
        self.assertFalse(Story.objects.visible_in_app().filter(pk=story.pk).exists())

        # Draft → trash → restore
        self.client.post(f'/author/stories/{story.pk}/chapters/new/', {'title': 'Draft', 'content': 'Soon.', 'action': 'save'})
        draft = story.chapters.get(title='Draft')
        self.client.post(f'/author/stories/{story.pk}/chapters/{draft.pk}/trash/')
        draft.refresh_from_db()
        self.assertEqual(draft.status, Chapter.STATUS_TRASH)
        self.client.post(f'/author/stories/{story.pk}/chapters/{draft.pk}/restore/')
        draft.refresh_from_db()
        self.assertEqual(draft.status, Chapter.STATUS_DRAFT)

        self.client.post(f'/author/stories/{story.pk}/signing/', {'action': 'apply', 'message': 'Please!'})
        self.assertEqual(story.applications.get().status, ContractApplication.STATUS_PENDING)

    def test_author_cannot_touch_others_story(self):
        self.login('author@bonovela.app')
        other = Story.objects.exclude(author__email='author@bonovela.app').first()
        self.assertEqual(self.client.get(f'/author/stories/{other.pk}/chapters/').status_code, 404)

    # ── Admin ────────────────────────────────────────────────────────────────
    def test_admin_pages_render(self):
        self.login('admin@bonovela.app')
        Report.objects.create(reporter=User.objects.get(email='reader@bonovela.app'),
                              story=Story.objects.first(), reason='Spam')
        self.assertPages(['/admin-panel/', '/admin-panel/users/', '/admin-panel/users/?role=author&q=a',
                          '/admin-panel/books/', '/admin-panel/books/?f=live', '/admin-panel/books/?f=removed',
                          '/admin-panel/revenue/', '/admin-panel/revenue/?kind=unlock', '/admin-panel/reports/',
                          '/admin-panel/reports/?tab=resolved', '/admin-panel/store/'])

    def test_admin_actions(self):
        admin = self.login('admin@bonovela.app')
        reader = User.objects.get(email='reader@bonovela.app')
        self.client.post(f'/admin-panel/users/{reader.pk}/', {'action': 'suspend'})
        reader.refresh_from_db()
        self.assertTrue(reader.is_suspended)
        self.client.post(f'/admin-panel/users/{reader.pk}/', {'action': 'role', 'role': 'author'})
        reader.refresh_from_db()
        self.assertEqual(reader.role, User.ROLE_AUTHOR)

        # Can't demote yourself.
        self.client.post(f'/admin-panel/users/{admin.pk}/', {'action': 'role', 'role': 'reader'})
        admin.refresh_from_db()
        self.assertEqual(admin.role, User.ROLE_ADMIN)

        story = Story.objects.visible_in_app().first()
        self.client.post(f'/admin-panel/books/{story.pk}/', {'action': 'remove'})
        self.assertFalse(Story.objects.visible_in_app().filter(pk=story.pk).exists())

        report = Report.objects.create(reporter=reader, story=story, reason='Copied')
        self.client.post(f'/admin-panel/reports/{report.pk}/', {'action': 'resolve'})
        report.refresh_from_db()
        self.assertTrue(report.is_resolved)
