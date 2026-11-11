"""Reader-facing API used by the Flutter app."""

from django.contrib.auth import get_user_model
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.users.ip import record as record_ip

from apps.catalog import feeds
from apps.catalog.models import Chapter, Genre, Story, Tag
from apps.engagement.models import InboxMessage, LibraryEntry, ReadingProgress, Report, Review, ReviewReply
from apps.users.models import Follow
from apps.wallet import services
from apps.wallet.models import AutoUnlock, GiftItem

from . import serializers as s

User = get_user_model()


def tokens_for(user):
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh), 'user': s.UserSerializer(user).data}


# ── Auth ─────────────────────────────────────────────────────────────────────
class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        ser = s.RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        record_ip(user, request, 'signup')
        return Response(tokens_for(user), status=status.HTTP_201_CREATED)


class TokenView(TokenObtainPairView):
    """JWT sign-in that also records the IP address."""

    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            user = User.objects.filter(email__iexact=request.data.get('email', '')).first()
            if user:
                record_ip(user, request, 'login')
        return response


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def email_status(request):
    """'Sign in or sign up' step: does this email already have an account?"""
    email = (request.data.get('email') or '').strip().lower()
    user = User.objects.filter(email=email).first()
    return Response({'exists': bool(user), 'verified': bool(user and user.email_verified)})


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = s.UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def onboarding(request):
    """'Choose a type': store the preference and fill the shelf."""
    reader_type = request.data.get('reader_type', '')
    user = request.user
    user.reader_type = reader_type if reader_type in ('female', 'male') else ''
    user.save(update_fields=['reader_type'])
    genres = {'female': ['Romance', 'Werewolf', 'Mafia', 'LGBTQ+'], 'male': ['Fantasy']}.get(
        user.reader_type, ['Romance', 'Werewolf', 'Mafia', 'LGBTQ+', 'Fantasy']
    )
    picks = feeds.books().filter(genre__name__in=genres, is_short=False).order_by('-avg_stars', '-views')[:9]
    for story in picks:
        LibraryEntry.objects.get_or_create(user=user, story=story, defaults={'is_recommended': True})
    return Response({'added': [b.id for b in picks]})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def become_author(request):
    if request.user.role == User.ROLE_READER:
        request.user.role = User.ROLE_AUTHOR
        request.user.save(update_fields=['role'])
    return Response(s.UserSerializer(request.user).data)


# ── Catalogue ────────────────────────────────────────────────────────────────
class BookListView(generics.ListAPIView):
    serializer_class = s.BookSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        qs = feeds.books()
        p = self.request.query_params
        if p.get('genre'):
            qs = qs.filter(genre__name=p['genre'])
        if p.get('short') in ('1', 'true'):
            qs = qs.filter(is_short=True)
        elif p.get('short') in ('0', 'false'):
            qs = qs.filter(is_short=False)
        if p.get('status') in ('ongoing', 'completed'):
            qs = qs.filter(status=p['status'])
        if p.get('audience') in ('female', 'male'):
            qs = qs.filter(audience=p['audience'])
        if p.get('q'):
            q = p['q']
            qs = qs.filter(
                Q(title__icontains=q) | Q(author__pen_name__icontains=q) | Q(genre__name__icontains=q)
                | Q(tags__name__icontains=q)
            ).distinct()
        order = {'popular': '-views', 'new': '-updated_at', 'rating': '-avg_stars'}.get(p.get('order'), '-views')
        return qs.order_by(order)


def _sections(request, sections):
    ctx = {'request': request}
    return [
        {'title': sec['title'], 'layout': sec['layout'], 'books': s.BookSerializer(sec['books'], many=True, context=ctx).data}
        for sec in sections
        if sec['books']
    ]


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def discover(request):
    return Response(_sections(request, feeds.discover_feed(request.query_params.get('tab', 'Discover'))))


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def short_collections(request):
    """Short Stories: [{title, sections: [{title, layout, books}]}]."""
    return Response([
        {'title': c['title'], 'sections': _sections(request, c['sections'])} for c in feeds.short_collections()
    ])


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def rankings(request):
    rank_type = request.query_params.get('type', 'bestsellers')
    if rank_type not in feeds.RANK_TYPES:
        return Response({'detail': f'type must be one of {feeds.RANK_TYPES}'}, status=400)
    qs = feeds.ranking(rank_type, request.query_params.get('genre'))[:50]
    return Response(s.BookSerializer(qs, many=True, context={'request': request}).data)


