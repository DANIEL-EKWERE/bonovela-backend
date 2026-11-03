"""Editorial dashboard: AE / SE / CE review, signing and promotions."""

from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Chapter, Story
from apps.editorial import services
from apps.editorial.models import ContractApplication as App
from apps.editorial.models import EditorialNote, Promotion, PromotionRequest
from apps.users.models import User

# Which application statuses each role acts on.
ACTIONABLE = {
    User.ROLE_AE: [App.STATUS_PENDING],
    User.ROLE_SE: [App.STATUS_AE_APPROVED],
    User.ROLE_CE: [App.STATUS_SE_APPROVED],
    User.ROLE_ADMIN: [App.STATUS_PENDING, App.STATUS_AE_APPROVED, App.STATUS_SE_APPROVED],
}


def editor_area(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_editor or request.user.is_platform_admin):
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def _role(user):
    return User.ROLE_ADMIN if user.is_platform_admin and not user.is_editor else user.role


def _run(request, fn, *args, ok='Done.', **kwargs):
    try:
        fn(*args, **kwargs)
        messages.success(request, ok)
        return True
    except (PermissionDenied, ValidationError) as e:
        messages.error(request, e.messages[0] if hasattr(e, 'messages') else str(e) or 'Not allowed.')
        return False


@editor_area
def home(request):
    role = _role(request.user)
    now = timezone.now()
    counts = {s: c for s, c in App.objects.values_list('status').annotate(n=Count('id'))}
    return render(request, 'dashboard/editorial/home.html', {
        'role': role,
        'mine': App.objects.filter(status__in=ACTIONABLE.get(role, [])).select_related('story__author')[:8],
        'counts': counts,
        'pending_requests': PromotionRequest.objects.filter(status=PromotionRequest.STATUS_PENDING).count(),
        'live_promotions': Promotion.objects.filter(starts_at__lte=now, ends_at__gt=now).count(),
        'signed_week': App.objects.filter(status=App.STATUS_SIGNED, updated_at__gte=now - timedelta(days=7)).count(),
        'unsigned_books': Story.objects.filter(is_signed=False).exclude(status=Story.STATUS_DRAFT).count(),
    })


@editor_area
def queue(request):
    role = _role(request.user)
    tab = request.GET.get('tab', 'mine')
    qs = App.objects.select_related('story__author', 'story__genre', 'ae', 'se', 'ce')
    if tab == 'mine':
        qs = qs.filter(status__in=ACTIONABLE.get(role, []))
    elif tab == 'open':
        qs = qs.filter(status__in=App.OPEN_STATUSES)
    else:
        qs = qs.exclude(status__in=App.OPEN_STATUSES)
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(story__title__icontains=q) | Q(story__author__pen_name__icontains=q))
    return render(request, 'dashboard/editorial/queue.html', {'apps': qs[:200], 'tab': tab, 'q': request.GET.get('q', '')})


@editor_area
def application(request, pk):
    app = get_object_or_404(App.objects.select_related('story__author', 'story__genre'), pk=pk)
    story = app.story
    role = _role(request.user)
    if request.method == 'POST':
        action = request.POST.get('action')
        feedback = request.POST.get('feedback', '').strip()
        if action == 'note':
            if feedback:
                EditorialNote.objects.create(story=story, editor=request.user, text=feedback)
                messages.success(request, 'Note added.')
        elif action in ('ae_approve', 'ae_revise'):
            _run(request, services.ae_review, app, request.user, approve=action == 'ae_approve', feedback=feedback,
                 ok='Sent to the Senior Editor.' if action == 'ae_approve' else 'Sent back to the author.')
        elif action in ('se_approve', 'se_revise', 'se_reject'):
            _run(request, services.se_decide, app, request.user, action.split('_')[1], feedback=feedback,
                 ok={'se_approve': 'Approved — the CE will send the contract.', 'se_revise': 'Sent back to the author.',
                     'se_reject': 'Rejected.'}[action])
        elif action == 'ce_contract':
            try:
                bonus = int(float(request.POST.get('signing_bonus') or 0) * 100)
            except ValueError:
                bonus = 0
            _run(request, services.ce_send_contract, app, request.user,
                 contract_type=request.POST.get('contract_type', 'non_exclusive'), signing_bonus_cents=bonus,
                 ok='Contract sent to the author.')
        return redirect('dashboard:editorial_application', app.pk)

    chapters = story.chapters.filter(status=Chapter.STATUS_PUBLISHED).order_by('number')
    reading = None
    if request.GET.get('chapter'):
        reading = chapters.filter(number=request.GET['chapter']).first()
    return render(request, 'dashboard/editorial/application.html', {
        'app': app, 'story': story, 'role': role, 'chapters': chapters, 'reading': reading or chapters.first(),
        'notes': story.editorial_notes.select_related('editor')[:30],
        'can_act': app.status in ACTIONABLE.get(role, []) or (role == User.ROLE_CE and app.status in ACTIONABLE[User.ROLE_SE] + ACTIONABLE[User.ROLE_AE]),
    })


