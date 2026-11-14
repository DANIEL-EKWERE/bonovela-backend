from datetime import timedelta

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone
from django.test import TestCase, override_settings

from apps.catalog.models import Chapter, Genre, Story, Tag
from apps.editorial.models import ContractApplication
from apps.engagement.models import Report
from apps.users.models import User

# Tests seed only a few chapters per novel.
FEW_CHAPTERS = override_settings(BONOVELA={**settings.BONOVELA, 'MIN_LIVE_CHAPTERS': 1})

PASSWORD = 'bonovela1'


@FEW_CHAPTERS
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
        details = {'novel_type': 'original', 'is_short': '0', 'genre': Genre.objects.filter(is_active=True).first().pk,
                   'tags': [tag.pk], 'action': 'next'}
        r = self.client.post(f'/author/stories/{story.pk}/info/?step=2', {**details, 'synopsis': 'word ' * 49})
        self.assertContains(r, 'Synopsis must be 50–300 words')  # too short
        retired = Genre.objects.get(name='Urban')
        r = self.client.post(f'/author/stories/{story.pk}/info/?step=2', {**details, 'genre': retired.pk,
                                                                         'synopsis': 'word ' * 60})
        self.assertEqual(r.status_code, 200)  # retired genres can't be picked
        r = self.client.post(f'/author/stories/{story.pk}/info/?step=2', {**details, 'synopsis': 'word ' * 60})
        self.assertRedirects(r, f'/author/stories/{story.pk}/info/?step=3')
        story.refresh_from_db()
        self.assertTrue(story.is_info_complete)
        self.assertNotContains(self.client.get(f'/author/stories/{story.pk}/info/?step=2'), 'Participate')

        # Chapters must be 600–2,000 words to go out; short ones stay as drafts.
        self.client.post(f'/author/stories/{story.pk}/chapters/new/', {
            'title': 'Too short', 'content': 'She ran. ' * 299, 'action': 'release'})
        self.assertEqual(story.chapters.get(title='Too short').status, Chapter.STATUS_DRAFT)
        r = self.client.post(f'/author/stories/{story.pk}/chapters/new/', {
            'title': 'Too long', 'content': 'word ' * 2001, 'action': 'save'})
        self.assertFalse(story.chapters.filter(title='Too long').exists())
        self.assertContains(r, 'Chapters must be 600–2,000 words')

        # The contract application only opens at 5,000 published words.
        self.client.post(f'/author/stories/{story.pk}/chapters/new/', {
            'title': 'Chapter 1', 'content': 'She ran. ' * 1000, 'action': 'release'})
        self.assertNotContains(self.client.get(f'/author/stories/{story.pk}/signing/'), 'Apply for Contract</a>')
        self.assertRedirects(self.client.get(f'/author/stories/{story.pk}/contract/'),
                             f'/author/stories/{story.pk}/signing/')
        for i in range(2):
            self.client.post(f'/author/stories/{story.pk}/chapters/new/', {
                'title': f'Chapter {i + 2}', 'content': 'She ran. ' * 1000, 'action': 'release'})
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

        self.assertContains(self.client.get(f'/author/stories/{story.pk}/signing/'), 'Apply for Contract</a>')
        from apps.editorial.testdata import application_data
        novels_se = User.objects.get(email='se@bonovela.app')
        shorts_se = User.objects.get(email='se5@bonovela.app')  # Short Stories desk
        page = self.client.get(f'/author/stories/{story.pk}/contract/')
        self.assertContains(page, novels_se.display_name)
        self.assertNotContains(page, shorts_se.display_name)
        # Under 18 without guardian details, and an editor from the wrong desk: sent back.
        r = self.client.post(f'/author/stories/{story.pk}/contract/',
                             application_data(shorts_se, is_minor='on'))
        self.assertContains(r, 'Required for authors under 18.')
        self.assertContains(r, 'Choose one of the listed editors.')
        self.assertFalse(story.applications.exists())
        r = self.client.post(f'/author/stories/{story.pk}/contract/', application_data(novels_se))
        self.assertRedirects(r, f'/author/stories/{story.pk}/signing/')
        app = story.applications.get()
        self.assertEqual((app.status, app.get_status_display()), (ContractApplication.STATUS_PENDING, 'Pending review'))
        self.assertEqual((app.legal_name, app.phone, app.contract_type), ('Ada Ngozi Okafor', '+2348012345678',
                                                                          'non_exclusive'))
        story.refresh_from_db()
        self.assertEqual(story.editor, novels_se)  # unassigned book goes to the chosen editor
        # ID photos are private: not under MEDIA_ROOT, only editors can open them.
        from django.conf import settings
        self.assertFalse(app.id_front.path.startswith(str(settings.MEDIA_ROOT)))
        url = f'/editorial/applications/{app.pk}/id/front/'
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(novels_se)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(f'/editorial/submissions/{app.pk}/'), 'Ada Ngozi Okafor')

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


    def test_payout_info_needs_bank_details_and_confirmation(self):
        from apps.wallet.models import PayoutMethod
        author = self.login('author@bonovela.app')
        r = self.client.get('/author/income/')
        self.assertContains(r, 'IMPORTANT NOTICE')
        self.assertNotContains(r, 'Signing Bonus')
        self.assertNotContains(r, 'Completion Bonus')
        bank = {'method': 'bank', 'country': 'Nigeria', 'currency': 'USD', 'bank_name': 'FCMB',
                'swift_code': 'FCMBNGLA', 'account_detail': '1047547276', 'account_name': 'Joy Mercy Osemeke',
                'address': '12 Agbara Road', 'city': 'Lagos', 'state': 'Lagos', 'postal_code': '100001'}
        self.client.post('/author/income/', bank)  # not confirmed in the dialog
        self.client.post('/author/income/', {**bank, 'city': '', 'confirmed': 'True'})  # missing bank field
        self.assertFalse(PayoutMethod.objects.filter(author=author).exists())
        self.client.post('/author/income/', {**bank, 'confirmed': 'True'})
        self.assertEqual(PayoutMethod.objects.get(author=author).swift_code, 'FCMBNGLA')

    def test_novels_need_twenty_chapters_to_go_live(self):
        story = Story.objects.visible_in_app().filter(is_short=False).first()  # seeded with 4 chapters
        short = Story.objects.visible_in_app().filter(is_short=True).first()
        with override_settings(BONOVELA={**settings.BONOVELA, 'MIN_LIVE_CHAPTERS': 20}):
            self.assertFalse(Story.objects.visible_in_app().filter(pk=story.pk).exists())
            self.assertTrue(Story.objects.visible_in_app().filter(pk=short.pk).exists())

    def test_publish_scheduled_publishes_due_chapters_only(self):
        from io import StringIO
        story = Story.objects.visible_in_app().filter(is_short=False).first()
        due = Chapter.objects.create(story=story, number=900, content='word ' * 700,
                                     status=Chapter.STATUS_SCHEDULED, scheduled_for=timezone.now() - timedelta(minutes=1))
        later = Chapter.objects.create(story=story, number=901, content='word ' * 700,
                                       status=Chapter.STATUS_SCHEDULED, scheduled_for=timezone.now() + timedelta(hours=1))
        short = Chapter.objects.create(story=story, number=902, content='word ' * 100,
                                       status=Chapter.STATUS_SCHEDULED, scheduled_for=timezone.now() - timedelta(minutes=1))
        call_command('publish_scheduled', stdout=StringIO())
        for c in (due, later, short):
            c.refresh_from_db()
        self.assertEqual((due.status, later.status, short.status),
                         (Chapter.STATUS_PUBLISHED, Chapter.STATUS_SCHEDULED, Chapter.STATUS_DRAFT))
        self.assertTrue(story.author.inbox.filter(title__contains="902 wasn't published").exists())

