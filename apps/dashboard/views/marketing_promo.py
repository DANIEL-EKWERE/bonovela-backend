"""Bonovela Marketing: promotions shown in the app, broadcasts, promo
codes, campaign links and settings. Every change is audited."""

import json
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, F, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Story
from apps.marketing import segments, services
from apps.marketing.models import (Banner, Broadcast, CampaignLink, Integration, MarketingAudit, MarketingMember,
                                   MarketingSettings)
from apps.users.models import User
from apps.wallet.models import CodeRedemption, RedeemCode, Transaction

from ..forms import BannerForm, BroadcastForm, CampaignLinkForm, RedeemCodeForm
from .marketing import PLATFORM_INFO, marketing_area

# page → (placements, title, blurb, button)
KINDS = {
    'discovery': (['discover'], 'Discovery Page Management',
                  'Feature books at the top of the Discover tab. Set the order with the arrows; each card opens its '
                  'exact book.', 'New discovery placement'),
    'banners': (['free', 'drama', 'store', 'rewards'], 'Banner Promotions',
                'Banners on the Free tab, Drama tab, Top Up store and Rewards. Artwork: 750 × 300 px (wide).',
                'New banner'),
    'popups': (['popup'], 'Pop-up Promotions',
               'An in-app pop-up for a chosen reader segment, shown once per device under the frequency limits.',
               'New pop-up'),
    'opening': (['splash'], 'Opening Screen Promotions',
                'A full-screen cover with View More and Skip before the app opens. Tapping it opens the exact '
                'advertised book.', 'New opening screen'),
}
KIND_OF = {p: k for k, (ps, *_) in KINDS.items() for p in ps}
STATUSES = ['active', 'scheduled', 'draft', 'paused', 'expired']


def _counts(qs):
    rows = list(qs)
    status = [b.status for b in rows]
    impressions = sum(b.impressions for b in rows)
    clicks = sum(b.clicks for b in rows)
    return {'live': status.count('active'), 'scheduled': status.count('scheduled'), 'drafts': status.count('draft'),
            'total': len(rows), 'impressions': impressions, 'ctr': clicks * 100 / impressions if impressions else 0}


# ── Promotion Centre ─────────────────────────────────────────────────────────
@marketing_area
def promotions(request):
    banners = list(Banner.objects.select_related('story', 'drama').order_by('-created_at'))
    notes = list(Broadcast.objects.filter(kind='notification').select_related('story'))
    status = request.GET.get('status', '')
    cards = []
    for key, (placements, title, *_rest) in KINDS.items():
        rows = [b for b in banners if b.placement in placements]
        cards.append({'key': key, 'title': title.replace(' Management', '').replace(' Promotions', 's'),
                      'live': sum(b.status == 'active' for b in rows), 'total': len(rows)})
    cards.append({'key': 'notifications', 'title': 'Notifications', 'total': len(notes),
                  'live': sum(n.status == 'scheduled' for n in notes)})
    rows = [b for b in banners if not status or b.status == status]
    return render(request, 'dashboard/marketing/promotions.html', {
        'cards': cards, 'rows': rows, 'status': status, 'statuses': STATUSES, 'kind_of': KIND_OF,
        'notes': [n for n in notes if not status or {'sent': 'expired'}.get(n.status, n.status) == status],
        'audience': dict(segments.choices()),
    })


def _promo_page(request, kind, template='dashboard/marketing/promo_list.html', extra=None):
    placements, title, blurb, button = KINDS[kind]
    qs = Banner.objects.filter(placement__in=placements).select_related('story', 'drama')
    ctx = {'kind': kind, 'title': title, 'blurb': blurb, 'button': button, 'rows': qs, 's': _counts(qs),
           'audience': dict(segments.choices()), 'cfg': MarketingSettings.current()}
    ctx.update(extra or {})
    return render(request, template, ctx)


