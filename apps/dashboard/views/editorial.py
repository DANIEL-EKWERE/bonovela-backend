"""Editorial workspaces: Chief Editor (platform-wide) and Senior Editor
(their own books, authors and recruits). Overview, book management, submissions
and promotions live here; people and quality tools are in the sibling
modules."""

import mimetypes
from datetime import datetime, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Chapter, Story
from apps.editorial import services, team
from apps.editorial.models import BookTransfer, ContractApplication as App
from apps.editorial.models import EditorialNote, Promotion, PromotionRequest, Recommendation, TeamMessage
from apps.editorial.models import Shelf, ShelfBook, ShelfCategory
from apps.engagement.models import InboxMessage
from apps.users.models import User

# Which contract-application statuses each level acts on.
ACTIONABLE = {
    team.SE: [App.STATUS_PENDING],
    team.CE: [App.STATUS_PENDING, App.STATUS_SE_APPROVED],
}


def editor_area(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_editor or request.user.is_platform_admin):
            raise PermissionDenied
        request.level = team.level(request.user)
        return view(request, *args, **kwargs)

    return wrapper


def chief_area(view):
    """Chief Editor (and platform admins) only."""

    @editor_area
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.level != team.CE:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def _int(value, default=7):
    try:
        return max(1, min(int(value), 30))
    except (TypeError, ValueError):
        return default


def run(request, fn, *args, ok='Done.', **kwargs):
    try:
        fn(*args, **kwargs)
        messages.success(request, ok)
        return True
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, e.messages[0] if hasattr(e, 'messages') else str(e) or 'Not allowed.')
        return False


def visible_messages(user):
    q = Q(recipient=user) | Q(sender=user) | Q(recipient__isnull=True, team_lead__isnull=True)
    if user.supervisor_id:
        q |= Q(recipient__isnull=True, team_lead_id=user.supervisor_id)
    q |= Q(recipient__isnull=True, team_lead=user)
    return TeamMessage.objects.filter(q).select_related('sender', 'recipient')


def _book_or_403(request, pk):
    story = get_object_or_404(Story.objects.select_related('author', 'editor', 'genre'), pk=pk)
    ids = team.team_ids(request.user)
    if ids is not None and story.editor_id not in ids and not (story.editor_id is None and request.level == team.SE):
        raise PermissionDenied
    return story


def _prior_context(app):
    """Earlier rejections that match this application (re-checked live)."""
    if not app:
        return {'prior': [], 'prior_app': None}
    from apps.editorial.history import find_prior
    from apps.users.models import IPLog
    prior = find_prior(app)
    for m in prior:
        m['rejected_at'] = datetime.fromisoformat(m['rejected_at'])
    return {
        'prior': prior, 'prior_app': app,
        'prior_rejected': any(m['status'] == App.STATUS_REJECTED for m in prior),
        'prior_other_team': any(m['other_team'] for m in prior),
        'author_ips': IPLog.objects.filter(user=app.story.author)[:10],
    }


# ── Command Centre ───────────────────────────────────────────────────────────
@editor_area
def home(request):
    user, lv = request.user, request.level
    now = timezone.now()
    books = team.scope_books(user)
    stats = team.editor_stats(user)
    targets = team.targets_for(user)
    ctx = {
        'lv': lv,
        'counts': {
            'books': books.count(),
            'authors': team.scope_authors(user).count(),
            'editors': team.team_editors(user).count(),
            'active': books.filter(status=Story.STATUS_ONGOING, is_removed=False).count(),
            'completed': books.filter(status=Story.STATUS_COMPLETED).count(),
            'suspended': books.filter(is_removed=True).count(),
            'unassigned': Story.objects.filter(editor__isnull=True).exclude(status=Story.STATUS_DRAFT).count(),
        },
        'open': team.open_items(user),
        'stats': stats,
        'progress': team.target_progress(stats, targets),
        'revenue': team.book_revenue_cents(books, since=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)),
        'commission': team.commission(user),
        'risk': team.at_risk_authors(user, limit=6),
        'interventions': team.annotate_books(books.filter(needs_intervention=True))[:6],
        'recruitment': team.recruitment_funnel(user)['funnel'],
        'inbox': visible_messages(user).exclude(sender=user)[:4],
        'mine': App.objects.filter(story__in=books, status__in=ACTIONABLE[lv]).select_related('story__author')[:6],
        'flagged': App.objects.filter(story__in=books, status__in=App.OPEN_STATUSES).exclude(prior_flag='')
        .select_related('story__author')[:6],
    }
    if lv == team.CE:
        ctx['workload'] = team.team_table(user)[:8]
        ctx['pending_recs'] = Recommendation.objects.filter(status=Recommendation.STATUS_PENDING).count()
    ctx['signed_week'] = App.objects.filter(story__in=books, status=App.STATUS_SIGNED,
                                            updated_at__gte=now - timedelta(days=7)).count()
    return render(request, 'dashboard/editorial/home.html', ctx)


