"""Text overlap checks for the Plagiarism Checker and Duplicate Chapter
Detector.

Each chapter becomes a set of hashed 6-word "shingles". Shingles that appear
in many different books (stock phrases, platform boilerplate) are ignored, so
a match means a passage that is rare across the catalogue appears in both
places. This only compares against books on Bonovela, not the open web.
"""

import re
import zlib
from collections import defaultdict

from django.core.cache import cache

from apps.catalog.models import Chapter

K = 6
COMMON_BOOKS = 5  # a shingle seen in this many books is treated as boilerplate
INDEX_KEY = 'editorial:shingle-index'
INDEX_TTL = 600

_word = re.compile(r"[a-z0-9']+")


def shingles(text):
    words = _word.findall((text or '').lower())
    return {zlib.crc32(' '.join(words[i:i + K]).encode()) for i in range(max(0, len(words) - K + 1))}


def _index():
    """shingle → {story_id: [chapter_id, ...]} for every published chapter."""
    idx = cache.get(INDEX_KEY)
    if idx is not None:
        return idx
    idx = defaultdict(dict)
    for ch in Chapter.objects.filter(status=Chapter.STATUS_PUBLISHED).only('id', 'story_id', 'content').iterator():
        for h in shingles(ch.content):
            idx[h].setdefault(ch.story_id, []).append(ch.id)
    idx = dict(idx)
    cache.set(INDEX_KEY, idx, INDEX_TTL)
    return idx


def refresh_index():
    cache.delete(INDEX_KEY)


def _distinctive(sh, idx):
    return {h for h in sh if len(idx.get(h, ())) < COMMON_BOOKS}


def _matches(sh, idx, exclude_story=None):
    """Count shared distinctive shingles per other chapter."""
    hits = defaultdict(int)
    for h in sh:
        for story_id, chapter_ids in idx.get(h, {}).items():
            if story_id == exclude_story:
                continue
            for cid in set(chapter_ids):
                hits[cid] += 1
    return hits


def plagiarism_report(story, min_pct=15):
    """Chapters of [story] that share passages with chapters of other books."""
    idx = _index()
    rows = []
    chapters = list(story.chapters.filter(status__in=[Chapter.STATUS_PUBLISHED, Chapter.STATUS_DRAFT])
                    .order_by('number'))
    for ch in chapters:
        own = _distinctive(shingles(ch.content), idx)
        if len(own) < 20:
            continue
        hits = _matches(own, idx, exclude_story=story.pk)
        if not hits:
            continue
        best_id, shared = max(hits.items(), key=lambda kv: kv[1])
        pct = round(shared * 100 / len(own))
        if pct >= min_pct:
            rows.append({'chapter': ch, 'pct': pct, 'match_id': best_id})
    sources = Chapter.objects.select_related('story').in_bulk([r['match_id'] for r in rows])
    for r in rows:
        src = sources.get(r['match_id'])
        r['match'] = src
        # Whichever was published first is most likely the original.
        r['ours_first'] = bool(src and r['chapter'].published_at and src.published_at
                               and r['chapter'].published_at < src.published_at)
    return {'rows': sorted(rows, key=lambda r: -r['pct']), 'checked': len(chapters),
            'score': max((r['pct'] for r in rows), default=0)}


def check_text(text, min_pct=10):
    """Pasted text against the whole catalogue."""
    idx = _index()
    own = _distinctive(shingles(text), idx)
    if len(own) < 10:
        return {'too_short': True, 'rows': []}
    hits = _matches(own, idx)
    top = sorted(hits.items(), key=lambda kv: -kv[1])[:10]
    chapters = Chapter.objects.select_related('story').in_bulk([cid for cid, _ in top])
    rows = [{'match': chapters[cid], 'pct': round(n * 100 / len(own))} for cid, n in top
            if cid in chapters and n * 100 / len(own) >= min_pct]
    return {'too_short': False, 'rows': rows, 'shingles': len(own)}


def duplicate_report(story, min_pct=50):
    """Pairs of chapters within one book that repeat each other."""
    idx = _index()
    chapters = list(story.chapters.exclude(status=Chapter.STATUS_TRASH).order_by('number'))
    sets = [(ch, _distinctive(shingles(ch.content), idx)) for ch in chapters]
    sets = [(ch, s) for ch, s in sets if len(s) >= 20]
    pairs = []
    for i, (a, sa) in enumerate(sets):
        for b, sb in sets[i + 1:]:
            inter = len(sa & sb)
            if not inter:
                continue
            pct = round(inter * 100 / min(len(sa), len(sb)))
            if pct >= min_pct:
                pairs.append({'a': a, 'b': b, 'pct': pct})
    return {'pairs': sorted(pairs, key=lambda p: -p['pct']), 'checked': len(chapters)}