@FEW_CHAPTERS
class EditorialDashboardTests(TestCase):
    """Senior Editor and Chief Editor workspaces (there is no AE level)."""

    @classmethod
    def setUpTestData(cls):
        call_command('seed_bonovela', chapters=12, episodes=4, verbosity=0)

    def setUp(self):
        self.se = User.objects.get(email='se@bonovela.app')
        self.se2 = User.objects.get(email='se2@bonovela.app')
        self.story = Story.objects.filter(editor=self.se, is_signed=True).first()
        self.story.is_signed = False
        self.story.save()
        self.app = ContractApplication.objects.create(story=self.story, message='Please sign me')

    def as_role(self, role):
        self.client.logout()
        self.assertTrue(self.client.login(username=f'{role}@bonovela.app', password=PASSWORD))
        return User.objects.get(email=f'{role}@bonovela.app')

    def get_ok(self, role, urls):
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, f'{role} {url} → {r.status_code}')

    def test_no_assistant_editor_role(self):
        self.assertNotIn('ae', dict(User.ROLE_CHOICES))
        self.assertEqual(User.EDITOR_ROLES, (User.ROLE_SE, User.ROLE_CE))
        self.assertNotIn('ae_approved', dict(ContractApplication.STATUS_CHOICES))

    def test_pages_render(self):
        from apps.editorial.models import CompletionApplication
        ch = self.story.chapters.first()
        comp = CompletionApplication.objects.first()
        common = ['/editorial/', '/editorial/authors/', '/editorial/authors/?f=at_risk', '/editorial/books/',
                  '/editorial/books/?f=at_risk', '/editorial/books/?f=completed', '/editorial/books/?f=unassigned',
                  '/editorial/submissions/', '/editorial/submissions/?tab=open', '/editorial/submissions/?tab=flagged',
                  f'/editorial/submissions/{self.app.pk}/', f'/editorial/submissions/{self.app.pk}/?chapter={ch.number}',
                  f'/editorial/books/{self.story.pk}/', f'/editorial/books/{self.story.pk}/?chapter={ch.number}',
                  f'/editorial/authors/{self.story.author_id}/', '/editorial/completion/', '/editorial/completion/?tab=done',
                  '/editorial/plagiarism/', f'/editorial/plagiarism/?book={self.story.pk}', '/editorial/duplicates/',
                  f'/editorial/duplicates/?book={self.story.pk}', '/editorial/duplicates/?scan=1',
                  '/editorial/recruitment/', '/editorial/recruitment/?stage=contracted', '/editorial/commission/',
                  '/editorial/escalations/', '/editorial/escalations/?tab=recs', '/editorial/messages/',
                  '/editorial/reports/', '/editorial/reports/?period=month', '/editorial/promotions/']
        chief = ['/editorial/team/', f'/editorial/team/{self.se.pk}/', '/editorial/performance/']
        for role in ('se', 'ce', 'admin'):
            self.as_role(role)
            self.get_ok(role, common + ([] if role == 'se' else chief))
        if comp:
            self.as_role('ce')
            self.get_ok('ce', [f'/editorial/completion/{comp.pk}/'])

    def test_se_scope_and_chief_only_pages(self):
        other_book = Story.objects.filter(editor=self.se2).first()
        self.as_role('se')
        for url in ('/editorial/team/', '/editorial/performance/', f'/editorial/team/{self.se2.pk}/'):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.client.get(f'/editorial/books/{other_book.pk}/').status_code, 403)
        self.as_role('ce')
        self.assertEqual(self.client.get(f'/editorial/books/{other_book.pk}/').status_code, 200)

    def test_navigation(self):
        self.as_role('se')
        nav = [i['label'] for i in self.client.get('/editorial/').context['nav']]
        self.assertEqual(nav[:4], ['Overview', 'Authors & Accounts', 'Book Management', 'Submission History'])
        self.assertIn('AI Completion Review', nav)
        self.assertNotIn('Senior Editors', nav)
        self.assertNotIn('Team Performance', nav)
        self.as_role('ce')
        nav = [i['label'] for i in self.client.get('/editorial/').context['nav']]
        self.assertIn('Senior Editors', nav)
        self.assertIn('Team Performance', nav)

    def test_signing_flow_se_then_ce(self):
        url = f'/editorial/submissions/{self.app.pk}/'
        # Another SE can't decide on a book that isn't theirs.
        self.as_role('se2')
        self.assertEqual(self.client.post(url, {'action': 'se_approve'}).status_code, 403)
        self.as_role('se')
        self.client.post(url, {'action': 'ce_contract'})  # only the CE sends contracts
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, ContractApplication.STATUS_PENDING)
        self.client.post(url, {'action': 'se_approve', 'feedback': 'Strong hook'})
        self.app.refresh_from_db()
        self.assertEqual((self.app.status, self.app.se), (ContractApplication.STATUS_SE_APPROVED, self.se))
        self.as_role('ce')
        self.client.post(url, {'action': 'ce_contract', 'contract_type': 'exclusive', 'signing_bonus': '25.50'})
        self.app.refresh_from_db()
        self.assertEqual(self.app.signing_bonus_cents, 0)  # no signing bonus any more
        self.client.logout()
        self.client.force_login(self.story.author)
        self.client.post(f'/author/stories/{self.story.pk}/signing/', {'action': 'sign', 'agree': 'on'})
        self.story.refresh_from_db()
        self.assertTrue(self.story.is_signed)

    def test_assign_take_transfer_and_suspend_rules(self):
        from apps.editorial.models import Recommendation
        url = f'/editorial/books/{self.story.pk}/action/'
        self.as_role('se')
        # An SE can't hand a book to another SE directly…
        self.client.post(url, {'action': 'assign', 'editor': self.se2.pk})
        self.story.refresh_from_db()
        self.assertEqual(self.story.editor, self.se)
        # …it becomes a recommendation the CE approves.
        self.client.post(url, {'action': 'recommend_transfer', 'editor': self.se2.pk, 'text': 'Genre fit'})
        rec = Recommendation.objects.get(kind='transfer_out', story=self.story)
        self.client.post(url, {'action': 'remove'})  # SEs can only recommend suspension
        self.story.refresh_from_db()
        self.assertFalse(self.story.is_removed)
        # An SE can take an unassigned book.
        free = Story.objects.filter(editor__isnull=True).exclude(status=Story.STATUS_DRAFT).first()
        self.client.post(f'/editorial/books/{free.pk}/action/', {'action': 'assign', 'editor': self.se.pk})
        free.refresh_from_db()
        self.assertEqual(free.editor, self.se)
        self.as_role('ce')
        self.client.post('/editorial/escalations/', {'action': 'approve', 'id': rec.pk, 'tab': 'recs'})
        self.story.refresh_from_db()
        self.assertEqual(self.story.editor, self.se2)
        self.client.post(url, {'action': 'remove'})
        self.story.refresh_from_db()
        self.assertTrue(self.story.is_removed)

    def test_chapter_review(self):
        ch = self.story.chapters.filter(status=Chapter.STATUS_PUBLISHED).last()
        Chapter.objects.filter(pk=ch.pk).update(review_status=Chapter.REVIEW_PENDING)
        url = f'/editorial/books/{self.story.pk}/action/'
        self.as_role('se')
        self.client.post(url, {'action': 'revise_chapter', 'chapter': ch.pk, 'text': 'Fix the typo'})
        ch.refresh_from_db()
        self.assertEqual((ch.status, ch.review_status), (Chapter.STATUS_REVISION, Chapter.REVIEW_FLAGGED))
        self.assertTrue(self.story.author.inbox.filter(title__contains=f'Chapter {ch.number}').exists())
        self.client.post(url, {'action': 'review_approve_all'})
        self.assertFalse(self.story.chapters.filter(status=Chapter.STATUS_PUBLISHED,
                                                    review_status=Chapter.REVIEW_PENDING).exists())

    def test_targets_and_messages(self):
        from apps.editorial.models import Target, TeamMessage
        self.as_role('ce')
        self.client.post('/editorial/performance/', {'editor': self.se.pk, 'recruitment': 7, 'contracts': 3})
        self.assertEqual(Target.objects.get(user=self.se, metric='recruitment', month__month=timezone.now().month).value, 7)
        self.client.post('/editorial/messages/', {'to': 'team', 'kind': 'announcement', 'title': 'Hi everyone', 'body': 'Go!'})
        self.as_role('se')
        self.assertContains(self.client.get('/editorial/messages/'), 'Hi everyone')
        self.assertEqual(self.client.post('/editorial/messages/', {'to': 'team', 'title': 'x', 'body': 'y'}).status_code, 403)
        ce = User.objects.get(email='ce@bonovela.app')
        self.client.post('/editorial/messages/', {'to': ce.pk, 'kind': 'message', 'title': 'Question', 'body': '?'})
        self.assertTrue(TeamMessage.objects.filter(sender=self.se, recipient=ce, title='Question').exists())

    def test_escalation_confidential(self):
        from apps.editorial.models import Escalation
        self.as_role('se')
        self.client.post('/editorial/escalations/', {'action': 'raise', 'category': 'misconduct',
                                                    'subject': 'Zq7 confidential case', 'body': 'x', 'confidential': 'on'})
        self.assertTrue(Escalation.objects.filter(subject='Zq7 confidential case', is_confidential=True).exists())
        self.as_role('se2')
        self.assertNotContains(self.client.get('/editorial/escalations/'), 'Zq7 confidential case')
        self.as_role('ce')
        self.assertContains(self.client.get('/editorial/escalations/'), 'Zq7 confidential case')

    def test_plagiarism_and_duplicates(self):
        from apps.editorial import similarity
        other = Story.objects.exclude(pk=self.story.pk).filter(editor=self.se, is_signed=True).first()
        original = ('The lighthouse keeper counted seventeen gulls on the broken pier before the storm swallowed '
                    'the harbour and the bells of Saint Ives rang out across the grey copper water at dawn. ') * 4
        self.story.chapters.create(number=900, title='Orig', content=original, status=Chapter.STATUS_PUBLISHED,
                                   published_at=timezone.now() - timedelta(days=3))
        other.chapters.create(number=900, title='Copy', content=original, status=Chapter.STATUS_PUBLISHED,
                              published_at=timezone.now())
        self.story.chapters.create(number=901, title='Again', content=original, status=Chapter.STATUS_PUBLISHED)
        report = similarity.plagiarism_report(other)
        row = next(r for r in report['rows'] if r['chapter'].number == 900)
        self.assertEqual(row['match'].story, self.story)
        self.assertFalse(row['ours_first'])
        dup = similarity.duplicate_report(self.story)
        self.assertEqual({dup['pairs'][0]['a'].number, dup['pairs'][0]['b'].number}, {900, 901})
        self.assertTrue(similarity.check_text(original)['rows'])
        clean = Story.objects.filter(is_signed=True).exclude(pk__in=[self.story.pk, other.pk]).first()
        self.assertFalse(similarity.plagiarism_report(clean)['rows'])
        self.as_role('se')
        self.assertContains(self.client.get(f'/editorial/plagiarism/?book={other.pk}'), self.story.title)

    def test_completion_review_flow(self):
        from apps.editorial.models import CompletionApplication
        from apps.wallet.models import AuthorEarning
        Story.objects.filter(pk=self.story.pk).update(status=Story.STATUS_COMPLETED, is_signed=True)
        self.client.force_login(self.story.author)
        self.client.post(f'/author/stories/{self.story.pk}/signing/', {'action': 'completion', 'message': 'Done!'})
        app = CompletionApplication.objects.get(story=self.story)
        self.as_role('se')
        self.client.post(f'/editorial/completion/{app.pk}/', {'action': 'analyse'})
        app.refresh_from_db()
        self.assertTrue(app.analysis['checks'])
        self.assertEqual(app.ai_review, '')  # no API key in tests
        self.client.post(f'/editorial/completion/{app.pk}/', {'action': 'recommend', 'note': 'Solid ending'})
        self.client.post(f'/editorial/completion/{app.pk}/', {'action': 'approve', 'bonus': '50'})  # SE can't
        app.refresh_from_db()
        self.assertEqual(app.status, CompletionApplication.STATUS_RECOMMENDED)
        self.as_role('ce')
        self.client.post(f'/editorial/completion/{app.pk}/', {'action': 'approve', 'bonus': '50'})
        app.refresh_from_db()
        self.assertEqual(app.status, CompletionApplication.STATUS_APPROVED)
        self.assertFalse(AuthorEarning.objects.filter(author=self.story.author, type='completion').exists())

    def test_commission_booking(self):
        from apps.editorial import team
        from apps.wallet.models import AuthorEarning, Transaction
        reader = User.objects.get(email='reader@bonovela.app')
        last_month = (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=15)
        tx = Transaction.objects.create(user=reader, kind=Transaction.KIND_UNLOCK, title='x', coins=-1000,
                                        story=self.story)
        Transaction.objects.filter(pk=tx.pk).update(created_at=timezone.make_aware(
            timezone.datetime.combine(last_month, timezone.datetime.min.time())))
        self.assertEqual(team.commission(self.se, last_month)['total'], 50)  # SE: 5% of 1000
        self.as_role('finance')
        self.client.post('/finance/payouts/commission/')
        self.client.post('/finance/payouts/commission/')  # idempotent
        self.assertEqual(AuthorEarning.objects.filter(author=self.se, type='editor_commission').count(), 1)


    def test_shelves_page_se_suggests_ce_approves_and_renames(self):
        from apps.editorial.models import Shelf, ShelfBook
        shelf = Shelf.objects.get(title='Love on Top')
        story = Story.objects.filter(editor=self.se, is_signed=True, is_short=False).first()
        self.as_role('se')
        self.assertEqual(self.client.get('/editorial/shelves/').status_code, 200)
        self.client.post('/editorial/shelves/?kind=novel', {'action': 'add', 'shelf': shelf.pk, 'story': story.pk})
        entry = ShelfBook.objects.get(shelf=shelf, story=story)
        self.assertEqual(entry.status, ShelfBook.STATUS_PENDING)
        self.client.post('/editorial/shelves/', {'action': 'rename', 'type': 'shelf', 'id': shelf.pk, 'name': 'Hacked'})
        self.as_role('ce')
        self.assertContains(self.client.get('/editorial/shelves/'), '1 waiting for approval')
        self.client.post('/editorial/shelves/', {'action': 'approve', 'entry': entry.pk})
        self.client.post('/editorial/shelves/', {'action': 'rename', 'type': 'shelf', 'id': shelf.pk,
                                                 'name': 'Love On Top', 'layout': 'cover_row'})
        entry.refresh_from_db()
        shelf.refresh_from_db()
        self.assertEqual(entry.status, ShelfBook.STATUS_APPROVED)
        self.assertEqual((shelf.title, shelf.layout), ('Love On Top', 'cover_row'))  # SE rename was refused
        self.assertEqual(self.client.get('/editorial/shelves/?kind=short').status_code, 200)