# ── Book Management ──────────────────────────────────────────────────────────
BOOK_TABS = [('all', 'All'), ('active', 'Active'), ('completed', 'Completed'), ('suspended', 'Suspended'),
             ('unassigned', 'Unassigned'), ('at_risk', 'At risk'), ('intervention', 'Needs attention')]


@editor_area
def books(request):
    user, lv = request.user, request.level
    f = request.GET.get('f', 'all')
    if f == 'unassigned':
        qs = Story.objects.filter(editor__isnull=True).exclude(status=Story.STATUS_DRAFT)
    else:
        qs = team.scope_books(user)
    if f == 'active':
        qs = qs.filter(status=Story.STATUS_ONGOING, is_removed=False)
    elif f == 'completed':
        qs = qs.filter(status=Story.STATUS_COMPLETED)
    elif f == 'suspended':
        qs = qs.filter(is_removed=True)
    elif f == 'intervention':
        qs = qs.filter(needs_intervention=True)
    editor_id = request.GET.get('editor', '')
    if editor_id.isdigit():
        qs = qs.filter(editor_id=editor_id)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(author__pen_name__icontains=q) | Q(author__email__icontains=q))
    rows = list(team.annotate_books(qs).order_by('-updated_at')[:300])
    for b in rows:
        b.risk = team.risk_of(b.last_update, b.status)
    if f == 'at_risk':
        rows = [b for b in rows if b.risk]
    return render(request, 'dashboard/editorial/books.html', {
        'books': rows, 'f': f, 'q': q, 'tabs': BOOK_TABS,
        'editors': team.assignable_editors(user), 'editor_id': editor_id, 'lv': lv,
    })


@editor_area
def book(request, pk):
    story = _book_or_403(request, pk)
    lv = request.level
    chapters = story.chapters.exclude(status=Chapter.STATUS_TRASH).order_by('number')
    reading = chapters.filter(number=request.GET.get('chapter') or 0).first() or \
        chapters.filter(status=Chapter.STATUS_PUBLISHED).first()
    in_team = team.team_ids(request.user)
    others = []
    if lv == team.SE:
        others = User.objects.filter(role=User.ROLE_SE).exclude(pk__in=in_team)
    return render(request, 'dashboard/editorial/book.html', {
        'story': story, 'chapters': chapters, 'reading': reading, 'lv': lv,
        'metrics': team.book_metrics(story), 'risk': None,
        'notes': story.editorial_notes.select_related('editor', 'chapter')[:30], 'app': story.applications.first(),
        'editors': team.assignable_editors(request.user), 'other_editors': others,
        'transfers': story.transfers.select_related('from_editor', 'to_editor', 'by')[:10],
        'recs': Recommendation.objects.filter(story=story, status=Recommendation.STATUS_PENDING),
        **_prior_context(story.applications.exclude(prior_flag='').first()),
        'pending_reviews': chapters.filter(status=Chapter.STATUS_PUBLISHED, review_status=Chapter.REVIEW_PENDING).count(),
    })


