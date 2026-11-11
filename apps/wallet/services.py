"""Wallet rules. All balance changes go through here so they're atomic and
always leave a Transaction / history row behind."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import (
    AuthorEarning,
    BonusBatch,
    ChapterUnlock,
    Gift,
    Subscription,
    Transaction,
    Vote,
    VoucherEntry,
    Wallet,
)


class InsufficientBalance(Exception):
    pass


def wallet_for(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


def bonus_balance(user) -> int:
    now = timezone.now()
    return sum(b.amount - b.consumed for b in user.bonus_batches.filter(expires_at__gt=now))


def grant_bonus(user, amount: int, source: str, days: int | None = None) -> BonusBatch:
    days = days or settings.BONOVELA['BONUS_EXPIRY_DAYS']
    return BonusBatch.objects.create(
        user=user, amount=amount, source=source, expires_at=timezone.now() + timedelta(days=days)
    )


def grant_vouchers(user, amount: int, source: str):
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=wallet_for(user).pk)
        wallet.vouchers += amount
        wallet.save(update_fields=['vouchers'])
        VoucherEntry.objects.create(user=user, amount=amount, source=source)


@transaction.atomic
def spend(user, amount: int, *, title: str, kind: str, story=None, chapter=None) -> tuple[int, int]:
    """Spend bonus first (soonest-expiring batch first), then coins.
    Returns (coins_spent, bonus_spent)."""
    wallet = Wallet.objects.select_for_update().get(pk=wallet_for(user).pk)
    batches = list(
        BonusBatch.objects.select_for_update()
        .filter(user=user, expires_at__gt=timezone.now())
        .order_by('expires_at')
    )
    available_bonus = sum(b.amount - b.consumed for b in batches)
    if wallet.coins + available_bonus < amount:
        raise InsufficientBalance

    left = amount
    for b in batches:
        if not left:
            break
        take = min(left, b.amount - b.consumed)
        if take:
            b.consumed += take
            b.save(update_fields=['consumed'])
            left -= take
    bonus_spent = amount - left
    wallet.coins -= left
    wallet.save(update_fields=['coins'])
    Transaction.objects.create(
        user=user, kind=kind, title=title, coins=-left, bonus_used=bonus_spent, story=story, chapter=chapter
    )
    return left, bonus_spent


def available_to_withdraw(user) -> int:
    """Earnings (USD cents) not yet paid out or already queued for payout."""
    from django.db.models import Sum

    from .models import AuthorEarning, Payout

    earned = AuthorEarning.objects.filter(author=user).aggregate(t=Sum('amount_cents'))['t'] or 0
    out = Payout.objects.filter(author=user, status__in=[Payout.STATUS_PAID, Payout.STATUS_PENDING]).aggregate(
        t=Sum('amount_cents'))['t'] or 0
    return max(0, earned - out)


def has_plus(user) -> bool:
    now = timezone.now()
    return Subscription.objects.filter(user=user, starts_at__lte=now, ends_at__gt=now).exists()


def can_read(user, chapter) -> bool:
    if chapter.is_free:
        return True
    if not user or not user.is_authenticated:
        return False
    if chapter.story.author_id == user.id:
        return True
    if chapter.story.is_short and has_plus(user):
        return True
    return ChapterUnlock.objects.filter(user=user, chapter=chapter).exists()


def _credit_author(story, coins: int, note: str):
    """Signed authors earn a share of coins spent on their book (1 coin ≈ 1¢)."""
    if story.is_signed and coins:
        share = int(coins * settings.BONOVELA['AUTHOR_SHARE'])
        if share:
            AuthorEarning.objects.create(author=story.author, story=story, type='profit_share', amount_cents=share, note=note)


@transaction.atomic
def unlock_chapter(user, chapter) -> ChapterUnlock:
    existing = ChapterUnlock.objects.filter(user=user, chapter=chapter).first()
    if existing:
        return existing
    coins, bonus = spend(
        user,
        chapter.price,
        title=f'{chapter.story.display_title} · Chapter {chapter.number}',
        kind=Transaction.KIND_UNLOCK,
        story=chapter.story,
        chapter=chapter,
    )
    _credit_author(chapter.story, coins + bonus, f'Unlock · Chapter {chapter.number}')
    return ChapterUnlock.objects.create(user=user, chapter=chapter, coins_paid=coins, bonus_paid=bonus)


@transaction.atomic
def send_gift(user, story, item) -> Gift:
    coins, bonus = spend(user, item.price, title=f'{item.name} for {story.display_title}', kind=Transaction.KIND_GIFT, story=story)
    gift = Gift.objects.create(sender=user, story=story, item=item, price=item.price, votes=item.votes)
    Vote.objects.create(user=user, story=story, count=item.votes, from_gift=gift)
    _credit_author(story, coins + bonus, f'Gift · {item.name}')
    return gift


@transaction.atomic
def vote(user, story, count: int) -> Vote:
    wallet = Wallet.objects.select_for_update().get(pk=wallet_for(user).pk)
    if count < 1 or wallet.vouchers < count:
        raise InsufficientBalance
    wallet.vouchers -= count
    wallet.save(update_fields=['vouchers'])
    VoucherEntry.objects.create(user=user, amount=-count, source=f'Voted for {story.display_title}')
    return Vote.objects.create(user=user, story=story, count=count)


@transaction.atomic
def top_up(user, package) -> Transaction:
    """Credits a coin package. Call only after the store/payment provider has
    confirmed the purchase."""
    wallet = Wallet.objects.select_for_update().get(pk=wallet_for(user).pk)
    wallet.coins += package.coins
    wallet.save(update_fields=['coins'])
    tx = Transaction.objects.create(
        user=user, kind=Transaction.KIND_TOP_UP, title=f'Top up {package.coins} coins',
        coins=package.coins, price_cents=package.price_cents,
    )
    if package.bonus:
        grant_bonus(user, package.bonus, 'Top-up bonus', days=30)
    grant_vouchers(user, 3, 'Top up')
    return tx


@transaction.atomic
def subscribe(user, plan) -> Subscription:
    now = timezone.now()
    current = Subscription.objects.filter(user=user, ends_at__gt=now).order_by('-ends_at').first()
    start = current.ends_at if current else now
    sub = Subscription.objects.create(user=user, plan=plan, starts_at=start, ends_at=start + timedelta(days=plan.days))
    Transaction.objects.create(
        user=user, kind=Transaction.KIND_SUBSCRIPTION, title=f'Bonovela Plus · {plan.name}', price_cents=plan.price_cents
    )
    return sub
