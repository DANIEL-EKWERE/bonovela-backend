"""Discover feed and rankings, shared by the API and the web.

Sections the Chief Editor promotes (Editor's Pick, Trending, …) show the
promoted books first; anything left is filled from the catalogue. Category
tabs (Romance, Mafia, …) and Short Stories are built from curated shelves.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from apps.editorial.models import Promotion, ShelfBook, ShelfCategory

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


SHELF_SIZE = 8
# Rotated across a category's shelves so filled-in shelves don't all match.
FILL_ORDER = ['-views', '-avg_stars', '-updated_at', '-created_at']


def shelf_books(shelf, base, fill_order='-views'):
    """Approved picks in the Chief Editor's order, then books with a tag named
    like the shelf (or else from the category's genres) up to SHELF_SIZE."""
    picked_ids = list(shelf.entries.filter(status=ShelfBook.STATUS_APPROVED).values_list('story_id', flat=True))
    by_id = {b.id: b for b in base.filter(id__in=picked_ids)}
    picks = [by_id[i] for i in picked_ids if i in by_id]
    if base.filter(tags__name=shelf.title).exists():
        fill = base.filter(tags__name=shelf.title)
    elif shelf.category.kind == ShelfCategory.KIND_NOVEL:
        fill = base.filter(genre__in=shelf.category.genres.all())
    else:
        fill = base
    seen = {b.id for b in picks}
    for b in fill.order_by(fill_order).distinct()[:SHELF_SIZE * 2]:
        if len(picks) >= SHELF_SIZE:
            break
        if b.id not in seen:
            picks.append(b)
            seen.add(b.id)
    return picks


def shelf_sections(category, base):
    return [
        section(shelf.title, shelf.layout, shelf_books(shelf, base, FILL_ORDER[i % len(FILL_ORDER)]))
        for i, shelf in enumerate(category.shelves.all())
    ]


def short_collections():
    """Short Stories categories, each a list of shelf sections."""
    shorts = books().filter(is_short=True)
    return [
        {'title': cat.name, 'sections': shelf_sections(cat, shorts)}
        for cat in ShelfCategory.objects.filter(kind=ShelfCategory.KIND_SHORT).prefetch_related('shelves')
    ]


def discover_feed(tab: str):
    """Same tabs and section order as the app."""
    novels = books().filter(is_short=False)
    popular = novels.order_by('-views')
    fresh = novels.order_by('-updated_at')

    if tab == 'Discover':
        return [
            section('Top Picks', 'cover_grid', with_promoted('featured', novels, popular, 8)),
            section('Readers Love These', 'cover_row', novels.order_by('?')[:8]),
            section('Just Updated', 'detail_list', fresh[:6]),
        ]
    if tab == 'New':
        return [
            section('Best Newcomers', 'hero', with_promoted('new_arrival', novels, fresh, 6)),
            section('Trending', 'compact_grid', with_promoted('trending', novels, popular, 6)),
            section('Picks for You', 'rated_list', novels.order_by('?')[:5]),
        ]

    category = ShelfCategory.objects.filter(kind=ShelfCategory.KIND_NOVEL, name=tab).first()
    if category:
        return shelf_sections(category, novels)
    genre_books = novels.filter(genre__name=tab)
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