def _assign(story, editor, by, note=''):
    BookTransfer.objects.create(story=story, from_editor=story.editor, to_editor=editor, by=by, note=note)
    story.editor, story.assigned_at = editor, timezone.now()
    story.save(update_fields=['editor', 'assigned_at', 'updated_at'])
    if editor and editor != by:
        TeamMessage.objects.create(sender=by, recipient=editor, kind='instruction',
                                   title=f'New book: {story.display_title}',
                                   body=note or f'{by.display_name} assigned "{story.display_title}" to you.')


@editor_area
@require_POST
def book_action(request, pk):
    story = _book_or_403(request, pk)
    user, lv = request.user, request.level
    action = request.POST.get('action')
    text = request.POST.get('text', '').strip()
    back = redirect(request.POST.get('next') or f"/editorial/books/{story.pk}/")

    if action == 'note':
        if text:
            EditorialNote.objects.create(story=story, editor=user, text=text, chapter_id=request.POST.get('chapter') or None)
            messages.success(request, 'Note added.')
    elif action in ('review_approve', 'review_flag', 'revise_chapter'):
        ch = get_object_or_404(Chapter, pk=request.POST.get('chapter'), story=story)
        ch.reviewed_by, ch.reviewed_at = user, timezone.now()
        if action == 'review_approve':
            ch.review_status = Chapter.REVIEW_APPROVED
            messages.success(request, f'Chapter {ch.number} approved.')
        else:
            ch.review_status = Chapter.REVIEW_FLAGGED
            if action == 'revise_chapter':
                ch.status = Chapter.STATUS_REVISION
                InboxMessage.objects.create(user=story.author, story=story, title=f'Chapter {ch.number} needs changes',
                                            body=text or 'An editor sent this chapter back for revision.')
            messages.success(request, f'Chapter {ch.number} {"sent back to the author" if action == "revise_chapter" else "flagged"}.')
            if text:
                EditorialNote.objects.create(story=story, editor=user, chapter=ch, text=text)
        ch.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'status', 'updated_at'])
    elif action == 'review_approve_all':
        n = story.chapters.filter(status=Chapter.STATUS_PUBLISHED, review_status=Chapter.REVIEW_PENDING).update(
            review_status=Chapter.REVIEW_APPROVED, reviewed_by=user, reviewed_at=timezone.now())
        messages.success(request, f'Approved {n} chapter{"s" if n != 1 else ""}.')
    elif action == 'assign':
        editor = get_object_or_404(team.assignable_editors(user), pk=request.POST.get('editor'))
        _assign(story, editor, user, text)
        messages.success(request, f'Assigned to {editor.display_name}.')
    elif action == 'recommend_transfer':
        target = get_object_or_404(User, pk=request.POST.get('editor'), role=User.ROLE_SE)
        Recommendation.objects.create(kind='transfer_out', story=story, target_editor=target, note=text,
                                      recommended_by=user)
        messages.success(request, 'Transfer sent to the Chief Editor for approval.')
    elif action == 'intervention':
        story.needs_intervention = not story.needs_intervention
        story.intervention_note = text[:200] if story.needs_intervention else ''
        story.save(update_fields=['needs_intervention', 'intervention_note', 'updated_at'])
        messages.success(request, 'Flagged for urgent attention.' if story.needs_intervention else 'Flag cleared.')
    elif action in ('remove', 'restore'):
        if lv != team.CE:
            messages.error(request, 'Only the Chief Editor can suspend books. Send a recommendation instead.')
        else:
            story.is_removed = action == 'remove'
            story.save(update_fields=['is_removed', 'updated_at'])
            messages.success(request, 'Book suspended.' if story.is_removed else 'Book restored.')
    elif action == 'recommend_suspend':
        Recommendation.objects.create(kind='suspend_book', story=story, note=text, recommended_by=user)
        messages.success(request, 'Suspension recommended to the Chief Editor.')
    return back