@marketing_area(perm='promote')
def discovery(request):
    if request.method == 'POST':
        b = get_object_or_404(Banner, pk=request.POST.get('id'), placement='discover')
        order = list(Banner.objects.filter(placement='discover').order_by('order', '-created_at'))
        i = order.index(b)
        j = i - 1 if request.POST.get('move') == 'up' else i + 1
        if 0 <= j < len(order):
            order[i], order[j] = order[j], order[i]
            for n, row in enumerate(order):
                Banner.objects.filter(pk=row.pk).update(order=n)
            services.audit(request.marketing_member, f'Moved "{b.title}" {request.POST.get("move")}', 'Promotion',
                           b.code)
        return redirect('dashboard:marketing_discovery')
    live = Banner.objects.live('discover').select_related('story', 'story__author', 'story__genre')
    return _promo_page(request, 'discovery', 'dashboard/marketing/discovery.html', {
        'rows': Banner.objects.filter(placement='discover').select_related('story').order_by('order', '-created_at'),
        'preview': live[:4],
    })


@marketing_area
def banners(request):
    return _promo_page(request, 'banners')


@marketing_area
def popups(request):
    return _promo_page(request, 'popups')


@marketing_area
def opening(request):
    return _promo_page(request, 'opening')


@marketing_area(perm='promote')
def banner_form(request, pk=None):
    banner = get_object_or_404(Banner, pk=pk) if pk else Banner(created_by=request.user)
    kind = KIND_OF.get(banner.placement) if pk else request.GET.get('kind', 'banners')
    if kind not in KINDS:
        kind = 'banners'
    placements = KINDS[kind][0]
    initial = {} if pk else {'starts_at': timezone.localtime().strftime('%Y-%m-%dT%H:%M'),
                             'placement': placements[0], 'audience': 'all'}
    form = BannerForm(request.POST or None, request.FILES or None, instance=banner, initial=initial,
                      placements=placements)
    if request.method == 'POST' and form.is_valid():
        created = not banner.pk
        form.save()
        services.audit(request.marketing_member, f'{"Created" if created else "Edited"} {KINDS[kind][1].lower()} '
                       f'"{banner.title}"', 'Promotion', banner.code)
        messages.success(request, f'"{banner.title}" saved.')
        return redirect(f'dashboard:marketing_{kind}')
    return render(request, 'dashboard/marketing/promo_form.html', {
        'banner': banner, 'form': form, 'palettes': range(8), 'kind': kind, 'kind_title': KINDS[kind][1],
    })


@marketing_area(perm='promote')
@require_POST
def banner_action(request, pk):
    banner = get_object_or_404(Banner, pk=pk)
    action = request.POST.get('action')
    back = request.POST.get('next') or reverse(f'dashboard:marketing_{KIND_OF.get(banner.placement, "banners")}')
    if action == 'toggle':
        banner.is_active = not banner.is_active
        banner.save(update_fields=['is_active'])
        msg = f'"{banner.title}" {"switched on" if banner.is_active else "paused"}.'
    elif action == 'publish':
        banner.is_draft, banner.is_active = False, True
        banner.save(update_fields=['is_draft', 'is_active'])
        msg = f'"{banner.title}" published.'
    elif action == 'end':
        banner.ends_at = timezone.now()
        banner.save(update_fields=['ends_at'])
        msg = f'"{banner.title}" ended.'
    elif action == 'delete':
        banner.delete()
        msg = 'Promotion deleted.'
    else:
        return redirect(back)
    services.audit(request.marketing_member, msg, 'Promotion', f'PRM-{pk:04d}')
    messages.success(request, msg)
    return redirect(back)


# ── Broadcasts & notifications ───────────────────────────────────────────────
def _reports(qs):
    """Delivery figures: opens are inbox reads; purchases are payments by
    recipients within 7 days of the send."""
    rows = list(qs.annotate(opens=Count('messages', filter=Q(messages__is_read=True))).prefetch_related('stories')[:50])
    for b in rows:
        if b.status == 'sent' and b.recipients:
            users = b.messages.values('user')
            b.purchases = Transaction.objects.filter(
                user__in=users, kind__in=segments.PAID_KINDS, status='succeeded',
                created_at__gte=b.sent_at, created_at__lt=b.sent_at + timedelta(days=7)).count()
            b.open_rate = b.opens * 100 / b.recipients
        else:
            b.purchases, b.open_rate = 0, None
    return rows


