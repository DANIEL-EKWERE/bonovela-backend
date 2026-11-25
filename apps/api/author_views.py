"""Author Center API: the app's Author Center talks to these."""

from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.catalog.models import Chapter, Contest, Story, submit_chapter_edit
from apps.editorial.models import ContractApplication
from apps.engagement.models import Review, ReviewReply
from apps.wallet.models import AuthorEarning
from apps.wallet.services import PAYOUT_FIELDS, author_status, save_payout_method, withdrawal_rules

from . import serializers as s


class IsAuthor(permissions.BasePermission):
    message = 'Become an author first.'

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role != 'reader'


Author = [IsAuthor]


class StoryListCreate(generics.ListCreateAPIView):
    serializer_class = s.AuthorStorySerializer
    permission_classes = Author
    pagination_class = None  # the app syncs an author's whole list

    def get_queryset(self):
        return Story.objects.filter(author=self.request.user).prefetch_related('tags', 'applications')

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)


class StoryDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = s.AuthorStorySerializer
    permission_classes = Author

    def get_queryset(self):
        return Story.objects.filter(author=self.request.user)

    def update(self, request, *args, **kwargs):
        story = self.get_object()
        if story.under_review:
            return Response({'detail': story.lock_reason, 'code': 'locked'}, status=400)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        story = self.get_object()
        if not story.can_delete:
            return Response({'detail': story.lock_reason, 'code': 'locked'}, status=400)
        return super().destroy(request, *args, **kwargs)


def _own_story(request, pk):
    return get_object_or_404(Story, pk=pk, author=request.user)


class ChapterListCreate(generics.ListCreateAPIView):
    """?status=draft|published|trash"""

    serializer_class = s.AuthorChapterSerializer
    permission_classes = Author
    pagination_class = None  # the app syncs every chapter of a book

    def get_queryset(self):
        qs = _own_story(self.request, self.kwargs['pk']).chapters.order_by('-number')
        status = self.request.query_params.get('status')
        if status == 'draft':
            qs = qs.filter(status__in=[Chapter.STATUS_DRAFT, Chapter.STATUS_SCHEDULED, Chapter.STATUS_REVISION])
        elif status:
            qs = qs.filter(status=status)
        return qs

    def perform_create(self, serializer):
        story = _own_story(self.request, self.kwargs['pk'])
        last = story.chapters.aggregate(n=Max('number'))['n'] or 0
        serializer.save(story=story, number=last + 1)


class ChapterDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = s.AuthorChapterSerializer
    permission_classes = Author

    def get_queryset(self):
        return Chapter.objects.filter(story__author=self.request.user, story_id=self.kwargs['pk'])

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs['chapter_id'])

    def update(self, request, *args, **kwargs):
        """Under contract review: no changes. Signed book, published chapter:
        the edit goes to the editor (202) and the live text stays."""
        chapter = self.get_object()
        story = chapter.story
        if story.under_review or (story.is_signed and chapter.status == Chapter.STATUS_PUBLISHED):
            try:
                result = submit_chapter_edit(chapter, request.data.get('title', chapter.title),
                                             request.data.get('content', chapter.content),
                                             request.data.get('note', chapter.note))
            except ValidationError as e:
                return Response({'detail': e.messages[0], 'code': 'locked'}, status=400)
            if result == 'review':
                return Response({**s.AuthorChapterSerializer(chapter).data, 'pending_review': True,
                                 'detail': 'Your edit was sent to your editor. It goes live once approved.'},
                                status=202)
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        chapter = self.get_object()
        story = chapter.story
        if story.under_review or (story.is_signed and chapter.status != Chapter.STATUS_DRAFT):
            return Response({'detail': story.lock_reason, 'code': 'locked'}, status=400)
        return super().destroy(request, *args, **kwargs)