class BookDetailView(generics.RetrieveAPIView):
    serializer_class = s.BookSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        return feeds.books()

    def retrieve(self, request, *args, **kwargs):
        book = self.get_object()
        Story.objects.filter(pk=book.pk).update(views=book.views + 1)
        data = self.get_serializer(book).data
        user = request.user
        data['on_shelf'] = user.is_authenticated and LibraryEntry.objects.filter(user=user, story=book).exists()
        progress = user.is_authenticated and ReadingProgress.objects.filter(user=user, story=book).first()
        data['resume_chapter'] = progress.chapter_number if progress else None
        data['following_author'] = user.is_authenticated and Follow.objects.filter(follower=user, author=book.author).exists()
        data['auto_unlock'] = user.is_authenticated and AutoUnlock.objects.filter(user=user, story=book).exists()
        data['votes'] = book.votes.aggregate(n=Sum('count'))['n'] or 0
        data['supporters'] = book.gifts.values('sender').distinct().count()
        return Response(data)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def related(request, pk):
    book = get_object_or_404(Story, pk=pk)
    qs = feeds.books().filter(genre=book.genre, is_short=book.is_short).exclude(pk=pk).order_by('-views')[:8]
    return Response(s.BookSerializer(qs, many=True, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def taxonomy(request):
    return Response({
        'genres': s.GenreSerializer(Genre.objects.filter(is_active=True), many=True).data,
        'tags': s.TagSerializer(Tag.objects.all(), many=True).data,
    })


# ── Chapters ─────────────────────────────────────────────────────────────────
def _visible_story(pk):
    return get_object_or_404(Story.objects.visible_in_app(), pk=pk)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def chapters(request, pk):
    story = _visible_story(pk)
    qs = story.chapters.filter(status=Chapter.STATUS_PUBLISHED).select_related('story')
    return Response(s.ChapterListSerializer(qs, many=True, context={'request': request}).data)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def chapter_detail(request, pk, number):
    story = _visible_story(pk)
    chapter = get_object_or_404(story.chapters.select_related('story'), number=number, status=Chapter.STATUS_PUBLISHED)
    user = request.user
    if user.is_authenticated:
        # Auto Subscription: unlock on arrival when the balance allows.
        if not services.can_read(user, chapter) and AutoUnlock.objects.filter(user=user, story=story).exists():
            try:
                services.unlock_chapter(user, chapter)
            except services.InsufficientBalance:
                pass
        ReadingProgress.objects.update_or_create(user=user, story=story, defaults={'chapter_number': number})
    return Response(s.ChapterDetailSerializer(chapter, context={'request': request}).data)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def unlock(request, pk, number):
    story = _visible_story(pk)
    chapter = get_object_or_404(story.chapters, number=number, status=Chapter.STATUS_PUBLISHED)
    try:
        services.unlock_chapter(request.user, chapter)
    except services.InsufficientBalance:
        return Response({'detail': 'Not enough coins.', 'code': 'insufficient_balance'}, status=402)
    return Response(s.ChapterDetailSerializer(chapter, context={'request': request}).data)


@api_view(['POST', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def auto_unlock(request, pk):
    story = _visible_story(pk)
    if request.method == 'POST':
        AutoUnlock.objects.get_or_create(user=request.user, story=story)
    else:
        AutoUnlock.objects.filter(user=request.user, story=story).delete()
    return Response({'auto_unlock': request.method == 'POST'})


# ── Library & progress ───────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def library(request):
    entries = LibraryEntry.objects.filter(user=request.user).select_related('story')
    ids = [e.story_id for e in entries]
    books = {b.id: b for b in feeds.books().filter(id__in=ids)}
    progress = {p.story_id: p for p in ReadingProgress.objects.filter(user=request.user, story_id__in=ids)}
    ctx = {'request': request}
    out = []
    for e in entries:
        b = books.get(e.story_id)
        if not b:
            continue
        p = progress.get(e.story_id)
        out.append({
            **s.BookSerializer(b, context=ctx).data,
            'is_recommended': e.is_recommended and not p,
            'last_chapter': p.chapter_number if p else None,
            'read_at': p.updated_at if p else None,
        })
    return Response(out)


@api_view(['POST', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def shelf(request, pk):
    story = _visible_story(pk)
    if request.method == 'POST':
        LibraryEntry.objects.get_or_create(user=request.user, story=story)
    else:
        LibraryEntry.objects.filter(user=request.user, story=story).delete()
    return Response({'on_shelf': request.method == 'POST'})


@api_view(['GET', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def history(request):
    qs = ReadingProgress.objects.filter(user=request.user)
    if request.method == 'DELETE':
        qs.delete()
        return Response(status=204)
    books = {b.id: b for b in feeds.books().filter(id__in=qs.values('story_id'))}
    ctx = {'request': request}
    return Response([
        {**s.BookSerializer(books[p.story_id], context=ctx).data, 'last_chapter': p.chapter_number, 'read_at': p.updated_at}
        for p in qs if p.story_id in books
    ])


# ── Reviews ──────────────────────────────────────────────────────────────────
class ReviewListView(generics.ListCreateAPIView):
    serializer_class = s.ReviewSerializer

    def get_queryset(self):
        return (Review.objects.filter(story_id=self.kwargs['pk'], is_hidden=False)
                .select_related('user', 'story').prefetch_related('replies__user', 'likes'))

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, story=_visible_story(self.kwargs['pk']))


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def like_review(request, review_id):
    review = get_object_or_404(Review, pk=review_id)
    if review.likes.filter(pk=request.user.pk).exists():
        review.likes.remove(request.user)
        liked = False
    else:
        review.likes.add(request.user)
        liked = True
    return Response({'liked': liked, 'likes': review.likes.count()})


# ── Gifts, votes, reports ────────────────────────────────────────────────────
@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def gift(request, pk):
    story = _visible_story(pk)
    item = get_object_or_404(GiftItem, pk=request.data.get('item'))
    try:
        services.send_gift(request.user, story, item)
    except services.InsufficientBalance:
        return Response({'detail': 'Balance is low.', 'code': 'insufficient_balance'}, status=402)
    return Response({'sent': item.name, 'votes': item.votes}, status=201)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def vote(request, pk):
    story = _visible_story(pk)
    try:
        count = int(request.data.get('count', 1))
        services.vote(request.user, story, count)
    except (ValueError, services.InsufficientBalance):
        return Response({'detail': 'Not enough vouchers.', 'code': 'insufficient_vouchers'}, status=402)
    return Response({'voted': count}, status=201)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def report(request):
    Report.objects.create(
        reporter=request.user,
        story_id=request.data.get('book_id'),
        reported_user_id=request.data.get('user_id'),
        reason=(request.data.get('reason') or 'No reason given')[:200],
    )
    return Response({'reported': True}, status=201)


# ── Authors (reader side) ────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def author_profile(request, user_id):
    author = get_object_or_404(User, pk=user_id)
    stories = feeds.books().filter(author=author).order_by('-updated_at')
    return Response({
        'id': author.id,
        'name': author.display_name,
        'about': author.about,
        'followers': author.follower_set.count(),
        'following': author.following_set.count(),
        'is_following': request.user.is_authenticated
        and Follow.objects.filter(follower=request.user, author=author).exists(),
        'books': s.BookSerializer(stories, many=True, context={'request': request}).data,
    })


@api_view(['POST', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def follow(request, user_id):
    author = get_object_or_404(User, pk=user_id)
    if author == request.user:
        return Response({'detail': "You can't follow yourself."}, status=400)
    if request.method == 'POST':
        Follow.objects.get_or_create(follower=request.user, author=author)
    else:
        Follow.objects.filter(follower=request.user, author=author).delete()
    return Response({'following': request.method == 'POST'})


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def following(request):
    authors = User.objects.filter(follower_set__follower=request.user)
    return Response([{'id': a.id, 'name': a.display_name, 'stories': a.stories.visible_in_app().count()} for a in authors])


# ── Inbox ────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def inbox(request):
    qs = InboxMessage.objects.filter(user=request.user)
    return Response({'unread': qs.filter(is_read=False).count(), 'messages': s.InboxSerializer(qs[:100], many=True).data})


@api_view(['POST', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def inbox_message(request, message_id):
    msg = get_object_or_404(InboxMessage, pk=message_id, user=request.user)
    if request.method == 'DELETE':
        msg.delete()
        return Response(status=204)
    msg.is_read = True
    msg.save(update_fields=['is_read'])
    return Response(s.InboxSerializer(msg).data)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def inbox_read_all(request):
    InboxMessage.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return Response({'unread': 0})


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def health(request):
    return Response({'ok': True, 'time': timezone.now()})


# ── Places (contract application) ────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def countries(request):
    """[{code, name, dial}] for every country."""
    from apps.editorial import geo
    return Response(geo.countries())


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def states(request, code):
    """[{code, name}] states/regions of a country (may be empty)."""
    from apps.editorial import geo
    return Response(geo.states(code))
