"""Platform admin dashboard: KPIs, users, books, revenue, reports, store."""

from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Chapter, Story
from apps.engagement.models import Report, Review
from apps.users.models import User
from apps.wallet.models import (AuthorEarning, CoinPackage, GiftItem, RedeemCode, Subscription,
                                SubscriptionPlan, Transaction)

PAID_KINDS = (Transaction.KIND_TOP_UP, Transaction.KIND_SUBSCRIPTION)


def admin_area(view):
    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_platform_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def _cents(qs):
    return qs.aggregate(t=Sum('price_cents'))['t'] or 0


@admin_area
def home(request):
    now = timezone.now()
    week, month = now - timedelta(days=7), now - timedelta(days=30)
    paid = Transaction.objects.filter(kind__in=PAID_KINDS)

    # Revenue per day for the last 14 days (simple bar chart).
    days = []
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).date()
        days.append({'label': d.strftime('%b %d'), 'cents': _cents(paid.filter(created_at__date=d))})
    peak = max((d['cents'] for d in days), default=0) or 1
    for d in days:
        d['pct'] = round(d['cents'] * 100 / peak)

    return render(request, 'dashboard/admin/home.html', {
        'users_total': User.objects.count(),
        'users_week': User.objects.filter(date_joined__gte=week).count(),
        'authors': User.objects.filter(role=User.ROLE_AUTHOR).count(),
        'books_live': Story.objects.visible_in_app().count(),
        'books_total': Story.objects.count(),
        'chapters_week': Chapter.objects.filter(status=Chapter.STATUS_PUBLISHED, published_at__gte=week).count(),
        'revenue_month': _cents(paid.filter(created_at__gte=month)),
        'revenue_total': _cents(paid),
        'subs_active': Subscription.objects.filter(ends_at__gt=now).values('user').distinct().count(),
        'open_reports': Report.objects.filter(is_resolved=False).count(),
        'days': days,
        'top_books': Story.objects.visible_in_app().order_by('-views')[:5],
        'recent_users': User.objects.order_by('-date_joined')[:6],
    })


@admin_area
def users(request):
    qs = User.objects.order_by('-date_joined')
    role = request.GET.get('role', '')
    if role:
        qs = qs.filter(role=role)
    if request.GET.get('suspended'):
        qs = qs.filter(is_suspended=True)
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(email__icontains=q) | Q(pen_name__icontains=q) | Q(first_name__icontains=q))
    qs = qs.annotate(n_stories=Count('stories', distinct=True)).select_related('wallet')
    return render(request, 'dashboard/admin/users.html', {
        'users': qs[:200], 'role': role, 'q': q, 'roles': User.ROLE_CHOICES,
        'suspended': bool(request.GET.get('suspended')),
    })


@admin_area
@require_POST
def user_action(request, pk):
    target = get_object_or_404(User, pk=pk)
    action = request.POST.get('action')
    if target == request.user and action in ('suspend', 'role'):
        messages.error(request, "You can't change your own account here.")
    elif action == 'role':
        new = request.POST.get('role')
        if new in dict(User.ROLE_CHOICES):
            target.role = new
            target.save(update_fields=['role'])
            messages.success(request, f'{target.display_name} is now {target.get_role_display()}.')
    elif action in ('suspend', 'unsuspend'):
        target.is_suspended = action == 'suspend'
        target.save(update_fields=['is_suspended'])
        messages.success(request, f'{target.display_name} {"suspended" if target.is_suspended else "reinstated"}.')
    return redirect(request.POST.get('next') or 'dashboard:admin_users')


@admin_area
def books(request):
    qs = Story.objects.select_related('author', 'genre').annotate(
        published=Count('chapters', filter=Q(chapters__status=Chapter.STATUS_PUBLISHED), distinct=True),
    ).order_by('-updated_at')
    f = request.GET.get('f', 'all')
    filters = {
        'live': Q(is_signed=True, is_removed=False) & ~Q(status=Story.STATUS_DRAFT),
        'unsigned': Q(is_signed=False),
        'removed': Q(is_removed=True),
        'drafts': Q(status=Story.STATUS_DRAFT),
    }
    if f in filters:
        qs = qs.filter(filters[f])
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(author__pen_name__icontains=q) | Q(author__email__icontains=q))
    return render(request, 'dashboard/admin/books.html', {'books': qs[:200], 'f': f, 'q': q})


