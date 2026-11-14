"""Bonovela Marketing: acquisition, readers, books and insights. Ad
figures come from imported (or, once connected, synced) platform data;
everything about readers, reading and revenue is Bonovela's own."""

import csv
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Exists, Max, OuterRef, Q, Subquery, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.engagement.models import ReadingProgress
from apps.marketing import analytics, segments, services
from apps.marketing.models import PLATFORM_CHOICES, AdCampaign, Integration, MarketingMember
from apps.wallet.models import Subscription, Transaction

RANGES = [(7, 'Last 7 days'), (30, 'Last 30 days'), (90, 'Last 90 days')]
PLATFORM_INFO = {
    'meta': ('Meta Ads', 'Covers Facebook and Instagram placements.', 'Marketing API + Conversions API'),
    'google': ('Google Ads', 'Search, Display, YouTube and App campaigns.', 'Google Ads API'),
    'tiktok': ('TikTok Ads', 'In-Feed and Spark Ads.', 'TikTok Marketing API + Events API'),
    'apple': ('Apple Ads', 'App Store search results, Search tab and Today tab.',
              'Apple Search Ads Campaign Management API + AdServices'),
}


def marketing_area(view=None, *, perm=None):
    """People on the marketing team (Marketing Settings → Roles) get in;
    platform admins and marketing-role users are Marketing Managers. Sets
    request.marketing_member."""

    def decorate(fn):
        @login_required
        @wraps(fn)
        def wrapper(request, *args, **kwargs):
            member = services.member_for(request.user)
            if not member:
                raise PermissionDenied
            if perm and request.method == 'POST':
                services.require(member, perm)
            MarketingMember.objects.filter(pk=member.pk).update(last_active=timezone.now())
            request.marketing_member = member
            request.ads_connected = Integration.objects.filter(connected_at__isnull=False).exists()
            return fn(request, *args, **kwargs)

        return wrapper

    return decorate(view) if view else decorate


def _days(request):
    try:
        days = int(request.GET.get('days', 30))
    except ValueError:
        days = 30
    return days if days in dict(RANGES) else 30


def _range_ctx(request):
    days = _days(request)
    return {'days': days, 'ranges': RANGES, 'range_label': dict(RANGES)[days], 'start': analytics.since(days)}


def _stack(series):
    """Columns for a stacked spend chart (heights in % of the tallest day)."""
    peak = max((d['total'] for d in series), default=0) or 1
    colors = {'meta': 'var(--f-chart-1)', 'google': 'var(--f-chart-2)', 'tiktok': 'var(--f-chart-3)',
              'apple': 'var(--f-chart-4)'}
    return [{'date': d['date'], 'total': d['total'],
             'parts': [{'h': d['spend'][k] * 100 / peak, 'color': colors[k], 'k': k} for k, _ in PLATFORM_CHOICES]}
            for d in series]


def _cols(values):
    peak = max(values, default=0) or 1
    return [v * 100 / peak for v in values]


def _csv(name, header, rows):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{name}"'
    w = csv.writer(resp)
    w.writerow(header)
    w.writerows(rows)
    return resp


# ── Overview & advertising ───────────────────────────────────────────────────
@marketing_area
def home(request):
    ctx = _range_ctx(request)
    start, days = ctx['start'], ctx['days']
    series = analytics.daily(start, days)
    ads = analytics.ad_totals(start)
    reader = analytics.readers(start)
    plats = analytics.platforms(start)
    peak_inst = max([p['installs'] for p in plats] + [p['registrations'] for p in plats] + [1])
    ctx.update({
        'ads': ads, 'r': reader, 'platforms': plats, 'peak_inst': peak_inst,
        'spend': _stack(series), 'regs': list(zip(series, _cols([d['registrations'] for d in series]))),
        'campaigns': analytics.campaigns(start)[:6],
        'books': analytics.books(start)[:6],
        'revenue': analytics.revenue(start),
        'has_ads': ads['impressions'] or ads['spend_cents'],
        'donut': reader['paying_pct'],
        'charts': [('installs', 'Installs by platform'), ('registrations', 'Registrations by platform')],
    })
    return render(request, 'dashboard/marketing/overview.html', ctx)


@marketing_area
def advertising(request):
    ctx = _range_ctx(request)
    start = ctx['start']
    plats = analytics.platforms(start)
    total = sum(p['spend_cents'] for p in plats) or 1
    for p in plats:
        p['share'] = p['spend_cents'] * 100 / total
    peak_cpi = max([p['cpi'] for p in plats] + [1])
    peak_roas = max([p['roas'] for p in plats] + [0.01])
    ctx.update({'platforms': plats, 'campaigns': analytics.campaigns(), 'peak_cpi': peak_cpi,
                'peak_roas': peak_roas, 'totals': analytics.ad_totals(start)})
    return render(request, 'dashboard/marketing/advertising.html', ctx)