def _compose_ctx(request):
    sizes = {key: services.reach(key)[0].count() for key, _ in segments.choices()}
    sizes.update({key: services.audience(key).count() for key, _ in Broadcast.AUDIENCE_CHOICES})
    return {
        'sizes_json': json.dumps(sizes), 'picked': request.POST.getlist('stories'),
        'audience': dict(segments.choices()), 'cfg': MarketingSettings.current(),
        'open_form': request.method == 'POST',
        'push': Integration.objects.filter(key='push', connected_at__isnull=False).exists(),
        'email': Integration.objects.filter(key='email', connected_at__isnull=False).exists(),
    }


def _compose(request, kind):
    """Shared by Broadcast Centre and Notification Promotions."""
    member = request.marketing_member
    form = BroadcastForm(request.POST or None, request.FILES or None, initial={
        'channels': ['inapp', 'push'] if kind == 'notification' else ['inapp'], 'audience': 'all',
        'segment': request.GET.get('segment', '')})
    if request.method == 'POST' and request.POST.get('cancel'):
        services.require(member, 'broadcast')
        b = get_object_or_404(Broadcast, pk=request.POST['cancel'], status='scheduled')
        b.status = 'cancelled'
        b.save(update_fields=['status'])
        services.audit(member, f'Cancelled "{b.title}"', 'Broadcast', b.code)
        messages.success(request, f'"{b.title}" cancelled.')
        return None, form
    if request.method == 'POST':
        services.require(member, 'broadcast')
        if form.is_valid():
            b = form.save(commit=False)
            b.kind, b.sent_by = kind, request.user
            if not b.story and form.cleaned_data.get('stories'):
                b.story = form.cleaned_data['stories'][0]
            if b.scheduled_at:
                b.status, b.sent_at = 'scheduled', b.scheduled_at
            b.save()
            form.save_m2m()
            if b.status == 'scheduled':
                msg = f'"{b.title}" scheduled for {timezone.localtime(b.scheduled_at):%d %b, %H:%M}.'
            else:
                services.send_broadcast(request.user, title=b.title, body=b.body, audience_key=b.audience, broadcast=b)
                msg = f'Sent to {b.recipients} inboxes.'
            services.audit(member, f'{"Scheduled" if b.status == "scheduled" else "Sent"} "{b.title}"', 'Broadcast',
                           b.code)
            messages.success(request, msg)
            return None, form
    return form, form


@marketing_area
def broadcasts(request):
    done, form = _compose(request, 'broadcast')
    if done is None:
        return redirect('dashboard:marketing_broadcasts')
    return render(request, 'dashboard/marketing/broadcasts.html', {
        'form': form, **_compose_ctx(request),
        'rows': _reports(Broadcast.objects.filter(kind='broadcast').select_related('sent_by', 'story')),
    })


@marketing_area
def notifications(request):
    done, form = _compose(request, 'notification')
    if done is None:
        return redirect('dashboard:marketing_notifications')
    rows = _reports(Broadcast.objects.filter(kind='notification').select_related('story'))
    impressions = sum(r.recipients for r in rows)
    opens = sum(r.opens for r in rows)
    return render(request, 'dashboard/marketing/notifications.html', {
        'form': form, 'rows': rows, **_compose_ctx(request),
        's': {'live': sum(r.status == 'scheduled' for r in rows), 'sent': sum(r.status == 'sent' for r in rows),
              'impressions': impressions, 'ctr': opens * 100 / impressions if impressions else 0},
    })