# ── Submission History (contract applications) ───────────────────────────────
@editor_area
def queue(request):
    lv = request.level
    tab = request.GET.get('tab', 'mine')
    qs = App.objects.filter(story__in=team.scope_books(request.user)).select_related(
        'story__author', 'story__genre', 'story__editor', 'se', 'ce')
    if tab == 'mine':
        qs = qs.filter(status__in=ACTIONABLE[lv])
    elif tab == 'open':
        qs = qs.filter(status__in=App.OPEN_STATUSES)
    elif tab == 'flagged':
        qs = qs.exclude(prior_flag='')
    else:
        qs = qs.exclude(status__in=App.OPEN_STATUSES)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(story__title__icontains=q) | Q(story__author__pen_name__icontains=q))
    flagged = App.objects.filter(story__in=team.scope_books(request.user), status__in=App.OPEN_STATUSES).exclude(
        prior_flag='').count()
    return render(request, 'dashboard/editorial/queue.html', {'apps': qs[:200], 'tab': tab, 'q': q, 'flagged': flagged})


@editor_area
def application_id(request, pk, side):
    """An applicant's ID photo, for editors who can see the book only. Files
    live outside MEDIA_ROOT, so this is the only way to open them."""
    app = get_object_or_404(App.objects.select_related('story'), pk=pk)
    _book_or_403(request, app.story_id)
    image = {'front': app.id_front, 'back': app.id_back}.get(side)
    if not image:
        raise Http404
    response = FileResponse(image.open('rb'), content_type=mimetypes.guess_type(image.name)[0] or 'image/jpeg')
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response

@editor_area
def application(request, pk):
    app = get_object_or_404(App.objects.select_related('story__author', 'story__genre'), pk=pk)
    story = app.story
    _book_or_403(request, story.pk)
    lv = request.level
    if request.method == 'POST':
        action = request.POST.get('action')
        feedback = request.POST.get('feedback', '').strip()
        if action == 'note':
            if feedback:
                EditorialNote.objects.create(story=story, editor=request.user, text=feedback)
                messages.success(request, 'Note added.')
        elif action in ('se_approve', 'se_revise', 'se_reject'):
            run(request, services.se_decide, app, request.user, action.split('_')[1], feedback=feedback,
                ok={'se_approve': 'Recommended — the Chief Editor will send the contract.',
                    'se_revise': 'Sent back to the author.', 'se_reject': 'Rejected.'}[action])
        elif action == 'ce_contract':
            run(request, services.ce_send_contract, app, request.user,
                contract_type=request.POST.get('contract_type', 'non_exclusive'), ok='Contract sent to the author.')
        return redirect('dashboard:editorial_application', app.pk)

    chapters = story.chapters.filter(status=Chapter.STATUS_PUBLISHED).order_by('number')
    reading = chapters.filter(number=request.GET['chapter']).first() if request.GET.get('chapter') else None
    from apps.editorial import geo
    return render(request, 'dashboard/editorial/application.html', {
        **_prior_context(app),
        'app': app, 'story': story, 'role': request.user.role, 'lv': lv, 'is_chief': lv == team.CE,
        'id_sides': [('front', 'Front of ID'), ('back', 'Back of ID')],
        'app_country': geo.country_name(app.country), 'app_state': geo.state_name(app.state),
        'chapters': chapters, 'reading': reading or chapters.first(),
        'notes': story.editorial_notes.select_related('editor')[:30],
        'can_act': app.status in ACTIONABLE[lv],
    })