@api_view(['POST'])
@permission_classes(Author)
def chapter_action(request, pk, chapter_id, action):
    """publish | schedule | trash | restore"""
    chapter = get_object_or_404(Chapter, pk=chapter_id, story_id=pk, story__author=request.user)
    story = chapter.story
    if action == 'trash' and (story.under_review or (story.is_signed and chapter.status == Chapter.STATUS_PUBLISHED)):
        return Response({'detail': story.lock_reason, 'code': 'locked'}, status=400)
    if action in ('publish', 'schedule'):
        if not chapter.story.is_info_complete:
            return Response({'detail': 'Complete your book information before publishing.'}, status=400)
        error = Chapter.length_error(chapter.word_count)
        if error:
            return Response({'detail': error, 'code': 'chapter_length'}, status=400)
    if action == 'publish':
        chapter.publish()
    elif action == 'schedule':
        try:
            dt = datetime.fromisoformat(request.data.get('scheduled_for') or '')
        except ValueError:
            dt = None
        if dt and timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        if not dt or dt <= timezone.now():
            return Response({'detail': 'scheduled_for must be a future ISO date-time.'}, status=400)
        chapter.status, chapter.scheduled_for = Chapter.STATUS_SCHEDULED, dt
        chapter.save()
    elif action == 'trash':
        chapter.move_to_trash()
    elif action == 'restore':
        chapter.restore()
    else:
        return Response({'detail': 'Unknown action.'}, status=404)
    return Response(s.AuthorChapterSerializer(chapter).data)


@api_view(['GET', 'POST'])
@permission_classes(Author)
def application(request, pk):
    """GET: the latest application. POST (multipart): Apply for Contract with
    every step of the form; needs complete book info and CONTRACT_MIN_WORDS
    published words. Errors: {"errors": {field: [msg]}, "step": n}."""
    story = _own_story(request, pk)
    if request.method == 'GET':
        app = story.applications.first()
        return Response(s.ApplicationSerializer(app).data if app else None)
    if story.applications.filter(status__in=ContractApplication.OPEN_STATUSES + (ContractApplication.STATUS_SIGNED,)).exists():
        return Response({'detail': 'This story already has an open application.'}, status=400)
    if story.contract_cooldown_until:
        return Response({'detail': 'You can apply again 24 hours after the decision.', 'code': 'cooldown',
                         'reapply_at': story.contract_cooldown_until}, status=400)
    if not story.can_apply_for_contract:
        need = settings.BONOVELA['CONTRACT_MIN_WORDS']
        return Response({'detail': f'Complete your book information and publish at least {need:,} words first.'},
                        status=400)
    from apps.editorial.forms import ContractApplicationForm
    from apps.editorial.history import submit
    form = ContractApplicationForm(request.data, request.FILES, story=story)
    if not form.is_valid():
        return Response({'detail': 'Please check the highlighted fields.', 'errors': form.errors,
                         'step': form.first_error_step()}, status=400)
    app = submit(story, request, request.data.get('message', '') or '', form=form)
    return Response(s.ApplicationSerializer(app).data, status=201)


@api_view(['GET'])
@permission_classes(Author)
def editors(request, pk):
    """Editors an author can choose for this book (its desk only)."""
    from apps.editorial.forms import editors_for
    story = _own_story(request, pk)
    return Response([{'id': e.id, 'name': e.display_name} for e in editors_for(story)])


@api_view(['POST'])
@permission_classes(Author)
def sign_contract(request, pk):
    story = _own_story(request, pk)
    app = story.applications.filter(status=ContractApplication.STATUS_CONTRACT_SENT).first()
    if not app:
        return Response({'detail': 'No contract waiting for your signature.'}, status=400)
    from apps.editorial.services import author_sign
    from apps.users.ip import client_ip
    try:
        author_sign(app, request.data.get('signature_name', ''), request.data.get('signature_image', ''),
                    ip=client_ip(request))
    except ValidationError as e:
        return Response({'detail': e.messages[0], 'code': 'signature'}, status=400)
    return Response(s.ApplicationSerializer(app).data)