# ── Promo codes ──────────────────────────────────────────────────────────────
@marketing_area(perm='codes')
def codes(request):
    member = request.marketing_member
    form = RedeemCodeForm(request.POST or None)
    if request.method == 'POST':
        if request.POST.get('action') == 'toggle':
            code = get_object_or_404(RedeemCode, pk=request.POST.get('id'))
            code.is_active = not code.is_active
            code.save(update_fields=['is_active'])
            services.audit(member, f'{"Enabled" if code.is_active else "Paused"} code {code.code}', 'Promo code')
            messages.success(request, f'{code.code} {"enabled" if code.is_active else "disabled"}.')
            return redirect('dashboard:marketing_codes')
        if form.is_valid():
            code = form.save(commit=False)
            days = form.cleaned_data.get('days')
            begin = code.starts_at or timezone.now()
            code.expires_at = begin + timedelta(days=days) if days else None
            code.save()
            services.audit(member, f'Created code {code.code}', 'Promo code')
            messages.success(request, f'Code {code.code} created.')
            return redirect('dashboard:marketing_codes')
    rows = list(RedeemCode.objects.annotate(n_uses=Count('redeemed_by')).order_by('-is_active', '-created_at'))
    chapter_price = settings.BONOVELA['CHAPTER_PRICE']
    total_cost = total_rev = 0
    for c in rows:
        # Cost: bonus coins at 1¢, chapter vouchers at the chapter price.
        c.cost = c.n_uses * c.bonus * (chapter_price if c.reward == 'vouchers' else 1)
        rev = 0
        for r in CodeRedemption.objects.filter(code=c):
            rev += Transaction.objects.filter(user_id=r.user_id, kind__in=segments.PAID_KINDS, status='succeeded',
                                              created_at__gte=r.created_at,
                                              created_at__lt=r.created_at + timedelta(days=30)) \
                .aggregate(t=Sum('price_cents'))['t'] or 0
        c.revenue = rev
        total_cost += c.cost
        total_rev += rev
    ok = sum(c.n_uses for c in rows)
    bad = sum(c.rejected for c in rows)
    return render(request, 'dashboard/marketing/codes.html', {
        'codes': rows, 'form': form, 'open_form': request.method == 'POST', 'audience': dict(segments.choices()),
        's': {'ok': ok, 'bad': bad, 'bad_pct': bad * 100 / (ok + bad) if ok + bad else 0, 'cost': total_cost,
              'revenue': total_rev, 'multiple': total_rev / total_cost if total_cost else 0},
    })


# ── Campaign links & the public book link ────────────────────────────────────
def _urls(request, link):
    base = request.build_absolute_uri(reverse('dashboard:book_link', args=[link.story.book_code]))
    q = {'src': link.source, 'cid': link.campaign, 'pv': link.preview, 'l': link.code}
    referrer = urlencode({'utm_source': link.source, 'utm_campaign': link.campaign, 'book_id': link.story.book_code,
                          'link': link.code})
    play = settings.BONOVELA['PLAY_STORE_URL']
    return {
        'universal': f'{base}?{urlencode({k: v for k, v in q.items() if v})}',
        'play': f'{play}{"&" if "?" in play else "?"}referrer={quote(referrer)}',
        'apple': settings.BONOVELA['APP_STORE_URL'],
    }


@marketing_area(perm='promote')
def links(request):
    form = CampaignLinkForm(request.POST or None, initial={'source': 'meta', 'preview': 1})
    if request.method == 'POST' and form.is_valid():
        link = form.save(commit=False)
        link.created_by = request.user
        link.save()
        services.audit(request.marketing_member, f'Created link for {link.story.book_code}', 'Link', link.code)
        messages.success(request, f'{link.code} saved.')
        return redirect(f"{reverse('dashboard:marketing_links')}?show={link.pk}")
    rows = list(CampaignLink.objects.select_related('story').annotate(n_installs=Count('readers')))
    shown = next((r for r in rows if str(r.pk) == request.GET.get('show')), rows[0] if rows else None)
    return render(request, 'dashboard/marketing/links.html', {
        'form': form, 'rows': rows, 'shown': shown, 'urls': _urls(request, shown) if shown else None,
        'all_urls': {r.pk: _urls(request, r)['universal'] for r in rows},
        'setup': [
            ('Book preview web pages', True),
            ('Store links keep the campaign and Book ID', True),
            ('Android App Links (assetlinks.json)', False),
            ('Google Play Install Referrer read by the app', False),
            ('iOS Universal Links (apple-app-site-association)', False),
            ('Deferred deep linking / MMP', Integration.objects.filter(key='mmp', connected_at__isnull=False).exists()),
            ('Tested on real Android and iOS devices', False),
        ],
    })


