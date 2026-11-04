"""Who an editor can see and the numbers the editorial dashboards show.

Scope rules:
    Chief Editor / admin  → every book, author and Senior Editor
    Senior Editor         → the books assigned to them (and the unassigned pool)
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from apps.catalog.models import Chapter, Story
from apps.engagement.models import ReadingProgress
from apps.users.models import User
from apps.wallet.models import ChapterUnlock, Transaction

from .models import ContractApplication, Escalation, Recommendation, Target, month_start

CE, SE = 'ce', 'se'


def level(user):
    if user.is_platform_admin or user.role == User.ROLE_CE:
        return CE
    return SE


def team_editors(user):
    """The Senior Editors the Chief Editor manages (SEs manage no editors)."""
    if level(user) == CE:
        return User.objects.filter(role=User.ROLE_SE)
    return User.objects.none()


def team_ids(user):
    if level(user) == CE:
        return None  # everyone
    return [user.pk, *team_editors(user).values_list('pk', flat=True)]


def scope_books(user):
    ids = team_ids(user)
    qs = Story.objects.exclude(status=Story.STATUS_DRAFT)
    return qs if ids is None else qs.filter(editor_id__in=ids)


def scope_authors(user):
    ids = team_ids(user)
    authors = User.objects.filter(role=User.ROLE_AUTHOR)
    if ids is None:
        return authors
    return authors.filter(Q(stories__editor_id__in=ids) | Q(recruited_by_id__in=ids)).distinct()


def can_manage_book(user, story):
    ids = team_ids(user)
    return ids is None or story.editor_id in ids or (story.editor_id is None and level(user) == SE)


def assignable_editors(user):
    """Who this user may give a book to."""
    if level(user) == CE:
        return User.objects.filter(role=User.ROLE_SE).order_by('pen_name')
    return User.objects.filter(pk=user.pk)  # an SE can take unassigned books


# ── Activity & risk ─────────────────────────────────────────────────────────
def last_update_map(stories):
    return dict(Chapter.objects.filter(story__in=stories, status=Chapter.STATUS_PUBLISHED)
                .values_list('story').annotate(t=Max('published_at')))


def risk_of(last_update, status):
    """'' (fine), 'slowing' or 'at_risk' from the days since the last chapter."""
    if status == Story.STATUS_COMPLETED or last_update is None:
        return '' if status == Story.STATUS_COMPLETED else 'at_risk'
    days = (timezone.now() - last_update).days
    if days >= settings.BONOVELA['AUTHOR_AT_RISK_DAYS']:
        return 'at_risk'
    if days >= settings.BONOVELA['AUTHOR_INACTIVE_DAYS']:
        return 'slowing'
    return ''


def annotate_books(qs):
    now = timezone.now()
    pub = Q(chapters__status=Chapter.STATUS_PUBLISHED)
    return qs.select_related('author', 'editor', 'genre').annotate(
        n_chapters=Count('chapters', filter=pub, distinct=True),
        n_words=Sum('chapters__word_count', filter=pub),
        last_update=Max('chapters__published_at', filter=pub),
        updates_7d=Count('chapters', filter=pub & Q(chapters__published_at__gte=now - timedelta(days=7)), distinct=True),
        pending_reviews=Count('chapters', filter=pub & Q(chapters__review_status=Chapter.REVIEW_PENDING), distinct=True),
    )


def at_risk_authors(user, limit=None):
    """Authors with ongoing books that have stopped updating."""
    books = annotate_books(scope_books(user).filter(status=Story.STATUS_ONGOING, is_removed=False))
    rows = []
    for b in books:
        r = risk_of(b.last_update, b.status)
        if r:
            rows.append({'story': b, 'author': b.author, 'risk': r,
                         'days': (timezone.now() - b.last_update).days if b.last_update else None})
    rows.sort(key=lambda r: -(r['days'] or 999))
    return rows[:limit] if limit else rows


# ── Book performance ────────────────────────────────────────────────────────
def book_revenue_cents(stories, since=None, until=None):
    """Coins (≈ cents) readers spent on these books: unlocks + gifts."""
    qs = Transaction.objects.filter(story__in=stories, kind__in=[Transaction.KIND_UNLOCK, Transaction.KIND_GIFT])
    if since:
        qs = qs.filter(created_at__gte=since)
    if until:
        qs = qs.filter(created_at__lt=until)
    agg = qs.aggregate(c=Sum('coins'), b=Sum('bonus_used'))
    return -(agg['c'] or 0) + (agg['b'] or 0)


def book_metrics(story):
    now = timezone.now()
    chapters = list(story.chapters.filter(status=Chapter.STATUS_PUBLISHED).order_by('number'))
    first10 = chapters[:10]
    readers = ReadingProgress.objects.filter(story=story)
    started = readers.count()
    reached10 = readers.filter(chapter_number__gte=10).count()
    free = settings.BONOVELA['FREE_SHORT_CHAPTERS' if story.is_short else 'FREE_CHAPTERS']
    past_free = readers.filter(chapter_number__gt=free).count()
    payers = ChapterUnlock.objects.filter(chapter__story=story).values('user').distinct().count()
    recent = [c for c in chapters if c.published_at and c.published_at >= now - timedelta(days=30)]
    return {
        'first10_words': sum(c.word_count for c in first10),
        'first10_avg': sum(c.word_count for c in first10) // len(first10) if first10 else 0,
        'updates_7d': sum(1 for c in recent if c.published_at >= now - timedelta(days=7)),
        'updates_30d': len(recent),
        'per_week': round(len(recent) / 4.3, 1),
        'retention': round(reached10 * 100 / started) if started else None,
        'conversion': round(payers * 100 / past_free) if past_free else None,
        'readers': started,
        'words': sum(c.word_count for c in chapters),
        'chapters': len(chapters),
        'completed': story.status == Story.STATUS_COMPLETED,
        'revenue_cents': book_revenue_cents([story]),
        'revenue_30d': book_revenue_cents([story], since=now - timedelta(days=30)),
        'last_update': chapters[-1].published_at if chapters else None,
        'pending_reviews': sum(1 for c in chapters if c.review_status == Chapter.REVIEW_PENDING),
        'flagged': sum(1 for c in chapters if c.review_status == Chapter.REVIEW_FLAGGED),
    }


# ── Editors: stats, targets, ranking ────────────────────────────────────────
def _month_range(month):
    start = month_start(month)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, end


def editor_stats(editor, month=None):
    """Numbers for one editor (an SE's numbers include their whole team)."""
    start, end = _month_range(month or timezone.localdate())
    ids = team_ids(editor) or []
    if level(editor) == CE:
        books = Story.objects.exclude(status=Story.STATUS_DRAFT)
        recruiters = None
    else:
        books = Story.objects.filter(editor_id__in=ids).exclude(status=Story.STATUS_DRAFT)
        recruiters = ids
    books = annotate_books(books)
    book_list = list(books)
    ongoing = [b for b in book_list if b.status == Story.STATUS_ONGOING and not b.is_removed]
    active = [b for b in ongoing if not risk_of(b.last_update, b.status)]
    reviewed = Chapter.objects.filter(story__in=book_list, reviewed_at__date__gte=start, reviewed_at__date__lt=end)
    n_reviewed = reviewed.count()
    on_time = sum(1 for c in reviewed.only('published_at', 'reviewed_at')
                  if c.published_at and c.reviewed_at and c.reviewed_at - c.published_at <= timedelta(hours=48))
    recruits = User.objects.filter(date_joined__date__gte=start, date_joined__date__lt=end, role=User.ROLE_AUTHOR)
    if recruiters is not None:
        recruits = recruits.filter(recruited_by_id__in=recruiters)
    apps = ContractApplication.objects.filter(story__in=book_list)
    book_reviews = apps.filter(updated_at__date__gte=start, updated_at__date__lt=end).exclude(
        status=ContractApplication.STATUS_PENDING).count()
    contracts = apps.filter(status=ContractApplication.STATUS_SIGNED, updated_at__date__gte=start,
                            updated_at__date__lt=end).count()
    troubled = sum(1 for b in book_list if b.needs_intervention or b.is_removed)
    quality = round(100 * (1 - troubled / len(book_list))) if book_list else 100
    pending = sum(b.pending_reviews for b in book_list)
    return {
        'editor': editor,
        'books': len(book_list),
        'active_books': len(active),
        'completed_books': sum(1 for b in book_list if b.status == Story.STATUS_COMPLETED),
        'pending_reviews': pending,
        'chapters_reviewed': n_reviewed,
        'chapter_review_pct': round(on_time * 100 / n_reviewed) if n_reviewed else (100 if not pending else 0),
        'recruitment': recruits.count(),
        'book_reviews': book_reviews,
        'contracts': contracts,
        'active_authors_pct': round(len(active) * 100 / len(ongoing)) if ongoing else 100,
        'quality_score': quality,
        'interventions': sum(1 for b in book_list if b.needs_intervention),
    }


def targets_for(user, month=None):
    m = month_start(month)
    saved = dict(Target.objects.filter(user=user, month=m).values_list('metric', 'value'))
    return {k: saved.get(k, v) for k, v in Target.DEFAULTS.items()}


def target_progress(stats, targets):
    rows = []
    for key, label in Target.METRIC_CHOICES:
        actual, goal = stats.get(key, 0), targets.get(key, 0)
        rows.append({'key': key, 'label': label, 'actual': actual, 'target': goal,
                     'pct': min(100, round(actual * 100 / goal)) if goal else 100,
                     'is_pct': key.endswith('_pct') or key == 'quality_score'})
    return rows


def performance_score(stats, targets):
    """0–100 ranking score: quality 30, recruitment 20, active authors 20,
    review speed 15, completed books 15."""
    def ratio(a, b):
        return min(1.0, a / b) if b else 1.0
    return round(
        30 * stats['quality_score'] / 100
        + 20 * ratio(stats['recruitment'], targets['recruitment'])
        + 20 * stats['active_authors_pct'] / 100
        + 15 * stats['chapter_review_pct'] / 100
        + 15 * ratio(stats['completed_books'], max(1, stats['books'] // 5))
    )


def team_table(user, month=None):
    """Stats, targets, score and workload flag for each editor in scope."""
    rows = []
    for ed in team_editors(user).order_by('role', 'pen_name'):
        st = editor_stats(ed, month)
        tg = targets_for(ed, month)
        rows.append({**st, 'targets': tg, 'score': performance_score(st, tg)})
    if rows:
        avg = sum(r['books'] for r in rows) / len(rows)
        limit = avg * settings.BONOVELA['EDITOR_OVERLOAD']
        for r in rows:
            r['overloaded'] = avg > 0 and r['books'] > limit
            r['underloaded'] = avg > 0 and r['books'] < avg / 2
            r['inactive'] = not r['editor'].last_login or r['editor'].last_login < timezone.now() - timedelta(days=7)
    rows.sort(key=lambda r: -r['score'])
    for i, r in enumerate(rows, 1):
        r['rank'] = i
    return rows


# ── Recruitment ─────────────────────────────────────────────────────────────
def recruitment_funnel(user):
    ids = team_ids(user)
    recruits = User.objects.filter(role=User.ROLE_AUTHOR, recruited_by__isnull=False)
    if ids is not None:
        recruits = recruits.filter(recruited_by_id__in=ids)
    recruits = recruits.select_related('recruited_by').prefetch_related('stories__applications')
    stages = ['recruited', 'submitted', 'awaiting', 'passed', 'applied', 'kyc', 'contracted']
    counts = dict.fromkeys(stages, 0)
    rows = []
    for a in recruits:
        stories = list(a.stories.all())
        apps = [ap for s in stories for ap in s.applications.all()]
        st = {
            'recruited': True,
            'submitted': any(s.status != Story.STATUS_DRAFT for s in stories),
            'awaiting': any(ap.status == ContractApplication.STATUS_PENDING for ap in apps),
            'passed': any(ap.status in (ContractApplication.STATUS_SE_APPROVED, ContractApplication.STATUS_CONTRACT_SENT,
                                        ContractApplication.STATUS_SIGNED) for ap in apps),
            'applied': bool(apps),
            'kyc': a.kyc_verified,
            'contracted': any(s.is_signed for s in stories),
        }
        for k, v in st.items():
            counts[k] += int(v)
        stage = next((k for k in reversed(stages) if st[k]), 'recruited')
        rows.append({'author': a, 'stage': stage, 'flags': st, 'books': len(stories)})
    labels = {'recruited': 'Recruited', 'submitted': 'Submitted a book', 'awaiting': 'Awaiting review',
              'passed': 'Passed book review', 'applied': 'Applied for contract', 'kyc': 'Completed KYC',
              'contracted': 'Contracted'}
    total = counts['recruited'] or 1
    funnel = [{'key': k, 'label': labels[k], 'n': counts[k], 'pct': round(counts[k] * 100 / total)} for k in stages]
    return {'funnel': funnel, 'rows': rows, 'conversion': round(counts['contracted'] * 100 / total, 1),
            'labels': labels}


# ── Commission ──────────────────────────────────────────────────────────────
def commission_rate(user):
    return settings.BONOVELA['EDITOR_COMMISSION'].get(level(user), 0)


def commission(user, month=None):
    """This user's editorial commission for a month, per book."""
    start, end = _month_range(month or timezone.localdate())
    rate = commission_rate(user)
    books = list(scope_books(user))
    rows = []
    for b in books:
        rev = book_revenue_cents([b], since=start, until=end)
        if rev:
            rows.append({'story': b, 'revenue': rev, 'commission': int(rev * rate)})
    rows.sort(key=lambda r: -r['commission'])
    return {'rate': rate, 'rows': rows, 'total': sum(r['commission'] for r in rows),
            'revenue': sum(r['revenue'] for r in rows), 'month': start}


def book_commissions(month):
    """Record last month's commission as earnings so Finance can pay it.
    Idempotent per editor, book and month."""
    from apps.wallet.models import AuthorEarning
    start, _ = _month_range(month)
    tag = f'Commission {start:%Y-%m}'
    made = 0
    for ed in User.objects.filter(role__in=[User.ROLE_SE, User.ROLE_CE]):
        for row in commission(ed, start)['rows']:
            note = f'{tag} · {row["story"].title}'[:160]
            if row['commission'] and not AuthorEarning.objects.filter(author=ed, type='editor_commission', note=note).exists():
                AuthorEarning.objects.create(author=ed, story=row['story'], type='editor_commission',
                                             amount_cents=row['commission'], note=note)
                made += 1
    return made


def open_items(user):
    """Counts for the badges on the Command Centre."""
    books = scope_books(user)
    apps = ContractApplication.objects.filter(story__in=books, status__in=ContractApplication.OPEN_STATUSES)
    return {
        'pending_book_reviews': apps.filter(status=ContractApplication.STATUS_PENDING).count(),
        'pending_chapter_reviews': Chapter.objects.filter(story__in=books, status=Chapter.STATUS_PUBLISHED,
                                                          review_status=Chapter.REVIEW_PENDING).count(),
        'pending_contracts': apps.filter(status__in=[ContractApplication.STATUS_SE_APPROVED,
                                                     ContractApplication.STATUS_CONTRACT_SENT]).count(),
        'open_escalations': Escalation.objects.filter(status=Escalation.STATUS_OPEN).filter(
            Q(raised_by=user) if level(user) != CE else Q()).count(),
        'pending_recommendations': Recommendation.objects.filter(status=Recommendation.STATUS_PENDING).filter(
            Q(recommended_by=user) if level(user) != CE else Q()).count(),
    }