@marketing_area(perm='import')
def platform(request, platform):
    name, blurb, api = PLATFORM_INFO[platform]
    if request.method == 'POST' and request.FILES.get('file'):
        n, errors = services.import_ad_csv(request.FILES['file'], platform)
        services.audit(request.marketing_member, f'Imported {n} rows of {name} data', 'Import')
        if n:
            messages.success(request, f'Imported {n} day{"s" if n != 1 else ""} of campaign data.')
        for e in errors:
            messages.error(request, e)
        return redirect(request.path)
    ctx = _range_ctx(request)
    start, days = ctx['start'], ctx['days']
    series = analytics.daily(start, days, platform)
    peak = max([d['total'] for d in series] + [d['revenue'] for d in series] + [1])
    row = next(p for p in analytics.platforms(start) if p['key'] == platform)
    budget = AdCampaign.objects.filter(platform=platform).aggregate(t=Sum('budget_cents'))['t'] or 0
    ctx.update({
        'key': platform, 'name': name, 'blurb': blurb, 'api': api, 'p': row, 'budget': budget,
        'conn': Integration.objects.filter(key=platform).first(),
        'series': [{'date': d['date'], 's': d['total'] * 100 / peak, 'r': d['revenue'] * 100 / peak} for d in series],
        'campaigns': analytics.campaigns(platform=platform), 'columns': services.IMPORT_COLUMNS,
        'can_import': request.marketing_member.can('import'),
    })
    return render(request, 'dashboard/marketing/platform.html', ctx)


@marketing_area
def import_template(request):
    rows = [[timezone.localdate().isoformat(), 'META-2026-0011', 'Rainy Night Romance', 'meta', 'Facebook',
             'Romance · 18–34 F', 'Velvet Rain — Video 15s', 'BV-1042', '7550.00', '168.40', '35120', '17400',
             '880', '170', '120', '85', '12', '296.50', 'active']]
    return _csv('bonovela-ad-import-template.csv', services.IMPORT_COLUMNS, rows)


# ── Readers ──────────────────────────────────────────────────────────────────
def _sub(qs, field, fn):
    """A per-reader aggregate as a subquery (no join fan-out)."""
    return Subquery(qs.filter(user=OuterRef('pk')).order_by().values('user').annotate(v=fn(field)).values('v'))


def _reader_rows(qs):
    """Annotate readers with what the table shows."""
    from apps.marketing.models import ReaderActivity
    now = timezone.now()
    return (qs.select_related('reader_profile')
            .annotate(spent=_sub(Transaction.objects.filter(kind__in=segments.PAID_KINDS), 'price_cents', Sum),
                      books=_sub(ReadingProgress.objects.all(), 'pk', Count),
                      chapters=_sub(ReadingProgress.objects.all(), 'chapter_number', Sum),
                      seen=_sub(ReaderActivity.objects.all(), 'date', Max),
                      subscribed=Exists(Subscription.objects.filter(user=OuterRef('pk'), ends_at__gt=now)))
            .order_by('-date_joined'))


def _readers_page(request, *, template, base_key=None, tabs=None, extra=None):
    cfg = segments.MarketingSettings.current()
    seg = request.GET.get('segment', '')
    if tabs and seg not in dict(tabs):
        seg = tabs[0][0]
    qs = segments.segment(seg, cfg=cfg) if seg else (segments.segment(base_key, cfg=cfg) if base_key else segments.readers())
    q = request.GET.get('q', '').strip()
    if q:
        code = q.upper().removeprefix('RD-')
        qs = qs.filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(pen_name__icontains=q) |
                       (Q(pk=int(code) - 100000) if code.isdigit() else Q()))
    source = request.GET.get('source', '')
    if source:
        qs = qs.filter(reader_profile__source=source) if source != 'organic' else \
            qs.filter(Q(reader_profile__isnull=True) | Q(reader_profile__source='organic'))
    page = Paginator(_reader_rows(qs), 15).get_page(request.GET.get('page'))
    ctx = {'cards': segments.cards(cfg), 'page': page, 'seg': seg, 'q': q, 'source': source, 'tabs': tabs,
           'sources': ['meta', 'google', 'tiktok', 'apple', 'organic', 'referral'], 'cfg': cfg,
           'total': page.paginator.count}
    ctx.update(extra or {})
    return render(request, template, ctx)


@marketing_area
def readers(request):
    if request.GET.get('reader'):
        return reader_profile(request, request.GET['reader'])
    return _readers_page(request, template='dashboard/marketing/readers.html')


@marketing_area
def paying(request):
    cfg = segments.MarketingSettings.current()
    payers = segments.segment('paying', cfg=cfg)
    paid = Transaction.objects.filter(kind__in=segments.PAID_KINDS, user__in=payers)
    total = segments.readers().count()
    revenue = analytics.revenue(users=payers)
    by_source = analytics.revenue_by_source(payers)
    peak = max([v for _, v in by_source] + [1])
    n = payers.count()
    extra = {
        'n': n, 'share': n * 100 / total if total else 0, 'revenue': revenue, 'arppu': revenue / n if n else 0,
        'subscribers': Subscription.objects.filter(ends_at__gt=timezone.now(), user__in=payers)
        .values('user').distinct().count(),
        'purchases': paid.count(),
        'by_source': [(k, v, v * 100 / peak) for k, v in by_source],
    }
    return _readers_page(request, template='dashboard/marketing/paying.html', base_key='paying', extra=extra)