def _link_for(request):
    code = (request.GET.get('l') or '').upper()
    if code.startswith('LNK-') and code[4:].isdigit():
        return CampaignLink.objects.filter(pk=int(code[4:]) - 499).first()
    return None


def book_link(request, book_id):
    """Public: the page an ad opens. Cover, synopsis and the preview
    chapters, then the store (with the Book ID kept for the app)."""
    code = book_id.upper()
    if not (code.startswith('BV-') and code[3:].isdigit()):
        raise Http404
    story = get_object_or_404(Story.objects.visible_in_app().select_related('author', 'genre'),
                              pk=int(code[3:]) - 1000)
    link = _link_for(request)
    if link and link.story_id != story.pk:
        link = None
    preview = link.preview if link else (5 if request.GET.get('pv') == '5' else 1)
    chapters = list(story.published_chapters().order_by('number')[:preview])
    if link:
        CampaignLink.objects.filter(pk=link.pk).update(clicks=F('clicks') + 1,
                                                       previews=F('previews') + (1 if chapters else 0))
    q = {k: v for k, v in {'l': link.code if link else '', 'src': request.GET.get('src', ''),
                           'cid': request.GET.get('cid', '')}.items() if v}
    return render(request, 'dashboard/marketing/book_link.html', {
        'story': story, 'chapters': chapters, 'link': link,
        'android': f"{reverse('dashboard:book_link_store', args=[story.book_code])}?{urlencode({**q, 'os': 'android'})}",
        'ios': f"{reverse('dashboard:book_link_store', args=[story.book_code])}?{urlencode({**q, 'os': 'ios'})}",
    })


def book_link_store(request, book_id):
    link = _link_for(request)
    if link:
        CampaignLink.objects.filter(pk=link.pk).update(store_visits=F('store_visits') + 1)
    if request.GET.get('os') == 'ios':
        return redirect(settings.BONOVELA['APP_STORE_URL'])
    referrer = urlencode({'utm_source': request.GET.get('src') or (link.source if link else 'web'),
                          'utm_campaign': request.GET.get('cid') or (link.campaign if link else ''),
                          'book_id': book_id.upper(), 'link': link.code if link else ''})
    play = settings.BONOVELA['PLAY_STORE_URL']
    return redirect(f'{play}{"&" if "?" in play else "?"}referrer={quote(referrer)}')


# ── Settings ─────────────────────────────────────────────────────────────────
EVENTS = [
    ('ad_impression', 'Ad platforms (import / API)', 'ads'), ('ad_click', 'Ad platforms + campaign links', 'ads'),
    ('book_preview_open', 'Campaign link pages', 'live'), ('store_visit', 'Campaign link pages', 'live'),
    ('app_install', 'Ad platforms / MMP', 'ads'), ('registration_complete', 'Bonovela accounts', 'live'),
    ('app_open', 'The app (daily activity)', 'live'), ('chapter_read', 'The app (reading progress)', 'live'),
    ('book_open', 'The app (reading progress)', 'live'), ('coin_purchase', 'Store purchases', 'live'),
    ('subscription_start', 'Store purchases', 'live'), ('promo_code_redeem', 'Promo codes', 'live'),
    ('banner_impression', 'Banners, pop-ups and opening screens', 'live'),
    ('banner_click', 'Banners, pop-ups and opening screens', 'live'),
    ('notification_open', 'Inbox (push once connected)', 'live'),
    ('uninstall', 'MMP / push delivery signals', 'mmp'),
]
TABS = [('integrations', 'Integrations'), ('segments', 'Segment rules'), ('caps', 'Frequency caps'),
        ('roles', 'Roles'), ('events', 'Events'), ('audit', 'Audit log')]


