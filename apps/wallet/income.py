"""Author Income: how earnings flow from Finance approval to withdrawal.

    book earnings (pending) ──Finance approves──▶ this month's sections
    end of month ──────────────────────────────▶ Carry over
    Finance releases part of Carry over ─────────▶ Available to withdraw
    author withdraws ────────────────────────────▶ Pending withdrawal ──▶ paid

Nothing is moved or deleted: every balance is calculated from the
earnings, releases and payment requests, so no money can be lost or
counted twice. A rejected withdrawal simply stops counting, which puts
the amount back in Available to withdraw."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from .models import AuthorEarning, IncomeRelease, Payout

COMPLETION_BONUS_CENTS = 4000  # fixed: UPDATE COMPLETION BONUS — $40
APPROVED = AuthorEarning.STATUS_APPROVED


def month_start(day=None):
    return (day or timezone.localdate()).replace(day=1)


def _sum(qs, field='amount_cents'):
    return qs.aggregate(t=Sum(field))['t'] or 0


def credit_book(story, cents, kind, note=''):
    """Add a book's earning to this month's pending line for that book
    (one line per book, type and month, so Finance reviews each book once)."""
    if cents <= 0:
        return None
    period = month_start()
    with transaction.atomic():
        line = (AuthorEarning.objects.select_for_update()
                .filter(author=story.author, story=story, type=kind, period=period,
                        status=AuthorEarning.STATUS_PENDING).first())
        if line:
            AuthorEarning.objects.filter(pk=line.pk).update(amount_cents=F('amount_cents') + cents)
            return line
        return AuthorEarning.objects.create(author=story.author, story=story, type=kind, amount_cents=cents,
                                            period=period, status=AuthorEarning.STATUS_PENDING,
                                            note=note or f'{story.display_title} · {period:%B %Y}')


def balances(author, today=None):
    """Carry over, available to withdraw and pending withdrawal (cents)."""
    this_month = month_start(today)
    approved = AuthorEarning.objects.filter(author=author, status=APPROVED)
    earlier = _sum(approved.filter(period__lt=this_month))
    released = _sum(IncomeRelease.objects.filter(author=author))
    payouts = Payout.objects.filter(author=author)
    committed = _sum(payouts.filter(status__in=Payout.COMMITTED))
    pending = _sum(payouts.filter(status__in=Payout.OPEN + (Payout.STATUS_APPROVED,)))
    return {
        'carry_over': max(0, earlier - released),
        'available': max(0, released - committed),
        'pending_withdrawal': pending,
        'this_month': _sum(approved.filter(period=this_month)),
        'total': _sum(approved),
    }


@transaction.atomic
def release(author, cents, by=None, note=''):
    """Finance: move [cents] from Carry over into Available to withdraw."""
    carry = balances(author)['carry_over']
    if cents <= 0:
        raise ValidationError('Enter an amount to make available.')
    if cents > carry:
        raise ValidationError(f'Carry over is only ${carry / 100:,.2f}.')
    return IncomeRelease.objects.create(author=author, amount_cents=cents, created_by=by, note=note[:200])


def decide(earning, approve, by=None, note=''):
    """Finance approves or rejects a pending earning."""
    if earning.status != AuthorEarning.STATUS_PENDING:
        raise ValidationError('This earning has already been decided.')
    if not approve and not note.strip():
        raise ValidationError('Give a reason for rejecting it.')
    earning.status = APPROVED if approve else AuthorEarning.STATUS_REJECTED
    earning.decided_by, earning.decided_at, earning.decision_note = by, timezone.now(), note[:200]
    earning.save(update_fields=['status', 'decided_by', 'decided_at', 'decision_note'])
    if approve:
        from apps.engagement.models import InboxMessage
        what = earning.get_type_display()
        book = f' for "{earning.story.display_title}"' if earning.story_id else ''
        InboxMessage.objects.create(user=earning.author, story=earning.story,
                                    title=f'{what} approved 💰',
                                    body=f'${earning.amount_cents / 100:,.2f} {what}{book} is now on your Income page.')
    return earning


def award(author, kind, cents, by=None, story=None, note=''):
    """Finance adds an approved earning directly (Completion Bonus, One-time
    Payment)."""
    if cents <= 0:
        raise ValidationError('Enter the amount.')
    if kind == 'completion':
        if not story:
            raise ValidationError('Choose the completed book.')
        if AuthorEarning.objects.filter(story=story, type='completion').exclude(
                status=AuthorEarning.STATUS_REJECTED).exists():
            raise ValidationError('This book already has its completion bonus.')
        cents = COMPLETION_BONUS_CENTS
    if kind == 'one_time' and not note.strip():
        raise ValidationError('Give the reason for the payment.')
    e = AuthorEarning.objects.create(author=author, story=story, type=kind, amount_cents=cents, status=APPROVED,
                                     period=month_start(), decided_by=by, decided_at=timezone.now(),
                                     decision_note=note[:200], note=note[:160])
    from apps.engagement.models import InboxMessage
    InboxMessage.objects.create(user=author, story=story, title=f'{e.get_type_display()} 💰',
                                body=f'${cents / 100:,.2f} {e.get_type_display()} was added to your Income page.')
    return e