@api_view(['GET'])
@permission_classes(Author)
def stats(request, pk):
    story = _own_story(request, pk)
    pub = story.published_chapters()
    words_by_day = {}
    for c in pub:
        d = (c.published_at or c.updated_at).date().isoformat()
        words_by_day[d] = words_by_day.get(d, 0) + c.word_count
    return Response({
        'views': story.views,
        'chapters': pub.count(),
        'words': sum(c.word_count for c in pub),
        'avg_chapter_length': (sum(c.word_count for c in pub) // pub.count()) if pub else 0,
        'comments': story.reviews.count(),
        'library_adds': story.shelved_by.count(),
        'votes': sum(v.count for v in story.votes.all()),
        'words_by_day': words_by_day,
    })


@api_view(['GET'])
@permission_classes(Author)
def comments(request):
    qs = (Review.objects.filter(story__author=request.user, is_hidden=False)
          .select_related('user', 'story').prefetch_related('replies__user', 'likes'))
    if request.query_params.get('book'):
        qs = qs.filter(story_id=request.query_params['book'])
    if request.query_params.get('stars'):
        qs = qs.filter(stars=request.query_params['stars'])
    return Response(s.ReviewSerializer(qs[:200], many=True, context={'request': request}).data)


@api_view(['POST'])
@permission_classes(Author)
def reply(request, review_id):
    review = get_object_or_404(Review, pk=review_id, story__author=request.user)
    text = (request.data.get('text') or '').strip()
    if not text:
        return Response({'detail': 'Write a reply first.'}, status=400)
    r = ReviewReply.objects.create(review=review, user=request.user, text=text[:1000])
    return Response(s.ReviewReplySerializer(r).data, status=201)


@api_view(['GET', 'POST'])
@permission_classes(Author)
def pay_to_read(request, pk):
    """GET: Pay-to-Read status and chapters. POST {from_chapter, message}:
    apply (signed books with 30,000 published words)."""
    from apps.editorial import services as editorial
    story = _own_story(request, pk)
    if request.method == 'POST':
        try:
            editorial.apply_pay_to_read(story, int(request.data.get('from_chapter') or 0),
                                        str(request.data.get('message') or ''))
        except (ValueError, TypeError):
            return Response({'detail': 'Choose the first chapter to lock.'}, status=400)
        except ValidationError as e:
            return Response({'detail': e.messages[0]}, status=400)
    app = story.pay_to_read_applications.first()
    return Response({
        'can_apply': story.can_apply_pay_to_read, 'paid_from_chapter': story.paid_from_chapter,
        'min_words': story.pay_to_read_min_words, 'words': story.total_words,
        'chapters': list(story.published_chapters().order_by('number').values_list('number', flat=True)),
        'application': {'status': app.status, 'label': app.get_status_display(), 'requested_from': app.requested_from,
                        'approved_from': app.approved_from, 'note': app.note} if app else None,
    }, status=201 if request.method == 'POST' else 200)


@api_view(['GET'])
@permission_classes(Author)
def income(request):
    """?month=YYYY-MM — this month's approved earnings per section (USD
    cents), plus Carry over, Available to withdraw and Pending withdrawal."""
    from datetime import date

    from apps.wallet.income import balances
    try:
        year, mon = (int(x) for x in (request.query_params.get('month') or '').split('-'))
        month = date(year, mon, 1)
    except ValueError:
        month = timezone.localdate().replace(day=1)
    approved = AuthorEarning.objects.filter(author=request.user, status=AuthorEarning.STATUS_APPROVED)
    by_type = {key: 0 for key, _ in AuthorEarning.TYPE_CHOICES}
    works = {key: set() for key, _ in AuthorEarning.TYPE_CHOICES}
    for e in approved.filter(period=month):
        by_type[e.type] += e.amount_cents
        works[e.type].add(e.story_id or f'x{e.pk}')
    labels = dict(AuthorEarning.TYPE_CHOICES)
    bal = balances(request.user)
    rules = withdrawal_rules(request.user)
    return Response({
        'month': month.strftime('%Y-%m'),
        'total_cents': bal['total'],
        'month_cents': sum(by_type[k] for k in AuthorEarning.MONTHLY_TYPES),
        'available_cents': bal['available'],
        'carry_over_cents': bal['carry_over'],
        'pending_withdrawal_cents': bal['pending_withdrawal'],
        'withdraw': _rules_json(rules),
        # The Income cards, in order (then Available to withdraw and Withdrawal history).
        'types': [{'type': k, 'label': labels[k],
                   'amount_cents': bal['carry_over'] if k == 'carry_over' else by_type[k],
                   'works': len(works[k])} for k in AuthorEarning.INCOME_CARDS],
    })


@api_view(['GET'])
@permission_classes(Author)
def income_detail(request, kind):
    """The books behind one Income section in a month (Profit Share…)."""
    from datetime import date
    try:
        year, mon = (int(x) for x in (request.query_params.get('month') or '').split('-'))
        month = date(year, mon, 1)
    except ValueError:
        month = timezone.localdate().replace(day=1)
    rows = (AuthorEarning.objects.filter(author=request.user, type=kind, period=month,
                                         status=AuthorEarning.STATUS_APPROVED).select_related('story'))
    books, others = {}, []
    for e in rows:
        if e.story_id:
            b = books.setdefault(e.story_id, {
                'story_id': e.story_id, 'title': e.story.display_title, 'book_id': e.story.book_code,
                'story_length': e.story_length, 'cover': request.build_absolute_uri(e.story.cover.url)
                if e.story.cover else '', 'cover_index': e.story.cover_index, 'amount_cents': 0})
            b['amount_cents'] += e.amount_cents
        else:
            others.append({'reason': e.decision_note or e.note, 'amount_cents': e.amount_cents})
    return Response({'type': kind, 'label': dict(AuthorEarning.TYPE_CHOICES).get(kind, kind),
                     'month': month.strftime('%Y-%m'), 'total_cents': sum(e.amount_cents for e in rows),
                     'books': list(books.values()), 'others': others})


def _rules_json(rules):
    return {'available_cents': rules['available'], 'minimum_cents': rules['minimum'],
            'maximum_cents': rules['maximum'], 'placed_this_month': rules['placed_this_month'],
            'can_withdraw': rules['can_withdraw']}


def _withdrawal_json(p):
    return {'id': p.pk, 'code': p.code, 'category': p.get_category_display(), 'requested_cents': p.requested_cents,
            'approved_cents': p.approved_cents, 'status': author_status(p), 'state': p.status,
            'created_at': p.created_at, 'paid_at': p.paid_at, 'reference': p.reference if p.paid_at else '',
            'paid_to': p.method}


@api_view(['GET', 'POST'])
@permission_classes(Author)
def withdrawals(request):
    """GET: withdrawal history (rejections stay internal) and the rules.
    POST {amount_cents, confirmed: true, ...payment account fields}: save
    the account and place this month's withdrawal."""
    from apps.dashboard.forms import PayoutForm
    from apps.wallet.models import Payout, PayoutMethod
    from apps.wallet.services import WithdrawalError, place_withdrawal

    if request.method == 'POST':
        current = PayoutMethod.objects.filter(author=request.user).first()
        form = PayoutForm({**{k: request.data.get(k, '') for k in PAYOUT_FIELDS},
                           'confirmed': request.data.get('confirmed') in (True, 'true', 'True', 'on')},
                          instance=current)
        if not form.is_valid():
            return Response({'detail': 'Please check the highlighted fields.', 'errors': form.errors}, status=400)
        save_payout_method(request.user, form.cleaned_data)
        method = PayoutMethod.objects.filter(author=request.user).first()
        try:
            cents = int(request.data.get('amount_cents') or 0)
            p = place_withdrawal(request.user, cents, method)
        except (ValueError, TypeError):
            return Response({'detail': 'Enter the amount to withdraw.'}, status=400)
        except WithdrawalError as e:
            return Response({'detail': str(e), 'code': 'withdrawal'}, status=400)
        return Response(_withdrawal_json(p), status=201)
    rows = Payout.objects.filter(author=request.user).exclude(status=Payout.STATUS_REJECTED)
    return Response({'rules': _rules_json(withdrawal_rules(request.user)),
                     'withdrawals': [_withdrawal_json(p) for p in rows]})





@api_view(['GET', 'PUT'])
@permission_classes(Author)
def payout_method(request):
    """Where earnings are paid. PUT needs every bank field and "confirmed":
    true (the app's "Please check your information" step), like the web."""
    from apps.dashboard.forms import PayoutForm
    from apps.wallet.models import PayoutMethod

    current = PayoutMethod.objects.filter(author=request.user).first()
    if request.method == 'PUT':
        form = PayoutForm({**{k: request.data.get(k, '') for k in PAYOUT_FIELDS},
                           'confirmed': request.data.get('confirmed') in (True, 'true', 'True', 'on')},
                          instance=current)
        if not form.is_valid():
            return Response({'detail': 'Please check the highlighted fields.', 'errors': form.errors}, status=400)
        result = save_payout_method(request.user, form.cleaned_data)
        current = PayoutMethod.objects.filter(author=request.user).first()
        return Response({**{k: getattr(current, k) for k in PAYOUT_FIELDS}, 'pending_change': result == 'pending'})
    from apps.wallet.models import PayoutMethodChange
    pending = PayoutMethodChange.objects.filter(author=request.user, status=PayoutMethodChange.STATUS_PENDING).exists()
    return Response({**{k: getattr(current, k) for k in PAYOUT_FIELDS}, 'pending_change': pending} if current else None)

@api_view(['GET'])
@permission_classes(Author)
def contests(request):
    return Response(s.ContestSerializer(Contest.objects.filter(is_active=True), many=True).data)
