"""Figures for the marketing pages. Ad figures come from AdStat (imported
or synced); reader, reading and revenue figures come from Bonovela's own
data. Only successful purchases count as revenue (USD cents)."""

from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from apps.engagement.models import ReadingProgress
from apps.wallet.models import Transaction

from . import segments
from .models import PLATFORM_CHOICES, AdCampaign, AdStat, CampaignLink, Integration, ReaderActivity, ReaderProfile

PAID = segments.PAID_KINDS
AD_FIELDS = ('spend_cents', 'impressions', 'reach', 'clicks', 'installs', 'registrations', 'book_opens', 'paying',
             'revenue_cents')


def since(days):
    return timezone.localdate() - timedelta(days=days - 1)


def _div(a, b, scale=1):
    return a * scale / b if b else 0


def derive(row):
    """Add CTR, CPC, CPI, cost per registration / paying reader and ROAS."""
    s = row.get('spend_cents', 0)
    row['ctr'] = _div(row.get('clicks', 0), row.get('impressions', 0), 100)
    row['cpc'] = _div(s, row.get('clicks', 0))
    row['cpi'] = _div(s, row.get('installs', 0))
    row['cpr'] = _div(s, row.get('registrations', 0))
    row['cpp'] = _div(s, row.get('paying', 0))
    row['rpp'] = _div(row.get('revenue_cents', 0), row.get('paying', 0))
    row['roas'] = _div(row.get('revenue_cents', 0), s)
    return row


def _sum(qs):
    agg = qs.aggregate(**{f: Sum(f) for f in AD_FIELDS})
    return {k: v or 0 for k, v in agg.items()}


def ad_totals(start=None):
    qs = AdStat.objects.all() if start is None else AdStat.objects.filter(date__gte=start)
    return derive(_sum(qs))


def platforms(start=None):
    """One row per ad platform, with its connection."""
    conns = {i.key: i for i in Integration.objects.all()}
    rows = []
    for key, name in PLATFORM_CHOICES:
        qs = AdStat.objects.filter(campaign__platform=key)
        if start:
            qs = qs.filter(date__gte=start)
        row = derive(_sum(qs))
        row.update(key=key, name=name, connection=conns.get(key))
        rows.append(row)
    return rows


def campaigns(start=None, platform=None, story=None):
    qs = AdCampaign.objects.select_related('story', 'story__author', 'story__genre')
    if platform:
        qs = qs.filter(platform=platform)
    if story:
        qs = qs.filter(story=story)
    stat_q = Q(stats__date__gte=start) if start else Q()
    qs = qs.annotate(**{f: Sum(f'stats__{f}', filter=stat_q) for f in AD_FIELDS})
    rows = []
    for c in qs:
        row = derive({f: getattr(c, f) or 0 for f in AD_FIELDS})
        row['c'] = c
        rows.append(row)
    rows.sort(key=lambda r: -r['revenue_cents'])
    return rows


def daily(start, days, platform=None):
    """Per day: spend by platform, total installs and registrations."""
    qs = AdStat.objects.filter(date__gte=start)
    if platform:
        qs = qs.filter(campaign__platform=platform)
    by_day = defaultdict(lambda: defaultdict(int))
    for r in qs.values('date', 'campaign__platform').annotate(s=Sum('spend_cents'), rev=Sum('revenue_cents')):
        by_day[r['date']][r['campaign__platform']] += r['s'] or 0
        by_day[r['date']]['revenue'] += r['rev'] or 0
    regs = dict(segments.readers().filter(date_joined__date__gte=start).values_list('date_joined__date')
                .annotate(n=Count('pk')))
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        spend = {k: by_day[d][k] for k, _ in PLATFORM_CHOICES}
        out.append({'date': d, 'spend': spend, 'total': sum(spend.values()), 'revenue': by_day[d]['revenue'],
                    'registrations': regs.get(d, 0)})
    return out


def readers(start):
    cfg = segments.MarketingSettings.current()
    all_readers = segments.readers()
    paying = segments.segment('paying', cfg=cfg).count()
    total = all_readers.count()
    return {
        'registered': total,
        'new': all_readers.filter(date_joined__date__gte=start).count(),
        'active': segments.segment('active', cfg=cfg).count(),
        'paying': paying,
        'never': total - paying,
        'former': segments.segment('former', cfg=cfg).count(),
        'paying_pct': round(_div(paying, total, 100)),
        'active_days': cfg.active_days, 'lapse_days': cfg.paying_lapse_days,
    }


