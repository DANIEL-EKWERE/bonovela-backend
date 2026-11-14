"""Finance helpers shared by the dashboard: who may do what, and the audit log."""

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import AuditEntry, FinanceMember


def member_for(user):
    """The user's finance role. Platform admins are Management; finance-role
    users without a row yet are Finance Leads (what they could do before
    roles existed; settings stay with Management)."""
    member = FinanceMember.objects.filter(user=user).first()
    if member:
        return member
    if user.is_platform_admin:
        return FinanceMember.objects.create(user=user, role=FinanceMember.ROLE_MANAGEMENT)
    if user.role == 'finance':
        return FinanceMember.objects.create(user=user, role=FinanceMember.ROLE_LEAD)
    return None


def require(member, permission):
    if not (member and member.can(permission)):
        raise PermissionDenied(f"Your role can't {FinanceMember.PERMISSIONS[permission][0].lower()}.")


def actor_label(member):
    return f'{member.user.display_name} ({member.get_role_display()})' if member else 'System (Automation)'


def audit(member, action, category, reference='', detail=''):
    return AuditEntry.objects.create(
        actor=member.user if member else None, actor_label=actor_label(member), action=action,
        category=category, reference=reference, detail=detail[:200], created_at=timezone.now(),
    )


def money(cents):
    sign = '−' if cents < 0 else ''
    return f'{sign}${abs(cents) / 100:,.2f}'


def subscription_pool(month):
    """One month of Short Story Subscriptions: what subscribers paid (each
    plan spread over its days, so annual plans count about a twelfth a
    month), less fees and Bonovela's share, shared by chapters read.

    Returns the pool figures and writes each author's suggested share
    (decisions already made are kept)."""
    from datetime import datetime, time, timedelta

    from django.db.models import Count
    from django.utils import timezone

    from apps.wallet.models import Subscription

    from .models import FinanceSettings, SubscriberRead, SubscriptionShare

    tz = timezone.get_current_timezone()
    start = datetime.combine(month, time.min, tzinfo=tz)
    nxt = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    end = datetime.combine(nxt, time.min, tzinfo=tz)
    revenue, active = 0, 0
    for sub in Subscription.objects.filter(starts_at__lt=end, ends_at__gt=start).select_related('plan'):
        span = (sub.ends_at - sub.starts_at).total_seconds()
        overlap = (min(sub.ends_at, end) - max(sub.starts_at, start)).total_seconds()
        if span > 0 and overlap > 0:
            revenue += round(sub.plan.price_cents * overlap / span)
            active += 1
    fs = FinanceSettings.current()
    fees = round(revenue * fs.processing_fee_pct / 100)
    bonovela = round((revenue - fees) * (100 - fs.author_share_pct) / 100)
    pool = revenue - fees - bonovela

    reads = (SubscriberRead.objects.filter(month=month).values('author')
             .annotate(n=Count('pk'), subscribers=Count('user', distinct=True)).order_by('-n'))
    total_reads = sum(r['n'] for r in reads)
    for r in reads:
        suggested = round(pool * r['n'] / total_reads) if total_reads else 0
        share, created = SubscriptionShare.objects.get_or_create(
            month=month, author_id=r['author'], defaults={'reads': r['n'], 'suggested_cents': suggested})
        if not created and (share.reads, share.suggested_cents) != (r['n'], suggested):
            share.reads, share.suggested_cents = r['n'], suggested
            share.save(update_fields=['reads', 'suggested_cents'])
    return {'revenue': revenue, 'active': active, 'fees': fees, 'bonovela': bonovela, 'pool': pool,
            'reads': total_reads, 'fee_pct': fs.processing_fee_pct, 'bonovela_pct': 100 - fs.author_share_pct,
            'author_pct': fs.author_share_pct}
