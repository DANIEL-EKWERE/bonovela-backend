"""Marketing actions: team access, the audit log, broadcasts, promo code
redemption, reader activity and ad-data imports."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.engagement.models import InboxMessage
from apps.wallet import services as wallet
from apps.wallet.models import CodeRedemption, RedeemCode, Subscription

from . import segments
from .models import AdCampaign, AdStat, Broadcast, MarketingAudit, MarketingMember, ReaderActivity, ReaderProfile

User = get_user_model()


# ── Team & audit ─────────────────────────────────────────────────────────────
def member_for(user):
    """The user's marketing role. Platform admins and marketing-role users
    without a row yet are Marketing Managers (what they could do before
    roles existed)."""
    member = MarketingMember.objects.filter(user=user).first()
    if member:
        return member
    if user.is_platform_admin or user.role == User.ROLE_MARKETING:
        return MarketingMember.objects.create(user=user, role=MarketingMember.ROLE_MANAGER)
    return None


def require(member, permission):
    if not (member and member.can(permission)):
        raise PermissionDenied(f"Your role can't {MarketingMember.PERMISSIONS[permission][0].lower()}.")


def audit(member, action, category, reference=''):
    label = f'{member.user.display_name} ({member.get_role_display()})' if member else 'System (Automation)'
    return MarketingAudit.objects.create(actor=member.user if member else None, actor_label=label,
                                         action=action[:160], category=category, reference=reference)


# ── Audiences & broadcasts ───────────────────────────────────────────────────
def audience(key):
    """Users for a broadcast audience: the original audiences, or any reader
    segment / genre audience."""
    now = timezone.now()
    qs = User.objects.filter(is_active=True, is_suspended=False)
    if key == 'readers':
        return qs.filter(role=User.ROLE_READER)
    if key == 'authors':
        return qs.filter(role=User.ROLE_AUTHOR)
    if key == 'plus':
        ids = Subscription.objects.filter(starts_at__lte=now, ends_at__gt=now).values('user')
        return qs.filter(pk__in=ids)
    if key == 'lapsed':
        return qs.exclude(last_login__gte=now - timedelta(days=14))
    if key == 'new':
        return qs.filter(date_joined__gte=now - timedelta(days=7))
    if key and key != 'all':
        return segments.segment(key, base=segments.readers().filter(is_suspended=False))
    return qs


def reach(key, country='', consent_only=False):
    """(users to deliver to, how many were left out by consent)."""
    qs = audience(key)
    if country:
        qs = qs.filter(reader_profile__country__iexact=country)
    total = qs.count()
    if consent_only:
        qs = qs.exclude(reader_profile__marketing_consent=False)
    return qs, total - qs.count()


@transaction.atomic
def send_broadcast(sender, *, title, body, audience_key, story=None, bonus=0, broadcast=None) -> Broadcast:
    """Deliver now: every recipient gets an inbox message (push and email
    follow once connected). Pass [broadcast] to send a scheduled one."""
    if broadcast is None:
        broadcast = Broadcast.objects.create(title=title, body=body, audience=audience_key, story=story, bonus=bonus,
                                             sent_by=sender)
    key = broadcast.segment or broadcast.audience
    users, excluded = reach(key, broadcast.country, consent_only=broadcast.kind == 'notification')
    users = list(users)
    story = broadcast.story or broadcast.stories.first()
    InboxMessage.objects.bulk_create([
        InboxMessage(user=u, title=broadcast.title, body=broadcast.body, story=story, broadcast=broadcast)
        for u in users
    ])
    if broadcast.bonus:
        for u in users:
            wallet.grant_bonus(u, broadcast.bonus, f'Gift · {broadcast.title}', days=7)
    broadcast.recipients, broadcast.excluded = len(users), excluded
    broadcast.status, broadcast.sent_at = 'sent', timezone.now()
    broadcast.save(update_fields=['recipients', 'excluded', 'status', 'sent_at'])
    return broadcast


def send_due_broadcasts():
    """Scheduled broadcasts whose time has come (run by publish_scheduled)."""
    sent = 0
    for b in Broadcast.objects.filter(status='scheduled', scheduled_at__lte=timezone.now()):
        send_broadcast(None, title=b.title, body=b.body, audience_key=b.audience, broadcast=b)
        sent += 1
    return sent


# ── Promo codes ──────────────────────────────────────────────────────────────
def redeem(user, raw):
    """Redeem a code for [user]: (reward dict, None) or (None, (message, code))."""
    code = (raw or '').strip().upper()
    rc = RedeemCode.objects.filter(code=code).first()
    if not rc:
        return None, ('Invalid or expired code.', 'invalid')

    def reject(msg, why):
        RedeemCode.objects.filter(pk=rc.pk).update(rejected=F('rejected') + 1)
        return None, (msg, why)

    if not rc.is_usable:
        return reject('Invalid or expired code.', 'invalid')
    if rc.redeemed_by.filter(pk=user.pk).exists():
        return reject('Already redeemed.', 'used')
    if rc.segment and not segments.contains(rc.segment, user):
        return reject("This code isn't available on your account.", 'ineligible')
    if rc.country_list:
        country = getattr(getattr(user, 'reader_profile', None), 'country', '')
        if country.lower() not in {c.lower() for c in rc.country_list}:
            return reject("This code isn't available in your country.", 'ineligible')
    with transaction.atomic():
        rc.redeemed_by.add(user)
        CodeRedemption.objects.create(code=rc, user=user)
        if rc.reward == 'vouchers':
            wallet.grant_vouchers(user, rc.bonus, f'Code {rc.code}')
            return {'bonus': 0, 'vouchers': rc.bonus}, None
        wallet.grant_bonus(user, rc.bonus, f'Code {rc.code}', days=7)
    return {'bonus': rc.bonus, 'vouchers': 0}, None


# ── Readers ──────────────────────────────────────────────────────────────────
def record_activity(user):
    """Mark today as an active day for this reader (cheap, idempotent)."""
    if user and user.is_authenticated:
        ReaderActivity.objects.get_or_create(user=user, date=timezone.localdate())


def update_profile(user, data):
    """Store what the app reports: source and campaign (first touch only),
    platform, app version, country and consent."""
    from .models import SOURCE_CHOICES, CampaignLink
    profile, created = ReaderProfile.objects.get_or_create(user=user)
    sources = dict(SOURCE_CHOICES)
    if created or (profile.source == 'organic' and not profile.campaign):
        if data.get('source') in sources:
            profile.source = data['source']
        profile.campaign = str(data.get('campaign') or '')[:40]
        link = str(data.get('link') or '')
        if link.upper().startswith('LNK-') and link[4:].isdigit():
            profile.link = CampaignLink.objects.filter(pk=int(link[4:]) - 499).first()
            if profile.link:
                profile.source, profile.campaign = profile.link.source, profile.link.campaign
    if data.get('platform') in dict(ReaderProfile.PLATFORM_CHOICES):
        profile.platform = data['platform']
    if data.get('app_version'):
        profile.app_version = str(data['app_version'])[:20]
    country = str(data.get('country') or '').strip()
    if len(country) == 2:  # the app sends the device region (ISO code)
        import pycountry
        match = pycountry.countries.get(alpha_2=country.upper())
        country = getattr(match, 'common_name', None) or getattr(match, 'name', '') if match else ''
    if country:
        profile.country = country[:60]
    for field in ('marketing_consent', 'push_consent'):
        if field in data:
            setattr(profile, field, str(data[field]).lower() in ('1', 'true', 'yes'))
    profile.save()
    return profile


# ── Ad data import ───────────────────────────────────────────────────────────
IMPORT_COLUMNS = ['date', 'campaign_id', 'campaign_name', 'platform', 'channel', 'ad_set', 'ad', 'book_id',
                  'budget', 'spend', 'impressions', 'reach', 'clicks', 'installs', 'registrations', 'book_opens',
                  'paying', 'revenue', 'status']


def _cents(value):
    try:
        return int((Decimal(str(value or '0').replace('$', '').replace(',', '')) * 100).quantize(Decimal(1)))
    except InvalidOperation:
        raise ValueError(f'"{value}" is not an amount')


def _int(value):
    try:
        return int(str(value or '0').replace(',', '') or 0)
    except ValueError:
        raise ValueError(f'"{value}" is not a whole number')


def import_ad_csv(fileobj, platform=None):
    """Import a daily ad report (columns: IMPORT_COLUMNS; only date and
    campaign_id are required). Re-importing a day replaces it. Returns
    (rows imported, [errors])."""
    from apps.catalog.models import Story
    platforms = {'meta', 'google', 'tiktok', 'apple'}
    text = fileobj.read()
    if isinstance(text, bytes):
        text = text.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [(f or '').strip().lower().replace(' ', '_') for f in reader.fieldnames or []]
    if not {'date', 'campaign_id'} <= set(reader.fieldnames):
        return 0, ['The file needs at least "date" and "campaign_id" columns.']
    done, errors = 0, []
    for n, row in enumerate(reader, start=2):
        try:
            with transaction.atomic():
                day = date.fromisoformat((row.get('date') or '').strip())
                code = (row.get('campaign_id') or '').strip().upper()
                if not code:
                    raise ValueError('campaign_id is empty')
                plat = (row.get('platform') or platform or '').strip().lower()
                camp = AdCampaign.objects.filter(code=code).first()
                if not camp:
                    if plat not in platforms:
                        raise ValueError('platform must be meta, google, tiktok or apple')
                    camp = AdCampaign(code=code, platform=plat, name=row.get('campaign_name') or code)
                for field, col in (('name', 'campaign_name'), ('channel', 'channel'), ('ad_set', 'ad_set'),
                                   ('ad_name', 'ad')):
                    if row.get(col):
                        setattr(camp, field, row[col].strip()[:120])
                if row.get('budget'):
                    camp.budget_cents = _cents(row['budget'])
                if (row.get('status') or '').strip().lower() in dict(AdCampaign.STATUS_CHOICES):
                    camp.status = row['status'].strip().lower()
                book = (row.get('book_id') or '').strip().upper()
                if book:
                    if not (book.startswith('BV-') and book[3:].isdigit()):
                        raise ValueError(f'book_id "{book}" should look like BV-1042')
                    camp.story = Story.objects.filter(pk=int(book[3:]) - 1000).first()
                    if not camp.story:
                        raise ValueError(f'no book with ID {book}')
                camp.save()
                AdStat.objects.update_or_create(campaign=camp, date=day, defaults={
                    'spend_cents': _cents(row.get('spend')), 'revenue_cents': _cents(row.get('revenue')),
                    **{f: _int(row.get(f)) for f in ('impressions', 'reach', 'clicks', 'installs', 'registrations',
                                                     'book_opens', 'paying')},
                })
                done += 1
        except ValueError as e:
            errors.append(f'Row {n}: {e}')
    return done, errors[:20]