@editor_area
def promotions(request):
    role = _role(request.user)
    now = timezone.now()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'request':
            story = get_object_or_404(Story, pk=request.POST.get('story'))
            _run(request, services.request_promotion, request.user, story, request.POST.getlist('sections'),
                 int(request.POST.get('days') or 7), request.POST.get('reason', '').strip() or '—',
                 ok='Request sent to the Chief Editor.')
        elif action in ('approve', 'reject'):
            req = get_object_or_404(PromotionRequest, pk=request.POST.get('request'))
            _run(request, services.decide_promotion, request.user, req, approve=action == 'approve',
                 note=request.POST.get('note', ''), sections=request.POST.getlist('sections') or None,
                 ok='Promotion is live.' if action == 'approve' else 'Request rejected.')
        elif action == 'promote':
            story = get_object_or_404(Story, pk=request.POST.get('story'))
            for section in request.POST.getlist('sections'):
                if not _run(request, services.promote, request.user, story, section, int(request.POST.get('days') or 7),
                            ok='Promotion is live.'):
                    break
        elif action == 'end':
            promo = get_object_or_404(Promotion, pk=request.POST.get('promotion'))
            _run(request, services.end_promotion, request.user, promo, ok='Promotion ended.')
        return redirect('dashboard:editorial_promotions')

    return render(request, 'dashboard/editorial/promotions.html', {
        'role': role,
        'sections': Promotion.SECTION_CHOICES,
        'pending': PromotionRequest.objects.filter(status=PromotionRequest.STATUS_PENDING).select_related('story', 'requested_by'),
        'recent': PromotionRequest.objects.exclude(status=PromotionRequest.STATUS_PENDING).select_related('story', 'requested_by', 'decided_by')[:20],
        'live': Promotion.objects.filter(ends_at__gt=now).select_related('story', 'created_by').order_by('ends_at'),
        'signed': Story.objects.filter(is_signed=True, is_removed=False).order_by('title'),
    })


@editor_area
def books(request):
    qs = Story.objects.select_related('author', 'genre').annotate(
        published=Count('chapters', filter=Q(chapters__status=Chapter.STATUS_PUBLISHED))
    ).order_by('-updated_at')
    f = request.GET.get('f', 'all')
    if f == 'signed':
        qs = qs.filter(is_signed=True)
    elif f == 'unsigned':
        qs = qs.filter(is_signed=False)
    elif f == 'removed':
        qs = qs.filter(is_removed=True)
    if request.GET.get('q'):
        q = request.GET['q']
        qs = qs.filter(Q(title__icontains=q) | Q(author__pen_name__icontains=q) | Q(author__email__icontains=q))
    return render(request, 'dashboard/editorial/books.html', {'books': qs[:200], 'f': f, 'q': request.GET.get('q', '')})


@editor_area
def book(request, pk):
    story = get_object_or_404(Story.objects.select_related('author', 'genre'), pk=pk)
    chapters = story.chapters.exclude(status=Chapter.STATUS_TRASH).order_by('number')
    reading = chapters.filter(number=request.GET.get('chapter') or 0).first() or chapters.filter(status=Chapter.STATUS_PUBLISHED).first()
    return render(request, 'dashboard/editorial/book.html', {
        'story': story, 'chapters': chapters, 'reading': reading, 'role': _role(request.user),
        'notes': story.editorial_notes.select_related('editor')[:30], 'app': story.applications.first(),
    })


@editor_area
@require_POST
def book_action(request, pk):
    story = get_object_or_404(Story, pk=pk)
    action = request.POST.get('action')
    role = _role(request.user)
    if action == 'note':
        text = request.POST.get('text', '').strip()
        if text:
            EditorialNote.objects.create(story=story, editor=request.user, text=text,
                                         chapter_id=request.POST.get('chapter') or None)
            messages.success(request, 'Note added.')
    elif action == 'revise_chapter':
        ch = get_object_or_404(Chapter, pk=request.POST.get('chapter'), story=story)
        ch.status = Chapter.STATUS_REVISION
        ch.save(update_fields=['status', 'updated_at'])
        from apps.engagement.models import InboxMessage
        InboxMessage.objects.create(user=story.author, story=story, title=f'Chapter {ch.number} needs changes',
                                    body=request.POST.get('text') or 'An editor sent this chapter back for revision.')
        messages.success(request, 'Chapter sent back to the author.')
    elif action in ('remove', 'restore'):
        if role not in (User.ROLE_CE, User.ROLE_ADMIN):
            messages.error(request, 'Only the Chief Editor can remove books.')
        else:
            story.is_removed = action == 'remove'
            story.save(update_fields=['is_removed', 'updated_at'])
            messages.success(request, 'Book removed from the app.' if story.is_removed else 'Book restored.')
    return redirect('dashboard:editorial_book', story.pk)
