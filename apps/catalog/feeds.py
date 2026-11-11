"""Discover feed and rankings, shared by the API and the web.

Sections the Chief Editor promotes (Editor's Pick, Trending, …) show the
promoted books first; anything left is filled from the catalogue. Category
tabs (Romance, Mafia, …) and Short Stories are built from curated shelves.
"""

from datetime import timedelta

from django.db.models import Avg, Count, Prefetch, Q, Sum
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
FILL_ORDER = ['views', 'avg_stars', 'updated_at', 'created_at']
# Fill candidates per request, most-read first. Filtering happens in Python
# so a page of shelves costs a handful of queries, not several per shelf.
FILL_POOL = 300


def shelf_sections(categories, base):
    """{category: [section, …]}. Each shelf shows the Chief Editor's approved
    picks in order, then books tagged like the shelf's name, then books from
    the category's genres (novels) or any short, up to SHELF_SIZE."""
    categories = list(categories.prefetch_related(
        'genres', Prefetch('shelves__entries', queryset=ShelfBook.objects.filter(status=ShelfBook.STATUS_APPROVED),
                           to_attr='approved')))
    shelves = [sh for cat in categories for sh in cat.shelves.all()]
    pool = list(base.order_by('-views')[:FILL_POOL])
    by_id = {b.id: b for b in pool}
    missing = {e.story_id for sh in shelves for e in sh.approved} - by_id.keys()
    by_id.update({b.id: b for b in base.filter(id__in=missing)})

    def ranked(order):
        return sorted(pool, key=lambda b: getattr(b, order) or 0, reverse=True)

    out = {}
    for cat in categories:
        genre_ids = {g.id for g in cat.genres.all()}
        out[cat] = []
        for i, shelf in enumerate(cat.shelves.all()):
            picks = [by_id[e.story_id] for e in shelf.approved if e.story_id in by_id]
            seen = {b.id for b in picks}
            candidates = ranked(FILL_ORDER[i % len(FILL_ORDER)])
            tagged = [b for b in candidates if any(t.name == shelf.title for t in b.tags.all())]
            in_genre = [b for b in candidates if b.genre_id in genre_ids] if genre_ids else candidates
            for b in tagged + in_genre:
                if len(picks) >= SHELF_SIZE:
                    break
                if b.id not in seen:
                    picks.append(b)
                    seen.add(b.id)
            out[cat].append(section(shelf.title, shelf.layout, picks))
    return out


def short_collections():
    """Short Stories categories, each a list of shelf sections."""
    cats = shelf_sections(ShelfCategory.objects.filter(kind=ShelfCategory.KIND_SHORT), books().filter(is_short=True))
    return [{'title': cat.name, 'sections': sections} for cat, sections in cats.items()]


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

    cats = shelf_sections(ShelfCategory.objects.filter(kind=ShelfCategory.KIND_NOVEL, name=tab), novels)
    if cats:
        return next(iter(cats.values()))
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
