"""Marketing dashboard: growth KPIs, in-app banners, promo codes and
broadcast messages."""

from datetime import timedelta

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.marketing import services
from apps.marketing.models import Banner, Broadcast
from apps.users.models import User
from apps.wallet.models import RedeemCode, Transaction

from ..forms import BannerForm, BroadcastForm, RedeemCodeForm
from .access import role_area

marketing_area = role_area(User.ROLE_MARKETING)


def _bars(values):
    peak = max((v for _, v in values), default=0) or 1
    return [{'label': label, 'value': v, 'pct': round(v * 100 / peak)} for label, v in values]


@marketing_area
def home(request):
    now = timezone.now()
    week, month = now - timedelta(days=7), now - timedelta(days=30)
    users = User.objects.filter(role__in=[User.ROLE_READER, User.ROLE_AUTHOR])
    buyers = Transaction.objects.filter(kind=Transaction.KIND_TOP_UP).values('user').distinct().count()
    signups = []
    for i in range(13, -1, -1):
        d = (now - timedelta(days=i)).date()
        signups.append((d.strftime('%b %d'), users.filter(date_joined__date=d).count()))
    return render(request, 'dashboard/marketing/home.html', {
        'users': users.count(),
        'new_week': users.filter(date_joined__gte=week).count(),
        'new_month': users.filter(date_joined__gte=month).count(),
        'active_week': users.filter(last_login__gte=week).count(),
        'payer_rate': round(buyers * 100 / (users.count() or 1), 1),
        'live_banners': Banner.objects.live().count(),
        'code_uses': RedeemCode.redeemed_by.through.objects.count(),
        'broadcasts_month': Broadcast.objects.filter(sent_at__gte=month).count(),
        'signups': _bars(signups),
        'top_banners': sorted(Banner.objects.all(), key=lambda b: (-b.ctr, -b.clicks))[:5],
        'readers_by_type': users.values('reader_type').annotate(n=Count('id')).order_by('-n'),
    })


@marketing_area
def banners(request):
    placement = request.GET.get('placement', '')
    qs = Banner.objects.select_related('story', 'drama')
    if placement:
        qs = qs.filter(placement=placement)
    return render(request, 'dashboard/marketing/banners.html', {
        'banners': qs, 'placement': placement, 'placements': Banner.PLACEMENT_CHOICES,
    })


@marketing_area
def banner_form(request, pk=None):
    banner = get_object_or_404(Banner, pk=pk) if pk else Banner(created_by=request.user)
    initial = {} if pk else {'starts_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M')}
    form = BannerForm(request.POST or None, request.FILES or None, instance=banner, initial=initial)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Banner "{banner.title}" saved.')
        return redirect('dashboard:marketing_banners')
    return render(request, 'dashboard/marketing/banner_form.html', {'banner': banner, 'form': form, 'palettes': range(8)})


@marketing_area
@require_POST
def banner_action(request, pk):
    banner = get_object_or_404(Banner, pk=pk)
    action = request.POST.get('action')
    if action == 'toggle':
        banner.is_active = not banner.is_active
        banner.save(update_fields=['is_active'])
        messages.success(request, f'"{banner.title}" {"switched on" if banner.is_active else "paused"}.')
    elif action == 'delete':
        banner.delete()
        messages.success(request, 'Banner deleted.')
    return redirect('dashboard:marketing_banners')


@marketing_area
def codes(request):
    form = RedeemCodeForm(request.POST or None)
    if request.method == 'POST':
        if request.POST.get('action') == 'toggle':
            code = get_object_or_404(RedeemCode, pk=request.POST.get('id'))
            code.is_active = not code.is_active
            code.save(update_fields=['is_active'])
            messages.success(request, f'{code.code} {"enabled" if code.is_active else "disabled"}.')
            return redirect('dashboard:marketing_codes')
        if form.is_valid():
            code = form.save(commit=False)
            days = form.cleaned_data.get('days')
            code.expires_at = timezone.now() + timedelta(days=days) if days else None
            code.save()
            messages.success(request, f'Code {code.code} created.')
            return redirect('dashboard:marketing_codes')
    return render(request, 'dashboard/marketing/codes.html', {
        'codes': RedeemCode.objects.annotate(n_uses=Count('redeemed_by')).order_by('-is_active', '-created_at'),
        'form': form, 'now': timezone.now(), 'open_form': request.method == 'POST',
    })


@marketing_area
def broadcasts(request):
    form = BroadcastForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        b = services.send_broadcast(request.user, title=d['title'], body=d['body'], audience_key=d['audience'],
                                    story=d.get('story'), bonus=d.get('bonus') or 0)
        messages.success(request, f'Sent to {b.recipients} inboxes.')
        return redirect('dashboard:marketing_broadcasts')
    sizes = {key: services.audience(key).count() for key, _ in Broadcast.AUDIENCE_CHOICES}
    return render(request, 'dashboard/marketing/broadcasts.html', {
        'broadcasts': Broadcast.objects.select_related('sent_by', 'story')[:50], 'form': form, 'sizes': sizes,
        'audiences': Broadcast.AUDIENCE_CHOICES, 'open_form': request.method == 'POST',
    })