@marketing_area
def settings_page(request):
    member = request.marketing_member
    tab = request.GET.get('tab', 'integrations')
    if tab not in dict(TABS):
        tab = 'integrations'
    cfg = MarketingSettings.current()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action in ('segments', 'caps'):
            services.require(member, 'settings')
            fields = (['paying_lapse_days', 'active_days', 'inactive_days', 'new_days', 'stopped_chapters']
                      if action == 'segments' else
                      ['popup_per_day', 'splash_per_day', 'push_per_week', 'quiet_start', 'quiet_end'])
            changes = []
            for f in fields:
                try:
                    v = int(request.POST.get(f, ''))
                except ValueError:
                    continue
                top = 23 if f.startswith('quiet') else 365
                v = max(0 if f.startswith('quiet') else 1, min(top, v))
                if getattr(cfg, f) != v:
                    changes.append(f'{cfg._meta.get_field(f).verbose_name} {getattr(cfg, f)} → {v}')
                    setattr(cfg, f, v)
            cfg.save()
            for c in changes:
                services.audit(member, c[:1].upper() + c[1:], 'Setting')
            messages.success(request, 'Saved.' if changes else 'Nothing changed.')
        elif action == 'request':
            services.require(member, 'settings')
            key = request.POST.get('key')
            if key in dict(Integration.KEY_CHOICES):
                conn, _ = Integration.objects.get_or_create(key=key)
                conn.account_id = request.POST.get('account_id', '').strip()[:80]
                conn.requested_by, conn.requested_at = request.user, timezone.now()
                conn.save()
                name = dict(Integration.KEY_CHOICES)[key]
                services.audit(member, f'Requested a {name} connection', 'Integration', conn.account_id)
                messages.success(request, f'{name}: account saved. It shows as connected once the first sync '
                                          'with authorised API keys succeeds.')
        elif action in ('add_member', 'set_role', 'remove_member'):
            services.require(member, 'team')
            if action == 'add_member':
                user = User.objects.filter(email__iexact=request.POST.get('email', '').strip()).first()
                role = request.POST.get('role')
                if not user:
                    messages.error(request, 'No Bonovela account with that email.')
                elif role in dict(MarketingMember.ROLE_CHOICES):
                    MarketingMember.objects.update_or_create(user=user, defaults={'role': role,
                                                                                  'added_by': request.user})
                    services.audit(member, f'Gave {user.email} the {dict(MarketingMember.ROLE_CHOICES)[role]} role',
                                   'Access')
                    messages.success(request, f'{user.email} added.')
            else:
                m = get_object_or_404(MarketingMember, pk=request.POST.get('id'))
                if m.user_id == request.user.pk:
                    messages.error(request, "You can't change your own access.")
                elif action == 'remove_member':
                    services.audit(member, f'Removed {m.user.email}', 'Access')
                    m.delete()
                    messages.success(request, 'Removed.')
                elif request.POST.get('role') in dict(MarketingMember.ROLE_CHOICES):
                    m.role = request.POST['role']
                    m.save(update_fields=['role'])
                    services.audit(member, f'Changed {m.user.email} to {m.get_role_display()}', 'Access')
                    messages.success(request, 'Role updated.')
        return redirect(f"{reverse('dashboard:marketing_settings')}?tab={tab}")

    conns = {i.key: i for i in Integration.objects.all()}
    integrations = [
        {'key': k, 'name': name, 'api': PLATFORM_INFO.get(k, (None, None, {
            'mmp': 'Provider pull / raw-data API', 'push': 'Firebase Cloud Messaging / APNs',
            'email': 'Transactional email provider'}.get(k)))[2], 'conn': conns.get(k)}
        for k, name in Integration.KEY_CHOICES
    ]
    return render(request, 'dashboard/marketing/settings.html', {
        'tab': tab, 'tabs': TABS, 'cfg': cfg, 'integrations': integrations, 'cards': segments.cards(cfg),
        'members': MarketingMember.objects.select_related('user'), 'roles': MarketingMember.ROLE_CHOICES,
        'permissions': [(label, roles) for label, roles in MarketingMember.PERMISSIONS.values()],
        'events': EVENTS, 'audit': MarketingAudit.objects.all()[:200], 'can': member.can,
        'can_settings': member.can('settings'), 'can_team': member.can('team'),
    })