@FEW_CHAPTERS
class ResubmissionFlagTests(TestCase):
    """Books turned down by one editor and sent again are flagged."""

    @classmethod
    def setUpTestData(cls):
        call_command('seed_bonovela', chapters=4, episodes=2, verbosity=0)

    def as_role(self, role):
        self.client.logout()
        self.assertTrue(self.client.login(username=f'{role}@bonovela.app', password=PASSWORD))

    def test_seeded_resubmission_shows_flag_and_details(self):
        app = ContractApplication.objects.get(story__title='Bride of the Lantern Delta')
        self.assertEqual(app.prior_flag, ContractApplication.FLAG_REJECTED)
        self.as_role('se')
        self.assertContains(self.client.get('/editorial/submissions/?tab=flagged'), 'Bride of the Lantern Delta')
        self.assertContains(self.client.get('/editorial/'), 'Flagged submissions')
        r = self.client.get(f'/editorial/submissions/{app.pk}/')
        for text in ('Previously rejected', "another Senior Editor", 'See more info', 'The Lantern Bride',
                     'Bisi (SE)', 'lift whole scenes', 'Shared IP address (102.89.34.17)', 'Opening chapters'):
            self.assertContains(r, text)
        self.assertContains(self.client.get(f'/editorial/books/{app.story_id}/'), 'See more info')
        self.assertContains(self.client.get(f'/editorial/authors/{app.story.author_id}/'), 'Also used by')

    def test_ip_recorded_on_signup_login_and_submit(self):
        from rest_framework.test import APIClient
        from apps.users.models import IPLog
        c = APIClient(REMOTE_ADDR='41.58.10.20')
        c.post('/api/auth/register/', {'email': 'ipt@x.com', 'password': 'pink_reads27', 'name': 'Ip'}, format='json')
        u = User.objects.get(email='ipt@x.com')
        self.assertEqual(IPLog.objects.get(user=u).first_event, 'signup')
        APIClient(REMOTE_ADDR='41.58.10.21').post('/api/auth/token/', {'email': 'ipt@x.com', 'password': 'pink_reads27'},
                                                  format='json')
        self.assertEqual(set(IPLog.objects.filter(user=u).values_list('ip', flat=True)), {'41.58.10.20', '41.58.10.21'})
        self.client.post('/login/', {'username': 'reader@bonovela.app', 'password': PASSWORD}, REMOTE_ADDR='41.58.10.22')
        self.assertTrue(IPLog.objects.filter(user__email='reader@bonovela.app', ip='41.58.10.22').exists())

    def test_rejection_then_new_account_same_ip_is_flagged(self):
        from apps.editorial import history
        from apps.users.models import IPLog
        se2 = User.objects.get(email='se2@bonovela.app')
        se = User.objects.get(email='se@bonovela.app')
        story = Story.objects.filter(editor=se2, is_signed=True).exclude(tags=None).first()
        Story.objects.filter(pk=story.pk).update(is_signed=False, synopsis=' '.join(['word'] * 40))
        story.refresh_from_db()
        # Author applies from the web (IP recorded), SE2 rejects.
        from django.test import RequestFactory
        request = RequestFactory().post('/', REMOTE_ADDR='197.210.5.9')
        app = history.submit(story, request)
        self.assertEqual(app.submitted_ip, '197.210.5.9')
        self.assertEqual(app.prior_flag, '')
        self.as_role('se2')
        self.client.post(f'/editorial/submissions/{app.pk}/', {'action': 'se_reject', 'feedback': 'Weak hook'})
        app.refresh_from_db()
        self.assertEqual((app.rejected_by, app.rejection_reason), (se2, 'Weak hook'))
        # Same author tries again with the same book: flagged.
        again = history.submit(story, message='Second try')
        self.assertEqual(again.prior_flag, ContractApplication.FLAG_REJECTED)
        self.assertEqual(again.prior_matches[0]['reasons'][0]['kind'], 'same_book')
        # A new account on the same IP sends a different book to se's team: flagged as another team.
        other = User.objects.create_user(email='newpen@x.com', username='newpen', password=PASSWORD,
                                         role=User.ROLE_AUTHOR)
        IPLog.objects.create(user=other, ip='197.210.5.9', first_event='signup', last_event='signup')
        other_story = Story.objects.filter(editor=se).exclude(author=story.author).first()
        Story.objects.filter(pk=other_story.pk).update(author=other)
        other_story.refresh_from_db()
        fresh = history.submit(other_story)
        m = fresh.prior_matches[0]
        self.assertEqual(fresh.prior_flag, ContractApplication.FLAG_REJECTED)
        self.assertEqual(m['rejected_by'], se2.display_name)
        self.assertEqual(m['reason'], 'Weak hook')
        self.assertTrue(m['other_team'])
        self.assertIn('shared_ip', [r['kind'] for r in m['reasons']])

    def test_clean_submission_is_not_flagged(self):
        from apps.editorial import history
        story = Story.objects.filter(editor__email='se4@bonovela.app').first()
        self.assertEqual(history.submit(story).prior_flag, '')