def revenue(start=None, users=None):
    qs = Transaction.objects.filter(kind__in=PAID)
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if users is not None:
        qs = qs.filter(user__in=users)
    return qs.aggregate(t=Sum('price_cents'))['t'] or 0


def revenue_by_source(users=None):
    """Verified revenue grouped by the readers' acquisition source."""
    qs = Transaction.objects.filter(kind__in=PAID)
    if users is not None:
        qs = qs.filter(user__in=users)
    rows = defaultdict(int)
    for r in qs.values('user__reader_profile__source').annotate(t=Sum('price_cents')):
        rows[r['user__reader_profile__source'] or 'organic'] += r['t'] or 0
    return sorted(rows.items(), key=lambda kv: -kv[1])


def books(start=None, ids=None):
    """The reader journey per advertised (or linked) book."""
    from apps.catalog.models import Story
    ad = {r['story']: r for r in AdStat.objects.filter(**({'date__gte': start} if start else {}))
          .values(story=F('campaign__story'))
          .annotate(**{f: Sum(f) for f in AD_FIELDS})}
    linked = set(CampaignLink.objects.values_list('story', flat=True))
    story_ids = set(k for k in ad if k) | linked
    if ids:
        story_ids &= set(ids)
    sources = defaultdict(set)
    for sid, plat in AdCampaign.objects.filter(story__in=story_ids).values_list('story', 'platform'):
        sources[sid].add(dict(PLATFORM_CHOICES)[plat].replace(' Ads', ''))
    links = {r['story']: r for r in CampaignLink.objects.filter(story__in=story_ids).values('story')
             .annotate(clicks=Sum('clicks'), previews=Sum('previews'), installs=Count('readers'))}
    progress = ReadingProgress.objects.filter(story__in=story_ids)
    ch1 = dict(progress.values_list('story').annotate(n=Count('pk')))
    ch5 = dict(progress.filter(chapter_number__gte=5).values_list('story').annotate(n=Count('pk')))
    coins = dict(Transaction.objects.filter(story__in=story_ids, kind=Transaction.KIND_UNLOCK)
                 .values_list('story').annotate(t=Sum('coins')))
    payers = dict(Transaction.objects.filter(story__in=story_ids, kind=Transaction.KIND_UNLOCK, coins__lt=0)
                  .values_list('story').annotate(n=Count('user', distinct=True)))
    rows = []
    for s in Story.objects.filter(pk__in=story_ids).select_related('author', 'genre'):
        a, l = ad.get(s.pk, {}), links.get(s.pk, {})
        row = {f: a.get(f) or 0 for f in AD_FIELDS}
        row.update(story=s, sources=', '.join(sorted(sources[s.pk])) or 'Links only',
                   clicks=row['clicks'] + (l.get('clicks') or 0), previews=l.get('previews') or 0,
                   ch1=ch1.get(s.pk, 0), ch5=ch5.get(s.pk, 0), installs=row['installs'] + (l.get('installs') or 0),
                   readers_paying=payers.get(s.pk, 0))
        # Ad revenue (attributed) plus coins spent unlocking this book (1¢ each).
        row['revenue_cents'] = row['revenue_cents'] + abs(coins.get(s.pk) or 0)
        row['paying'] = max(row['paying'], row['readers_paying'])
        row['conv'] = _div(row['paying'], row['clicks'], 100)
        rows.append(row)
    rows.sort(key=lambda r: -r['revenue_cents'])
    return rows


FUNNEL = [
    ('Ad impression', 'ad_impression', 'ads'), ('Ad click', 'ad_click', 'ads'),
    ('Book preview visit', 'book_preview_open', 'links'), ('Chapter 1 read', 'chapter_1_read', 'app'),
    ('Chapter 5 read', 'chapter_5_read', 'app'), ('Play Store / App Store visit', 'store_visit', 'links'),
    ('App installation', 'app_install', 'ads'), ('Registration / login', 'registration_complete', 'app'),
    ('Advertised book opened', 'book_open', 'app'), ('Coin purchase / subscription', 'coin_purchase', 'app'),
    ('Paying reader', 'paying_reader', 'app'),
]