@marketing_area
def nonpaying(request):
    tabs = [('never', 'Never paid'), ('former', 'Former paying')]
    cfg = segments.MarketingSettings.current()
    counts = {k: segments.segment(k, cfg=cfg).count() for k, _ in tabs}
    return _readers_page(request, template='dashboard/marketing/nonpaying.html',
                         tabs=[(k, f'{label} ({counts[k]})') for k, label in tabs])


def reader_profile(request, code):
    code = code.upper().removeprefix('RD-')
    qs = segments.readers().filter(pk=int(code) - 100000) if code.isdigit() else segments.readers().none()
    reader = _reader_rows(qs).first()
    if not reader:
        messages.error(request, 'No reader with that ID.')
        return redirect('dashboard:marketing_readers')
    cfg = segments.MarketingSettings.current()
    member_of = [c for c in segments.SEGMENTS if segments.contains(c, reader, cfg)]
    return render(request, 'dashboard/marketing/reader.html', {
        'r': reader, 'member_of': [(segments.SEGMENTS[k][0], segments.SEGMENTS[k][1]) for k in member_of],
        'purchases': Transaction.objects.filter(user=reader, kind__in=segments.PAID_KINDS)[:10],
        'progress': ReadingProgress.objects.filter(user=reader).select_related('story')[:10],
        'codes': reader.redeemed_codes.all()[:10],
    })


# ── Books ────────────────────────────────────────────────────────────────────
@marketing_area
def books(request):
    ctx = _range_ctx(request)
    rows = analytics.books()
    ids = [int(i) for i in request.GET.getlist('compare') if i.isdigit()][:3]
    compare = [r for r in rows if r['story'].pk in ids]
    stages = [('Impressions', 'impressions'), ('Clicks', 'clicks'), ('Previews', 'previews'), ('Chapter 1', 'ch1'),
              ('Chapter 5', 'ch5'), ('Installs', 'installs'), ('Registrations', 'registrations'),
              ('Paying', 'paying')]
    peak = {k: max([r[k] for r in compare] + [1]) for _, k in stages}
    ctx.update({'rows': rows, 'top': rows[:6], 'compare': compare, 'ids': ids, 'stages': stages, 'peak': peak})
    return render(request, 'dashboard/marketing/books.html', ctx)


# ── Insights ─────────────────────────────────────────────────────────────────
@marketing_area
def funnel(request):
    ctx = _range_ctx(request)
    rows = analytics.funnel(ctx['start'])
    peak = max([r['n'] for r in rows] + [1])
    for r in rows:
        r['w'] = r['n'] * 100 / peak
    ctx['rows'] = rows
    return render(request, 'dashboard/marketing/funnel.html', ctx)


@marketing_area
def retention(request):
    series, active = analytics.activity(30)
    start = analytics.since(30)
    peak = max([d['n'] for d in series] + [1])
    return render(request, 'dashboard/marketing/retention.html', {
        'series': [(d, d['n'] * 100 / peak) for d in series], 'active': active,
        'installs': segments.readers().filter(date_joined__date__gte=start).count(),
        'reengaged': segments.segment('active').filter(date_joined__date__lt=start - timedelta(days=14)).count(),
        'by_source': analytics.retention_by_source(), 'cohorts': analytics.cohorts(), 'day_cols': ['d1', 'd7', 'd14', 'd30'],
        'push': Integration.objects.filter(key__in=['mmp', 'push'], connected_at__isnull=False).exists(),
    })


@marketing_area
def reports(request):
    ctx = _range_ctx(request)
    start = ctx['start']
    by = request.GET.get('by', 'platform')
    if by == 'campaign':
        rows = [dict(r, name=f"{r['c'].name} · {r['c'].code}") for r in analytics.campaigns(start)]
    elif by == 'book':
        rows = [dict(r, name=f"{r['story'].display_title} · {r['story'].book_code}") for r in analytics.books(start)]
        rows = [analytics.derive(r) for r in rows]
    else:
        by, rows = 'platform', analytics.platforms(start)
    if request.GET.get('export'):
        if not request.marketing_member.can('export'):
            raise PermissionDenied("Your role can't export reports.")
        services.audit(request.marketing_member, f'Exported the {by} report', 'Report')
        return _csv(f'bonovela-roas-by-{by}.csv',
                    ['Name', 'Spend', 'Revenue', 'ROAS', 'CPC', 'Cost per install', 'Cost per registration',
                     'Cost per paying reader'],
                    [[r['name'], r['spend_cents'] / 100, r['revenue_cents'] / 100, round(r['roas'], 2),
                      round(r['cpc'] / 100, 2), round(r['cpi'] / 100, 2), round(r['cpr'] / 100, 2),
                      round(r['cpp'] / 100, 2)] for r in rows])
    ctx.update({'t': analytics.ad_totals(start), 'rows': rows, 'by': by,
                'by_tabs': [('platform', 'By platform'), ('campaign', 'By campaign'), ('book', 'By book')],
                'can_export': request.marketing_member.can('export')})
    return render(request, 'dashboard/marketing/reports.html', ctx)
