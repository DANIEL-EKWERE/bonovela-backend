"""Discover feed and rankings, shared by the API and the web.

Sections the Chief Editor promotes (Editor's Pick, Trending, …) show the
promoted books first; anything left is filled from the catalogue.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.editorial.models import Promotion

from .models import Chapter, Story

PUBLISHED = Q(chapters__status=Chapter.STATUS_PUBLISHED)


def books():
    """App-visible stories with listing counters."""
    return (
        Story.objects.visible_in_app()
        .select_related('author', 'genre')
        .prefetch_related('tags')
        .annotate(
            chapter_count=Count('chapters', filter=PUBLISHED, distinct=True),
            word_count=Sum('chapters__word_count', filter=PUBLISHED),
            avg_stars=Avg('reviews__stars', filter=Q(reviews__is_hidden=False)),
        )
    )


def promoted(section: str, base):
    now = timezone.now()
    ids = Promotion.objects.filter(section=section, starts_at__lte=now, ends_at__gt=now).values_list('story_id', flat=True)
    return list(base.filter(id__in=ids))


def with_promoted(section: str, base, fallback, limit: int):
    """Promoted books first, then [fallback] until [limit]."""
    picked = promoted(section, base)
    seen = {b.id for b in picked}
    for b in fallback:
        if len(picked) >= limit:
            break
        if b.id not in seen:
            picked.append(b)
            seen.add(b.id)
    return picked[:limit]


def section(title: str, layout: str, items):
    return {'title': title, 'layout': layout, 'books': list(items)}


SUBGENRE_TAGS = ('Erotica', 'MM Romance')


def discover_feed(tab: str):
    """Same tabs and section order as the app."""
    novels = books().filter(is_short=False)
    popular = novels.order_by('-views')
    fresh = novels.order_by('-updated_at')

    if tab == 'Discover':
        return [
            section('Top Picks', 'ranked_columns', with_promoted('featured', novels, popular, 9)),
            section('Readers Love These', 'cover_row', novels.order_by('?')[:8]),
            section('Just Updated', 'detail_list', fresh[:6]),
        ]
    if tab == 'New':
        return [
            section('Best Newcomers', 'hero', with_promoted('new_arrival', novels, fresh, 6)),
            section('Trending', 'compact_grid', with_promoted('trending', novels, popular, 6)),
            section('Picks for You', 'rated_list', novels.order_by('?')[:5]),
        ]

    genre_books = novels.filter(genre__name=tab)
    if tab == 'Romance':
        main = genre_books.exclude(tags__name__in=SUBGENRE_TAGS)
        return [
            section('Recommended For You', 'compact_grid',
                    with_promoted('recommended', main, main.order_by('-views'), 6)),
            section("Editor's Pick", 'hero', with_promoted('editors_pick', main, main.order_by('-avg_stars'), 8)),
            section('Completed', 'cover_row', main.filter(status=Story.STATUS_COMPLETED)[:10]),
            section('Love On Top', 'rated_list', with_promoted('love_on_top', main, main.order_by('?'), 10)),
            section('Erotica', 'cover_row', genre_books.filter(tags__name='Erotica').order_by('-views')),
            section('MM Romance', 'detail_list', genre_books.filter(tags__name='MM Romance').order_by('-views')),
        ]
    return [
        section('New Arrival', 'compact_grid', with_promoted('new_arrival', genre_books, genre_books.order_by('-updated_at'), 6)),
        section("Editor's Pick", 'hero', with_promoted('editors_pick', genre_books, genre_books.order_by('-avg_stars'), 8)),
        section('Completed', 'cover_row', genre_books.filter(status=Story.STATUS_COMPLETED)[:10]),
        section('Picks for You', 'rated_list', genre_books.order_by('?')[:10]),
    ]


RANK_TYPES = ['bestsellers', 'rising_stars', 'completed', 'hottest_new', 'readers_choice']


def ranking(rank_type: str, genre: str | None = None):
    qs = books().filter(is_short=False)
    if genre:
        qs = qs.filter(genre__name=genre)
    if rank_type == 'rising_stars':
        since = timezone.now() - timedelta(days=30)
        return qs.filter(created_at__gte=since).order_by('-views') or qs.order_by('-views')
    if rank_type == 'completed':
        return qs.filter(status=Story.STATUS_COMPLETED).order_by('-views')
    if rank_type == 'hottest_new':
        return qs.order_by('-updated_at')
    if rank_type == 'readers_choice':
        return qs.order_by('-avg_stars', '-views')
    return qs.order_by('-views')
