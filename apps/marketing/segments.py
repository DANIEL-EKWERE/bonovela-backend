"""Reader segments A–H (rules in Marketing Settings) and genre audiences.
Everything here returns User querysets, so segments can be counted,
filtered and used as broadcast audiences."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.engagement.models import ReadingProgress
from apps.wallet.models import Transaction

from .models import MarketingSettings, ReaderActivity

User = get_user_model()

PAID_KINDS = (Transaction.KIND_TOP_UP, Transaction.KIND_SUBSCRIPTION)

# key → (letter, label, description template using the settings)
SEGMENTS = {
    'paying': ('A', 'Paying readers', '≥1 successful coin or subscription purchase'),
    'never': ('B', 'Never-paid readers', 'Registered, never completed a purchase'),
    'former': ('C', 'Former paying readers', 'Paid before, nothing in {paying_lapse_days}+ days'),
    'active': ('D', 'Active readers', 'Opened the app in the last {active_days} days'),
    'inactive': ('E', 'Inactive readers', 'Not seen for {inactive_days}+ days'),
    'new': ('F', 'Newly registered', 'Registered in the last {new_days} days'),
    'not_started': ('G', 'Not started reading', 'Registered but never opened a book'),
    'stopped': ('H', 'Stopped reading', 'Opened a book, read under {stopped_chapters} chapters'),
}


def readers():
    """Everyone who reads in the app (readers and authors, not staff)."""
    return User.objects.filter(role__in=[User.ROLE_READER, User.ROLE_AUTHOR], is_active=True)


def segment(key, base=None, cfg=None):
    cfg = cfg or MarketingSettings.current()
    qs = base if base is not None else readers()
    now = timezone.now()
    paid = Transaction.objects.filter(user=OuterRef('pk'), kind__in=PAID_KINDS, status='succeeded')
    if key == 'paying':
        return qs.filter(Exists(paid))
    if key == 'never':
        return qs.exclude(Exists(paid))
    if key == 'former':
        recent = paid.filter(created_at__gte=now - timedelta(days=cfg.paying_lapse_days))
        return qs.filter(Exists(paid)).exclude(Exists(recent))
    if key == 'active':
        return qs.filter(_active_q(cfg.active_days))
    if key == 'inactive':
        return qs.exclude(_active_q(cfg.inactive_days))
    if key == 'new':
        return qs.filter(date_joined__gte=now - timedelta(days=cfg.new_days))
    if key == 'not_started':
        return qs.exclude(Exists(ReadingProgress.objects.filter(user=OuterRef('pk'))))
    if key == 'stopped':
        progress = ReadingProgress.objects.filter(user=OuterRef('pk'))
        further = progress.filter(chapter_number__gte=cfg.stopped_chapters)
        return qs.filter(Exists(progress)).exclude(Exists(further))
    if key.startswith('g:'):
        return qs.filter(Exists(ReadingProgress.objects.filter(user=OuterRef('pk'), story__genre__slug=key[2:])))
    return qs


def _active_q(days):
    since = timezone.now() - timedelta(days=days)
    seen = ReaderActivity.objects.filter(user=OuterRef('pk'), date__gte=since.date())
    return Q(Exists(seen)) | Q(last_login__gte=since)


def contains(key, user, cfg=None):
    """Is this reader in the segment? 'all' and blank match everyone."""
    if not key or key == 'all':
        return True
    return segment(key, readers().filter(pk=user.pk), cfg).exists()


def cards(cfg=None):
    """The A–H summary cards: letter, key, label, count, description."""
    cfg = cfg or MarketingSettings.current()
    out = []
    for key, (letter, label, desc) in SEGMENTS.items():
        out.append({'key': key, 'letter': letter, 'label': label, 'n': segment(key, cfg=cfg).count(),
                    'desc': desc.format(**{f.name: getattr(cfg, f.name) for f in cfg._meta.fields})})
    return out


def choices():
    """Audience choices for promotions, broadcasts and codes."""
    from apps.catalog.models import Genre
    out = [('all', 'All eligible readers')]
    out += [(k, v[1]) for k, v in SEGMENTS.items()]
    out += [(f'g:{g.slug}', f'{g.name} readers') for g in Genre.objects.filter(is_active=True).order_by('name')]
    return out


def label(key):
    return dict(choices()).get(key or 'all', key)