def funnel(start):
    ads = ad_totals(start)
    links = CampaignLink.objects.aggregate(c=Sum('clicks'), p=Sum('previews'), s=Sum('store_visits'))
    new = segments.readers().filter(date_joined__date__gte=start)
    progress = ReadingProgress.objects.filter(user__in=new)
    paid = Transaction.objects.filter(kind__in=PAID, created_at__date__gte=start)
    advertised = AdCampaign.objects.exclude(story=None).values('story')
    values = {
        'ad_impression': ads['impressions'], 'ad_click': ads['clicks'] + (links['c'] or 0),
        'book_preview_open': links['p'] or 0,
        'chapter_1_read': progress.values('user').distinct().count(),
        'chapter_5_read': progress.filter(chapter_number__gte=5).values('user').distinct().count(),
        'store_visit': links['s'] or 0,
        'app_install': max(ads['installs'], ReaderProfile.objects.filter(user__in=new).count()),
        'registration_complete': new.count(),
        'book_open': progress.filter(story__in=advertised).values('user').distinct().count(),
        'coin_purchase': paid.count(),
        'paying_reader': paid.values('user').distinct().count(),
    }
    top = values['ad_impression'] or None
    rows, prev = [], None
    for label, event, source in FUNNEL:
        v = values[event]
        rows.append({'label': label, 'event': event, 'source': source, 'n': v,
                     'step': _div(v, prev, 100) if prev is not None else None,
                     'of_top': _div(v, top, 100) if top else None})
        prev = v
    return rows


def activity(days=30):
    """Daily active readers for the last [days] days, plus DAU/WAU/MAU."""
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    per_day = dict(ReaderActivity.objects.filter(date__gte=start).values_list('date').annotate(n=Count('user')))
    series = [{'date': start + timedelta(days=i), 'n': per_day.get(start + timedelta(days=i), 0)} for i in range(days)]

    def distinct(n):
        return ReaderActivity.objects.filter(date__gt=today - timedelta(days=n)).values('user').distinct().count()

    return series, {'dau': distinct(1), 'wau': distinct(7), 'mau': distinct(30)}


def retention_by_source():
    """Share of readers active again on day 1/7/14/30 after joining."""
    out = []
    joined = {u: (d.date() if hasattr(d, 'date') else d) for u, d in segments.readers().values_list('pk', 'date_joined')}
    src = dict(ReaderProfile.objects.values_list('user', 'source'))
    seen = defaultdict(set)
    for u, d in ReaderActivity.objects.values_list('user', 'date'):
        if u in joined:
            seen[u].add((d - joined[u]).days)
    groups = defaultdict(list)
    for u in joined:
        groups[src.get(u, 'organic')].append(u)
    today = timezone.localdate()
    for source, users in sorted(groups.items()):
        row = {'source': source, 'readers': len(users)}
        for day in (1, 7, 14, 30):
            eligible = [u for u in users if (today - joined[u]).days >= day]
            back = [u for u in eligible if any(x >= day for x in seen[u])]
            row[f'd{day}'] = round(_div(len(back), len(eligible), 100)) if eligible else None
        out.append(row)
    return out


def cohorts(weeks=8):
    """Weekly install cohorts: share still active in later weeks."""
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    first = monday - timedelta(weeks=weeks - 1)
    users = segments.readers().filter(date_joined__date__gte=first).values_list('pk', 'date_joined')
    cohort = {}
    for u, d in users:
        d = timezone.localtime(d).date()
        cohort[u] = (d - first).days // 7
    weeks_seen = defaultdict(set)
    for u, d in ReaderActivity.objects.filter(user__in=list(cohort), date__gte=first).values_list('user', 'date'):
        weeks_seen[u].add((d - first).days // 7)
    rows = []
    for w in range(weeks):
        members = [u for u, c in cohort.items() if c == w]
        cells = []
        for k in range(weeks - w):
            n = sum(1 for u in members if (w + k) in weeks_seen[u]) if k else len(members)
            cells.append(round(_div(n, len(members), 100)) if members else None)
        rows.append({'week': first + timedelta(weeks=w), 'readers': len(members), 'cells': cells})
    return rows
