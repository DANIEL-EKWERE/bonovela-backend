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