@admin_area
@require_POST
def book_action(request, pk):
    story = get_object_or_404(Story, pk=pk)
    action = request.POST.get('action')
    if action in ('remove', 'restore'):
        story.is_removed = action == 'remove'
        story.save(update_fields=['is_removed', 'updated_at'])
        messages.success(request, f'"{story.display_title}" {"removed from" if story.is_removed else "restored to"} the app.')
    return redirect(request.POST.get('next') or 'dashboard:admin_books')


@admin_area
def money(request):
    now = timezone.now()
    month = now - timedelta(days=30)
    kind = request.GET.get('kind', '')
    tx = Transaction.objects.select_related('user', 'story').order_by('-created_at')
    if kind:
        tx = tx.filter(kind=kind)
    paid = Transaction.objects.filter(kind__in=PAID_KINDS)
    earnings = AuthorEarning.objects.all()
    top_authors = (earnings.values('author__pen_name', 'author__email')
                   .annotate(total=Sum('amount_cents')).order_by('-total')[:8])
    return render(request, 'dashboard/admin/money.html', {
        'tx': tx[:100], 'kind': kind, 'kinds': Transaction.KIND_CHOICES,
        'top_ups': _cents(paid.filter(kind=Transaction.KIND_TOP_UP)),
        'subs': _cents(paid.filter(kind=Transaction.KIND_SUBSCRIPTION)),
        'month': _cents(paid.filter(created_at__gte=month)),
        'owed': earnings.aggregate(t=Sum('amount_cents'))['t'] or 0,
        'coins_spent': -(Transaction.objects.filter(coins__lt=0).aggregate(t=Sum('coins'))['t'] or 0),
        'top_authors': top_authors,
    })


@admin_area
def reports(request):
    tab = request.GET.get('tab', 'open')
    qs = Report.objects.select_related('reporter', 'story', 'reported_user').order_by('-created_at')
    qs = qs.filter(is_resolved=tab != 'open')
    hidden = Review.objects.filter(is_hidden=True).select_related('user', 'story')[:20]
    return render(request, 'dashboard/admin/reports.html', {'reports': qs[:200], 'tab': tab, 'hidden': hidden})


@admin_area
@require_POST
def report_action(request, pk):
    report = get_object_or_404(Report, pk=pk)
    action = request.POST.get('action')
    if action == 'resolve':
        report.is_resolved = True
        report.save(update_fields=['is_resolved'])
        messages.success(request, 'Report resolved.')
    elif action == 'remove_book' and report.story:
        report.story.is_removed = True
        report.story.save(update_fields=['is_removed', 'updated_at'])
        report.is_resolved = True
        report.save(update_fields=['is_resolved'])
        messages.success(request, 'Book removed and report resolved.')
    elif action == 'suspend_user' and report.reported_user and report.reported_user != request.user:
        report.reported_user.is_suspended = True
        report.reported_user.save(update_fields=['is_suspended'])
        report.is_resolved = True
        report.save(update_fields=['is_resolved'])
        messages.success(request, 'User suspended and report resolved.')
    return redirect('dashboard:admin_reports')


@admin_area
def store(request):
    if request.method == 'POST':
        model = {'package': CoinPackage, 'plan': SubscriptionPlan, 'code': RedeemCode}.get(request.POST.get('kind'))
        if model:
            obj = get_object_or_404(model, pk=request.POST.get('id'))
            obj.is_active = not obj.is_active
            obj.save(update_fields=['is_active'])
            messages.success(request, f'{obj} {"enabled" if obj.is_active else "disabled"}.')
        return redirect('dashboard:admin_store')
    return render(request, 'dashboard/admin/store.html', {
        'packages': CoinPackage.objects.all(),
        'plans': SubscriptionPlan.objects.all(),
        'gifts': GiftItem.objects.all(),
        'codes': RedeemCode.objects.annotate(n_uses=Count('redeemed_by')),
    })