# ── Curated shelves (editors suggest, CE approves and arranges) ──────────────
@editor_area
def shelves(request):
    kind = request.GET.get('kind', ShelfCategory.KIND_NOVEL)
    if kind not in dict(ShelfCategory.KIND_CHOICES):
        kind = ShelfCategory.KIND_NOVEL
    if request.method == 'POST':
        action = request.POST.get('action')
        user = request.user
        if action == 'add':
            shelf = get_object_or_404(Shelf, pk=request.POST.get('shelf'))
            story = get_object_or_404(team.scope_books(user), pk=request.POST.get('story'))
            run(request, services.add_to_shelf, user, shelf, story,
                ok='Added to the shelf.' if request.level == team.CE else 'Sent to the Chief Editor for approval.')
        elif action in ('approve', 'reject'):
            entry = get_object_or_404(ShelfBook, pk=request.POST.get('entry'))
            run(request, services.decide_shelf_book, user, entry, approve=action == 'approve',
                ok='Approved.' if action == 'approve' else 'Rejected.')
        elif action == 'remove':
            run(request, services.remove_from_shelf, user, get_object_or_404(ShelfBook, pk=request.POST.get('entry')),
                ok='Removed from the shelf.')
        elif action in ('up', 'down', 'rename'):
            model = {'book': ShelfBook, 'shelf': Shelf, 'category': ShelfCategory}.get(request.POST.get('type'))
            if model:
                obj = get_object_or_404(model, pk=request.POST.get('id'))
                if action == 'rename':
                    run(request, services.rename, user, obj, request.POST.get('name', ''), request.POST.get('layout'),
                        ok='Saved.')
                else:
                    run(request, services.move, user, obj, -1 if action == 'up' else 1, ok='Moved.')
        return redirect(f"{request.path}?kind={kind}")

    categories = ShelfCategory.objects.filter(kind=kind).prefetch_related(
        Prefetch('shelves__entries', queryset=ShelfBook.objects.exclude(status=ShelfBook.STATUS_REJECTED)
                 .select_related('story', 'added_by').order_by('order', 'pk')))
    books = (team.scope_books(request.user).filter(is_signed=True, is_removed=False,
                                                   is_short=kind == ShelfCategory.KIND_SHORT).order_by('title'))
    return render(request, 'dashboard/editorial/shelves.html', {
        'categories': categories, 'kind': kind, 'kinds': ShelfCategory.KIND_CHOICES, 'books': books,
        'is_chief': request.level == team.CE, 'layouts': Shelf.LAYOUT_CHOICES,
        'pending': ShelfBook.objects.filter(status=ShelfBook.STATUS_PENDING, shelf__category__kind=kind).count(),
    })


# ── Promotions (SE requests, CE decides) ─────────────────────────────────────
@editor_area
def promotions(request):
    lv = request.level
    now = timezone.now()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'request':
            story = get_object_or_404(Story, pk=request.POST.get('story'))
            run(request, services.request_promotion, request.user, story, request.POST.getlist('sections'),
                _int(request.POST.get('days')), request.POST.get('reason', '').strip() or '—',
                ok='Request sent to the Chief Editor.')
        elif action in ('approve', 'reject'):
            req = get_object_or_404(PromotionRequest, pk=request.POST.get('request'))
            run(request, services.decide_promotion, request.user, req, approve=action == 'approve',
                note=request.POST.get('note', ''), sections=request.POST.getlist('sections') or None,
                ok='Promotion is live.' if action == 'approve' else 'Request rejected.')
        elif action == 'promote':
            story = get_object_or_404(Story, pk=request.POST.get('story'))
            if not request.POST.getlist('sections'):
                messages.error(request, 'Pick at least one section.')
            for section in request.POST.getlist('sections'):
                if not run(request, services.promote, request.user, story, section, _int(request.POST.get('days')),
                           ok='Promotion is live.'):
                    break
        elif action == 'end':
            promo = get_object_or_404(Promotion, pk=request.POST.get('promotion'))
            run(request, services.end_promotion, request.user, promo, ok='Promotion ended.')
        return redirect('dashboard:editorial_promotions')

    signed = team.scope_books(request.user).filter(is_signed=True, is_removed=False).order_by('title')
    return render(request, 'dashboard/editorial/promotions.html', {
        'role': request.user.role, 'is_chief': lv == team.CE, 'lv': lv,
        'sections': Promotion.SECTION_CHOICES,
        'pending': PromotionRequest.objects.filter(status=PromotionRequest.STATUS_PENDING).select_related('story', 'requested_by'),
        'recent': PromotionRequest.objects.exclude(status=PromotionRequest.STATUS_PENDING).select_related(
            'story', 'requested_by', 'decided_by')[:20],
        'live': Promotion.objects.filter(ends_at__gt=now).select_related('story', 'created_by').order_by('ends_at'),
        'signed': signed,
    })
