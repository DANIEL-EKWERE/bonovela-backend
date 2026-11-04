"""Drama tab API: feed, detail, episodes, unlock, save, banners."""

from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.drama import services
from apps.drama.models import Drama, DramaSave, Episode, WatchProgress
from apps.marketing.models import Banner
from apps.wallet.services import InsufficientBalance


def _abs(request, f):
    if not f:
        return ''
    return request.build_absolute_uri(f.url) if request else f.url


class DramaSerializer(serializers.ModelSerializer):
    """Matches the app's DramaModel.fromJson."""

    genre = serializers.CharField(source='genre.name', default='')
    tags = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    episode_count = serializers.IntegerField(read_only=True)
    book_id = serializers.IntegerField(source='story_id', allow_null=True)

    class Meta:
        model = Drama
        fields = ['id', 'title', 'synopsis', 'kind', 'genre', 'tags', 'cover_url', 'cover_index', 'plays',
                  'episode_count', 'book_id', 'is_completed', 'released_at']

    def get_tags(self, obj):
        return [t.name for t in obj.tags.all()]

    def get_cover_url(self, obj):
        return _abs(self.context.get('request'), obj.cover)


def _episode_data(request, ep):
    watchable = services.can_watch(request.user, ep)
    return {
        'number': ep.number, 'title': ep.title, 'duration_seconds': ep.duration_seconds, 'is_free': ep.is_free,
        'price': ep.price, 'is_locked': not watchable,
        'video_url': ep.video_src(request) if watchable else '',
    }


def _dramas():
    return (Drama.objects.visible_in_app().select_related('genre').prefetch_related('tags')
            .annotate(episode_count=Count('episodes', filter=Q(episodes__status=Episode.STATUS_PUBLISHED))))


def _ser(request, qs):
    return DramaSerializer(qs, many=True, context={'request': request}).data


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def feed(request):
    """Same sections as the app: Popular Picks, New Release, You May Also
    Like, Trending."""
    base = _dramas()
    popular = list(base.order_by('-plays')[:6])
    new = list(base.order_by('-released_at')[:9])
    shown = {d.pk for d in popular + new}
    like = list(base.exclude(pk__in=shown).order_by('?')[:8])
    trending = list(base.annotate(heat=F('plays') / (F('episode_count') + 1)).order_by('-heat')[:9])
    banners = Banner.objects.live('drama')
    return Response({
        'banners': [_banner(request, b) for b in banners],
        'sections': [
            {'key': 'popular', 'title': 'Popular Picks', 'dramas': _ser(request, popular)},
            {'key': 'new_release', 'title': 'New Release', 'dramas': _ser(request, new)},
            {'key': 'you_may_like', 'title': 'You May Also Like', 'dramas': _ser(request, like)},
            {'key': 'trending', 'title': 'Trending', 'dramas': _ser(request, trending)},
        ],
    })


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def detail(request, pk):
    drama = get_object_or_404(_dramas(), pk=pk)
    data = DramaSerializer(drama, context={'request': request}).data
    data['episodes'] = [_episode_data(request, e) for e in drama.published_episodes()]
    user = request.user
    data['is_saved'] = user.is_authenticated and DramaSave.objects.filter(user=user, drama=drama).exists()
    progress = WatchProgress.objects.filter(user=user, drama=drama).first() if user.is_authenticated else None
    data['last_episode'] = progress.episode_number if progress else 1
    return Response(data)


def _episode(pk, number):
    drama = get_object_or_404(Drama.objects.visible_in_app(), pk=pk)
    return get_object_or_404(drama.episodes.select_related('drama'), number=number, status=Episode.STATUS_PUBLISHED)


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def episode(request, pk, number):
    ep = _episode(pk, number)
    if services.can_watch(request.user, ep):
        Episode.objects.filter(pk=ep.pk).update(plays=F('plays') + 1)
        if number == 1:
            Drama.objects.filter(pk=pk).update(plays=F('plays') + 1)
        if request.user.is_authenticated:
            WatchProgress.objects.update_or_create(user=request.user, drama=ep.drama, defaults={'episode_number': number})
    return Response(_episode_data(request, ep))


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def unlock(request, pk, number):
    ep = _episode(pk, number)
    try:
        services.unlock_episode(request.user, ep)
    except InsufficientBalance:
        return Response({'detail': 'Not enough coins.', 'code': 'insufficient_balance'}, status=402)
    return Response(_episode_data(request, ep))


@api_view(['POST', 'DELETE'])
@permission_classes([permissions.IsAuthenticated])
def save(request, pk):
    drama = get_object_or_404(Drama.objects.visible_in_app(), pk=pk)
    if request.method == 'POST':
        DramaSave.objects.get_or_create(user=request.user, drama=drama)
    else:
        DramaSave.objects.filter(user=request.user, drama=drama).delete()
    return Response({'is_saved': request.method == 'POST'})


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def saved(request):
    ids = DramaSave.objects.filter(user=request.user).values('drama')
    return Response(_ser(request, _dramas().filter(pk__in=ids)))


# ── Banners (Marketing) ──────────────────────────────────────────────────────
def _banner(request, b):
    return {
        'id': b.id, 'title': b.title, 'subtitle': b.subtitle, 'image_url': _abs(request, b.image),
        'color_index': b.color_index, 'target': b.target, 'story_id': b.story_id, 'drama_id': b.drama_id,
        'url': b.url,
    }


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def banners(request):
    qs = list(Banner.objects.live(request.query_params.get('placement')))
    Banner.objects.filter(pk__in=[b.pk for b in qs]).update(impressions=F('impressions') + 1)
    return Response([_banner(request, b) for b in qs])


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def banner_click(request, banner_id):
    Banner.objects.filter(pk=banner_id).update(clicks=F('clicks') + 1)
    return Response({'ok': True})
