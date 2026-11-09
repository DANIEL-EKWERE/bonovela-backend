"""Author Center API: the app's Author Center talks to these."""

from django.db.models import Max
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.catalog.models import Chapter, Contest, Story
from apps.editorial.models import ContractApplication
from apps.engagement.models import Review, ReviewReply
from apps.wallet.models import AuthorEarning

from . import serializers as s


class IsAuthor(permissions.BasePermission):
    message = 'Become an author first.'

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role != 'reader'


Author = [IsAuthor]


class StoryListCreate(generics.ListCreateAPIView):
    serializer_class = s.AuthorStorySerializer
    permission_classes = Author

    def get_queryset(self):
        return Story.objects.filter(author=self.request.user).prefetch_related('tags', 'contests', 'applications')

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)


class StoryDetail(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = s.AuthorStorySerializer
    permission_classes = Author

    def get_queryset(self):
        return Story.objects.filter(author=self.request.user)


def _own_story(request, pk):
    return get_object_or_404(Story, pk=pk, author=request.user)


class ChapterListCreate(generics.ListCreateAPIView):
    """?status=draft|published|trash"""

    serializer_class = s.AuthorChapterSerializer
    permission_classes = Author

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


@api_view(['POST'])
@permission_classes(Author)
def chapter_action(request, pk, chapter_id, action):
    """publish | schedule | trash | restore"""
    chapter = get_object_or_404(Chapter, pk=chapter_id, story_id=pk, story__author=request.user)
    if action == 'publish':
        if not chapter.story.is_info_complete:
            return Response({'detail': 'Complete your book information before publishing.'}, status=400)
        if not chapter.word_count:
            return Response({'detail': 'Write something first.'}, status=400)
        chapter.publish()
    elif action == 'schedule':
        when = request.data.get('scheduled_for')
        dt = when and timezone.datetime.fromisoformat(when)
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
    """Apply to sign a story (needs complete info and 3+ published chapters)."""
    story = _own_story(request, pk)
    if request.method == 'GET':
        app = story.applications.first()
        return Response(s.ApplicationSerializer(app).data if app else None)
    if story.applications.filter(status__in=ContractApplication.OPEN_STATUSES + (ContractApplication.STATUS_SIGNED,)).exists():
        return Response({'detail': 'This story already has an open application.'}, status=400)
    if not story.is_info_complete or story.published_chapters().count() < 3:
        return Response({'detail': 'Complete your book information and publish at least 3 chapters first.'}, status=400)
    from apps.editorial.history import submit
    app = submit(story, request, request.data.get('message', '') or '')
    return Response(s.ApplicationSerializer(app).data, status=201)


@api_view(['POST'])
@permission_classes(Author)
def sign_contract(request, pk):
    story = _own_story(request, pk)
    app = story.applications.filter(status=ContractApplication.STATUS_CONTRACT_SENT).first()
    if not app:
        return Response({'detail': 'No contract waiting for your signature.'}, status=400)
    from apps.editorial.services import sign
    sign(app)
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


@api_view(['GET'])
@permission_classes(Author)
def income(request):
    """?month=YYYY-MM — totals per income type in USD cents."""
    month = request.query_params.get('month') or timezone.localdate().strftime('%Y-%m')
    year, mon = (int(x) for x in month.split('-'))
    qs = AuthorEarning.objects.filter(author=request.user)
    in_month = qs.filter(created_at__year=year, created_at__month=mon)
    by_type = {key: 0 for key, _ in AuthorEarning.TYPE_CHOICES}
    for e in in_month:
        by_type[e.type] += e.amount_cents
    return Response({
        'month': month,
        'total_cents': sum(e.amount_cents for e in qs),
        'month_cents': sum(by_type.values()),
        'types': [{'type': k, 'label': label, 'amount_cents': by_type[k]} for k, label in AuthorEarning.TYPE_CHOICES
                  if k not in AuthorEarning.RETIRED_TYPES],
    })


@api_view(['GET'])
@permission_classes(Author)
def contests(request):
    return Response(s.ContestSerializer(Contest.objects.filter(is_active=True), many=True).data)
