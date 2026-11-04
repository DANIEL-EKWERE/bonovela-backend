"""Episode access and unlocking. Coins go through the wallet service."""

from django.conf import settings
from django.db import transaction

from apps.wallet import services as wallet
from apps.wallet.models import AuthorEarning, Transaction

from .models import EpisodeUnlock


def can_watch(user, episode) -> bool:
    if episode.is_free:
        return True
    if not user or not user.is_authenticated:
        return False
    if episode.drama.creator_id == user.id:
        return True
    if wallet.has_plus(user):
        return True
    return EpisodeUnlock.objects.filter(user=user, episode=episode).exists()


@transaction.atomic
def unlock_episode(user, episode) -> EpisodeUnlock:
    existing = EpisodeUnlock.objects.filter(user=user, episode=episode).first()
    if existing:
        return existing
    drama = episode.drama
    coins, bonus = wallet.spend(
        user, episode.price, title=f'{drama.title} · Episode {episode.number}', kind=Transaction.KIND_UNLOCK,
        story=drama.story,
    )
    share = int((coins + bonus) * settings.BONOVELA['CREATOR_SHARE'])
    if share:
        AuthorEarning.objects.create(author=drama.creator, type='profit_share', amount_cents=share,
                                     note=f'Drama · {drama.title} EP {episode.number}')
    return EpisodeUnlock.objects.create(user=user, episode=episode, coins_paid=coins, bonus_paid=bonus)