@FEW_CHAPTERS
class NewDashboardTests(TestCase):
    """Drama Studio, Marketing and Finance."""

    @classmethod
    def setUpTestData(cls):
        call_command('seed_bonovela', chapters=2, episodes=8, demo_sales=True, verbosity=0)

    def as_role(self, role):
        self.client.logout()
        self.assertTrue(self.client.login(username=f'{role}@bonovela.app', password=PASSWORD))
        return User.objects.get(email=f'{role}@bonovela.app')

    def get_ok(self, *urls):
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, f'{url} → {r.status_code}')

    def test_role_redirects_and_access(self):
        for role, home in [('creator', '/drama-studio/'), ('marketing', '/marketing/'), ('finance', '/finance/')]:
            self.as_role(role)
            self.assertRedirects(self.client.get('/'), home)
        self.as_role('finance')
        self.assertEqual(self.client.get('/marketing/').status_code, 403)
        self.assertEqual(self.client.get('/drama-studio/').status_code, 403)
        self.as_role('author')
        self.assertEqual(self.client.get('/finance/').status_code, 403)

    # ── Drama Studio ─────────────────────────────────────────────────────────
    def test_drama_pages_render(self):
        from apps.drama.models import Drama
        self.as_role('creator')
        d = Drama.objects.first()
        ep = d.episodes.first()
        self.get_ok('/drama-studio/', '/drama-studio/?f=live', '/drama-studio/new/', '/drama-studio/analytics/',
                    '/drama-studio/earnings/', f'/drama-studio/{d.pk}/', f'/drama-studio/{d.pk}/edit/',
                    f'/drama-studio/{d.pk}/stats/', f'/drama-studio/{d.pk}/episodes/new/',
                    f'/drama-studio/{d.pk}/episodes/{ep.pk}/')

    def test_creator_uploads_and_publishes(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.drama.models import Drama, Episode
        self.as_role('creator')
        story = Story.objects.filter(is_signed=True).first()
        r = self.client.post('/drama-studio/new/', {'title': 'Pink Skies', 'cover_index': 2, 'story': story.pk,
                                                    'synopsis': 'A short drama.', 'tags': [Tag.objects.first().pk]})
        d = Drama.objects.get(title='Pink Skies')
        self.assertRedirects(r, f'/drama-studio/{d.pk}/')
        self.assertEqual(d.kind, Drama.KIND_ADAPTED)

        # Going live needs a published episode.
        self.client.post(f'/drama-studio/{d.pk}/action/', {'action': 'publish'})
        d.refresh_from_db()
        self.assertFalse(d.is_published)

        # Wrong file type is rejected; an mp4 is accepted.
        bad = SimpleUploadedFile('ep1.txt', b'hello', content_type='text/plain')
        self.client.post(f'/drama-studio/{d.pk}/episodes/new/', {'number': 1, 'video': bad, 'action': 'publish'})
        self.assertFalse(d.episodes.exists())
        video = SimpleUploadedFile('ep1.mp4', b'\x00\x00\x00\x18ftypmp42', content_type='video/mp4')
        self.client.post(f'/drama-studio/{d.pk}/episodes/new/', {'number': 1, 'title': 'Pilot', 'video': video,
                                                                 'duration_seconds': 75, 'action': 'publish'})
        ep = d.episodes.get()
        self.assertEqual(ep.status, Episode.STATUS_PUBLISHED)
        self.addCleanup(ep.video.delete, save=False)

        # Duplicate number is refused.
        self.client.post(f'/drama-studio/{d.pk}/episodes/new/', {'number': 1, 'video_url': 'https://cdn.x/e.mp4'})
        self.assertEqual(d.episodes.count(), 1)

        self.client.post(f'/drama-studio/{d.pk}/action/', {'action': 'publish'})
        d.refresh_from_db()
        self.assertTrue(d.is_published)

    def test_creator_cannot_edit_others_drama(self):
        from apps.drama.models import Drama
        other = User.objects.create_user(email='c2@x.com', username='c2', password=PASSWORD, role=User.ROLE_CREATOR)
        d = Drama.objects.create(creator=other, title='Theirs')
        self.as_role('creator')
        self.assertEqual(self.client.get(f'/drama-studio/{d.pk}/').status_code, 404)

    # ── Marketing ────────────────────────────────────────────────────────────
    def test_marketing_pages_and_actions(self):
        from apps.engagement.models import InboxMessage
        from apps.marketing.models import Banner
        from apps.wallet.models import RedeemCode
        self.as_role('marketing')
        b = Banner.objects.first()
        self.get_ok('/marketing/', '/marketing/banners/', '/marketing/banners/?placement=drama',
                    '/marketing/banners/new/', f'/marketing/banners/{b.pk}/', '/marketing/codes/',
                    '/marketing/broadcasts/')

        story = Story.objects.visible_in_app().first()
        r = self.client.post('/marketing/banners/new/', {
            'title': 'Autumn reads', 'color_index': 1, 'placement': 'discover', 'target': 'story', 'story': story.pk,
            'order': 0, 'starts_at': '2026-01-01T10:00', 'is_active': 'on'})
        self.assertRedirects(r, '/marketing/banners/')
        self.assertTrue(Banner.objects.filter(title='Autumn reads').exists())
        # A story banner needs a story.
        self.client.post('/marketing/banners/new/', {'title': 'Broken', 'color_index': 1, 'placement': 'discover',
                                                     'target': 'story', 'order': 0, 'starts_at': '2026-01-01T10:00'})
        self.assertFalse(Banner.objects.filter(title='Broken').exists())

        self.client.post('/marketing/codes/', {'code': 'fall25', 'bonus': 25, 'max_uses': 1, 'days': 10})
        code = RedeemCode.objects.get(code='FALL25')
        self.assertTrue(code.is_usable)
        reader = User.objects.get(email='reader@bonovela.app')
        code.redeemed_by.add(reader)
        self.assertFalse(code.is_usable)  # max_uses reached

        before = InboxMessage.objects.filter(user=reader).count()
        self.client.post('/marketing/broadcasts/', {'title': 'Hello', 'body': 'New dramas!', 'audience': 'readers',
                                                    'bonus': 5})
        self.assertEqual(InboxMessage.objects.filter(user=reader).count(), before + 1)

    # ── Finance ──────────────────────────────────────────────────────────────
    def test_finance_stage_one_pages_settings_expenses_team_audit(self):
        from django.core.exceptions import PermissionDenied
        from apps.finance.models import AuditEntry, Expense, FinanceMember, FinanceSettings
        lead = self.as_role('finance')  # finance staff start as Finance Lead
        self.get_ok('/finance/', '/finance/expenses/', '/finance/settings/', '/finance/audit/', '/finance/team/',
                    '/finance/payouts/?tab=balances', '/finance/expenses/?month=2026-01')
        self.assertEqual(FinanceMember.objects.get(user=lead).role, FinanceMember.ROLE_LEAD)
        # Settings: Management only, every change audited.
        self.client.post('/finance/settings/', {'author_share_pct': '60', 'processing_fee_pct': '3',
                                                'minimum_payout_cents': '100', 'chargeback_reserve_pct': '5'})
        self.assertEqual(FinanceSettings.current().author_share_pct, 50)
        self.client.force_login(User.objects.get(email='admin@bonovela.app'))
        self.client.post('/finance/settings/', {'author_share_pct': '60', 'processing_fee_pct': '3',
                                                'minimum_payout_cents': '100', 'chargeback_reserve_pct': '5',
                                                'two_person_approval': 'on'})
        self.assertEqual(FinanceSettings.current().author_share_pct, 60)
        self.assertTrue(AuditEntry.objects.filter(action='Changed author share', detail='50% → 60%').exists())
        self.client.post('/finance/settings/', {'author_share_pct': '160', 'processing_fee_pct': '3',
                                                'minimum_payout_cents': '100', 'chargeback_reserve_pct': '5'})
        self.assertEqual(FinanceSettings.current().author_share_pct, 60)  # out of range: refused
        # Expenses: add, show on the Treasury, pay, delete; all audited.
        self.client.post('/finance/expenses/', {'action': 'add', 'title': 'Northwind Cloud', 'category': 'Hosting',
                                                'amount': '4120', 'date': '2026-11-11', 'status': 'due'})
        e = Expense.objects.get(title='Northwind Cloud')
        self.assertEqual((e.amount_cents, e.status), (412000, 'due'))
        self.assertContains(self.client.get('/finance/expenses/?month=2026-11'), 'Northwind Cloud')
        self.assertContains(self.client.get('/finance/'), 'Northwind Cloud')
        self.client.post('/finance/expenses/', {'action': 'paid', 'expense': e.pk})
        e.refresh_from_db()
        self.assertEqual(e.status, 'paid')
        self.client.post('/finance/expenses/', {'action': 'delete', 'expense': e.pk})
        self.assertFalse(Expense.objects.exists())
        self.assertEqual(AuditEntry.objects.filter(category='Expense').count(), 3)
        # Team: add a Viewer, who can look but not act.
        viewer = User.objects.get(email='marketing@bonovela.app')
        self.client.post('/finance/team/', {'action': 'add', 'email': viewer.email, 'role': 'viewer'})
        self.client.force_login(viewer)
        self.assertEqual(self.client.get('/finance/expenses/').status_code, 200)
        self.client.post('/finance/expenses/', {'action': 'add', 'title': 'X', 'category': 'Other',
                                                'amount': '1', 'date': '2026-11-11'})
        self.assertFalse(Expense.objects.exists())
        self.assertEqual(self.client.get('/finance/team/').status_code, 200)
        # Not on the team: no access.
        self.client.force_login(User.objects.get(email='reader@bonovela.app'))
        self.assertEqual(self.client.get('/finance/').status_code, 403)
        # The log is append-only.
        entry = AuditEntry.objects.first()
        entry.detail = 'changed'
        with self.assertRaises(PermissionDenied):
            entry.save()
        with self.assertRaises(PermissionDenied):
            entry.delete()

    def test_finance_pages_and_csv(self):
        self.as_role('finance')
        self.get_ok('/finance/', '/finance/transactions/', '/finance/transactions/?kind=paid&from=2026-01-01&q=reader',
                    '/finance/balances/', '/finance/balances/?due=1', '/finance/payouts/', '/finance/payouts/?tab=paid',
                    '/finance/statements/', '/finance/reports/', '/finance/revenue/', '/finance/revenue/?month=2026-01',
                    '/finance/search/', '/finance/search/?q=reader', '/finance/search/?q=TX-1&type=Transaction')
        for url in ['/finance/transactions/?export=csv', '/finance/balances/?export=csv', '/finance/reports/?download=pl',
                    '/finance/reports/?download=tax', '/finance/revenue/?export=csv']:
            r = self.client.get(url)
            self.assertEqual(r['Content-Type'], 'text/csv', url)
            self.assertGreater(len(r.content.splitlines()), 1, url)
        r = self.client.get('/finance/payouts/?export=csv')  # no payouts yet: header only
        self.assertEqual(r.content.splitlines()[0], b'id,email,name,usd,period_end,method,reference')

    def test_finance_stage_two_revenue_search_reports_and_rules(self):
        from apps.catalog.models import Story
        from apps.finance.models import AuditEntry, Expense, FinanceSettings
        from apps.wallet import services as wallet
        from apps.wallet.models import AuthorEarning, Payout, Transaction
        reader = User.objects.get(email='reader@bonovela.app')
        Transaction.objects.create(user=reader, kind=Transaction.KIND_TOP_UP, title='Coin pack — 1,200 coins',
                                   coins=1200, price_cents=1999)
        # Author share comes from Finance Settings.
        fs = FinanceSettings.current()
        fs.author_share_pct = 60
        fs.save()
        story = Story.objects.filter(is_signed=True).first()
        before = AuthorEarning.objects.filter(author=story.author).count()
        wallet._credit_author(story, 100, 'test')
        self.assertEqual(AuthorEarning.objects.filter(author=story.author).latest('pk').amount_cents, 60)
        self.assertEqual(AuthorEarning.objects.filter(author=story.author).count(), before + 1)

        self.as_role('finance')
        r = self.client.get('/finance/revenue/')
        self.assertContains(r, 'Reader spending')
        self.assertEqual(r.context['gross'] - r.context['fees'], r.context['net'])
        # Search finds a transaction by amount and a payout by ID.
        self.assertContains(self.client.get('/finance/search/?q=19.99'), 'Coin pack — 1,200 coins')
        creator = User.objects.get(email='creator@bonovela.app')
        big = Payout.objects.create(author=creator, amount_cents=600000, period_end=timezone.localdate())
        self.assertContains(self.client.get(f'/finance/search/?q=PO-{big.pk}'), f'Payout to {creator.display_name}')
        Expense.objects.create(title='Northwind Cloud', category='Hosting', amount_cents=412000)
        self.assertContains(self.client.get('/finance/search/?q=northwind&type=Invoice'), 'Northwind Cloud')
        # Two-person approval: over $5,000 needs a first approval by someone else.
        self.client.post(f'/finance/payouts/{big.pk}/', {'action': 'paid', 'reference': 'W-1'})
        big.refresh_from_db()
        self.assertEqual(big.status, Payout.STATUS_PENDING)
        self.client.post(f'/finance/payouts/{big.pk}/', {'action': 'approve'})
        self.client.post(f'/finance/payouts/{big.pk}/', {'action': 'paid', 'reference': 'W-1'})  # same person
        big.refresh_from_db()
        self.assertEqual((big.status, big.approved_by.email), (Payout.STATUS_PENDING, 'finance@bonovela.app'))
        self.client.force_login(User.objects.get(email='admin@bonovela.app'))
        self.client.post(f'/finance/payouts/{big.pk}/', {'action': 'paid', 'reference': 'W-1'})
        big.refresh_from_db()
        self.assertEqual(big.status, Payout.STATUS_PAID)
        # Report downloads are audited.
        r = self.client.get('/finance/reports/?download=pl')
        self.assertEqual(r['Content-Type'], 'text/csv')
        self.assertIn(b'Net profit', r.content)
        self.assertTrue(AuditEntry.objects.filter(action='Downloaded report').exists())
        self.assertEqual(self.client.get('/finance/reports/?download=authors')['Content-Type'], 'text/csv')

    def test_finance_stage_three_desks(self):
        from datetime import timedelta as td
        from apps.catalog.models import Chapter, Story
        from apps.finance.models import (BonusAward, BonusProgram, FinanceSettings, ProcessorPayout, RefundCase,
                                         SubscriberRead, SubscriptionShare, SupportTicket)
        from apps.wallet import services as wallet
        from apps.wallet.models import AuthorEarning, Subscription, SubscriptionPlan, Transaction
        from rest_framework.test import APIClient
        reader = User.objects.get(email='reader@bonovela.app')
        plan = SubscriptionPlan.objects.order_by('days').first() or SubscriptionPlan.objects.create(
            name='Monthly', price_cents=999, days=30)
        now = timezone.now()
        Subscription.objects.create(user=reader, plan=plan, starts_at=now - td(days=1), ends_at=now + td(days=30))
        # A Plus reader opening a paid short-story chapter is a subscriber read.
        story = Story.objects.visible_in_app().filter(is_short=True).first()
        chapter = story.chapters.filter(status=Chapter.STATUS_PUBLISHED).order_by('number').last()
        self.assertFalse(chapter.is_free, 'seed short story needs a paid chapter')
        api = APIClient()
        api.force_authenticate(reader)
        api.get(f'/api/books/{story.pk}/chapters/{chapter.number}/')
        api.get(f'/api/books/{story.pk}/chapters/{chapter.number}/')  # same month: counted once
        self.assertEqual(SubscriberRead.objects.filter(user=reader, chapter=chapter).count(), 1)

        self.as_role('finance')
        r = self.client.get('/finance/subscriptions/')
        self.assertGreater(r.context['pool']['pool'], 0)
        share = SubscriptionShare.objects.get(author=story.author)
        self.assertEqual(share.suggested_cents, r.context['pool']['pool'])  # only author with reads
        self.client.post('/finance/subscriptions/', {'action': 'approve', 'share': share.pk, 'amount': '1.50'})
        share.refresh_from_db()
        self.assertEqual((share.status, share.approved_cents), ('approved', 150))
        self.assertTrue(AuthorEarning.objects.filter(author=story.author, type='subscription_share',
                                                     amount_cents=150).exists())
        self.assertEqual(self.client.get('/finance/subscriptions/?export=csv')['Content-Type'], 'text/csv')

        # Refunds: small ones approve themselves when the setting is on; others are decided on the case page.
        fs = FinanceSettings.current()
        fs.auto_approve_small_refunds = True
        fs.save()
        self.client.post('/finance/refunds/', {'email': reader.email, 'amount': '2.99', 'reason': 'Bought twice',
                                               'kind': 'refund', 'processor': 'Google Play'})
        self.assertEqual(RefundCase.objects.get(amount_cents=299).status, 'refunded')
        wallet.credit_coins(reader, 500, 'test coins')
        self.client.post('/finance/refunds/', {'email': reader.email, 'amount': '19.99', 'reason': 'Accidental pack',
                                               'kind': 'refund', 'processor': 'Stripe'})
        case = RefundCase.objects.get(amount_cents=1999)
        self.assertContains(self.client.get(f'/finance/refunds/{case.pk}/'), 'test coins')
        before = wallet.wallet_for(reader).coins
        self.client.post(f'/finance/refunds/{case.pk}/', {'action': 'refund', 'amount': '19.99', 'coins': '500'})
        case.refresh_from_db()
        self.assertEqual((case.status, case.coins_removed), ('refunded', 500))
        self.assertEqual(wallet.wallet_for(reader).coins, before - 500)

        # Support: log a report, check payments, credit coins and Plus days.
        self.client.post('/finance/support/', {'action': 'new', 'email': reader.email, 'category': 'coins',
                                               'issue': 'not_delivered', 'message': 'Bought 1,000 coins, got nothing'})
        ticket = SupportTicket.objects.get()
        self.assertContains(self.client.get(f'/finance/support/?q={reader.email}'), 'Their latest payments')
        coins = wallet.wallet_for(reader).coins
        ends = reader.subscriptions.order_by('-ends_at').first().ends_at
        self.client.post('/finance/support/', {'action': 'credit', 'ticket': ticket.pk, 'coins': '1000', 'days': '7'})
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, 'resolved')
        self.assertEqual(wallet.wallet_for(reader).coins, coins + 1000)
        self.assertGreater(reader.subscriptions.order_by('-ends_at').first().ends_at, ends)
        self.assertTrue(reader.inbox.filter(title__contains='credited').exists())

        # Bonuses: author programmes pay into income, reader programmes pay coins.
        self.client.post('/finance/bonuses/', {'action': 'program', 'name': 'Top 10 Monthly Authors',
                                               'description': 'Cash bonus', 'audience': 'authors', 'budget': '15000'})
        prog = BonusProgram.objects.get()
        self.client.post('/finance/bonuses/', {'action': 'award', 'program': prog.pk, 'email': story.author.email,
                                               'amount': '2500', 'date': timezone.localdate().isoformat()})
        award = BonusAward.objects.get()
        self.client.post('/finance/bonuses/', {'action': 'pay', 'award': award.pk})
        award.refresh_from_db()
        self.assertEqual(award.status, 'paid')
        self.assertTrue(AuthorEarning.objects.filter(author=story.author, type='others', amount_cents=250000).exists())
        self.assertEqual(self.client.get('/finance/bonuses/').context['programs'][0].used, 250000)

        # Reconciliation: matched when the statement equals the ledger, otherwise resolve with a note.
        today = timezone.localdate().isoformat()
        self.client.post('/finance/reconciliation/', {'action': 'add', 'source': 'Bank', 'reference': 'WIRE-1',
                                                      'start': today, 'end': today, 'amount': '0'})
        self.assertEqual(ProcessorPayout.objects.get(reference='WIRE-1').status, 'matched')
        self.client.post('/finance/reconciliation/', {'action': 'add', 'source': 'Apple', 'reference': 'AAPL-1',
                                                      'start': today, 'end': today, 'amount': '12.00'})
        diff = ProcessorPayout.objects.get(reference='AAPL-1')
        self.assertEqual(diff.status, 'review')
        self.client.post('/finance/reconciliation/', {'payout': diff.pk, 'note': ''})
        diff.refresh_from_db()
        self.assertEqual(diff.status, 'review')  # needs an explanation
        self.client.post('/finance/reconciliation/', {'payout': diff.pk, 'note': 'Apple FX rounding'})
        diff.refresh_from_db()
        self.assertEqual(diff.status, 'resolved')
        self.get_ok('/finance/refunds/?tab=resolved', '/finance/support/?tab=resolved',
                    '/finance/subscriptions/?tab=subscriptions', '/finance/reconciliation/')

    def test_payout_flow(self):
        from apps.wallet.models import Payout, PayoutMethod
        creator = User.objects.get(email='creator@bonovela.app')
        PayoutMethod.objects.create(author=creator, method='paypal', account_name='Studio', account_detail='s@x.com')
        self.as_role('finance')
        self.client.post('/finance/payouts/generate/')
        payout = Payout.objects.get(author=creator)
        self.assertEqual(payout.status, Payout.STATUS_PENDING)
        self.assertIn('PayPal', payout.method)
        # Generating again doesn't double-pay: the pending amount is held.
        self.client.post('/finance/payouts/generate/')
        self.assertEqual(Payout.objects.filter(author=creator).count(), 1)

        self.client.post(f'/finance/payouts/{payout.pk}/', {'action': 'paid'})  # no reference
        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.STATUS_PENDING)
        self.client.post(f'/finance/payouts/{payout.pk}/', {'action': 'paid', 'reference': 'PP-123'})
        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.STATUS_PAID)
        self.assertTrue(creator.inbox.filter(title__startswith='Payout sent').exists())
