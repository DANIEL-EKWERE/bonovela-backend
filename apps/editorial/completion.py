"""AI Completion Review: whole-manuscript checks for completion
applications, plus an optional written assessment from Claude."""

import logging

from django.conf import settings
from django.utils import timezone

from apps.catalog.models import Chapter, word_count

from . import similarity

log = logging.getLogger(__name__)

MIN_WORDS = 50_000  # completion bonus needs a full-length book
MAX_GAP_DAYS = 30


def analyse(story):
    chapters = list(story.chapters.filter(status=Chapter.STATUS_PUBLISHED).order_by('number'))
    words = [c.word_count for c in chapters]
    total = sum(words)
    avg = total // len(words) if words else 0
    dates = [c.published_at for c in chapters if c.published_at]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    dup = similarity.duplicate_report(story)
    plag = similarity.plagiarism_report(story)
    numbers = [c.number for c in chapters]
    missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers)) if numbers else []
    last = chapters[-1] if chapters else None
    synopsis_words = word_count(story.synopsis)

    def check(key, label, ok, detail, warn=False):
        return {'key': key, 'label': label, 'status': 'pass' if ok else ('warn' if warn else 'fail'), 'detail': detail}

    checks = [
        check('status', 'Marked complete', story.status == 'completed',
              'Story status is Completed.' if story.status == 'completed' else 'The author has not marked the book complete.'),
        check('length', f'At least {MIN_WORDS:,} words', total >= MIN_WORDS, f'{total:,} words in {len(chapters)} chapters.'),
        check('numbering', 'No missing chapters', not missing,
              'Chapters run in order.' if not missing else f'Missing chapter numbers: {", ".join(map(str, missing[:12]))}.'),
        check('ending', 'Ending has substance', bool(last) and last.word_count >= avg * 0.6,
              f'Final chapter is {last.word_count if last else 0:,} words (average {avg:,}).', warn=True),
        check('gaps', f'No gap over {MAX_GAP_DAYS} days', not gaps or max(gaps) <= MAX_GAP_DAYS,
              f'Longest gap between chapters: {max(gaps) if gaps else 0} days.', warn=True),
        check('duplicates', 'No repeated chapters', not dup['pairs'],
              'No chapter repeats another.' if not dup['pairs'] else f'{len(dup["pairs"])} chapter pair(s) overlap heavily.'),
        check('plagiarism', 'No matches with other books', not plag['rows'],
              'No passages shared with other Bonovela books.' if not plag['rows']
              else f'{len(plag["rows"])} chapter(s) share passages with other books (up to {plag["score"]}%).'),
        check('synopsis', 'Synopsis 20–300 words', 20 <= synopsis_words <= 300, f'{synopsis_words} words.', warn=True),
    ]
    fails = sum(c['status'] == 'fail' for c in checks)
    warns = sum(c['status'] == 'warn' for c in checks)
    return {
        'chapters': len(chapters), 'words': total, 'avg_words': avg,
        'shortest': min(words) if words else 0, 'longest': max(words) if words else 0,
        'span_days': (dates[-1] - dates[0]).days if len(dates) > 1 else 0,
        'longest_gap': max(gaps) if gaps else 0,
        'per_week': round(len(chapters) / max(1, ((dates[-1] - dates[0]).days if len(dates) > 1 else 7) / 7), 1),
        'duplicate_pairs': len(dup['pairs']), 'plagiarism_hits': len(plag['rows']),
        'checks': checks, 'fails': fails, 'warns': warns,
        'verdict': 'ready' if not fails and not warns else ('review' if not fails else 'not_ready'),
    }


def ai_enabled():
    return bool(settings.ANTHROPIC_API_KEY)


SYSTEM = (
    'You are a senior fiction editor at Bonovela, a web-novel platform. An author has applied for a '
    'completion review of a finished serial. Using the manuscript excerpts and statistics provided, write a '
    'short assessment for the editorial team with four headed parts: Overall verdict (one line: Ready, Ready '
    'with minor fixes, or Not ready), Ending and resolution, Consistency and pacing, Issues to raise with the '
    'author. Base every point on the text supplied; say so when the excerpts are not enough to judge. Keep it '
    'under 350 words and plain text.'
)


def _excerpt(story):
    chapters = list(story.chapters.filter(status=Chapter.STATUS_PUBLISHED).order_by('number'))
    picks = chapters[:2] + chapters[len(chapters) // 2:len(chapters) // 2 + 1] + chapters[-3:]
    seen, parts = set(), []
    for c in picks:
        if c.pk in seen:
            continue
        seen.add(c.pk)
        parts.append(f'--- Chapter {c.number}: {c.display_title} ---\n{c.content[:6000]}')
    return '\n\n'.join(parts)


def ai_review(story, stats):
    """Ask Claude for a written assessment. Returns '' when not configured or
    when the request fails (the built-in checks still stand)."""
    if not ai_enabled():
        return ''
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    prompt = (
        f'Title: {story.title}\nGenre: {story.genre or "—"}\nSynopsis: {story.synopsis}\n\n'
        f'Statistics: {stats["chapters"]} chapters, {stats["words"]:,} words, average {stats["avg_words"]:,} '
        f'words per chapter, longest gap {stats["longest_gap"]} days, {stats["duplicate_pairs"]} repeated '
        f'chapter pairs, {stats["plagiarism_hits"]} chapters matching other books.\n\n'
        f'Excerpts (opening, middle and ending chapters):\n\n{_excerpt(story)}'
    )
    try:
        response = client.beta.messages.create(
            model=settings.AI_REVIEW_MODEL,
            max_tokens=16000,
            thinking={'type': 'adaptive'},
            output_config={'effort': 'medium'},
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            system=SYSTEM,
            messages=[{'role': 'user', 'content': prompt}],
        )
    except anthropic.RateLimitError:
        log.warning('AI completion review rate-limited for story %s', story.pk)
        return ''
    except anthropic.APIStatusError as e:
        log.warning('AI completion review failed for story %s: %s', story.pk, e.status_code)
        return ''
    except anthropic.APIConnectionError:
        log.warning('AI completion review could not reach the API for story %s', story.pk)
        return ''
    if response.stop_reason == 'refusal':
        return ''
    return '\n'.join(b.text for b in response.content if b.type == 'text').strip()


def run(application):
    stats = analyse(application.story)
    application.analysis = stats
    application.ai_review = ai_review(application.story, stats)
    application.analysed_at = timezone.now()
    application.save(update_fields=['analysis', 'ai_review', 'analysed_at', 'updated_at'])
    return application
