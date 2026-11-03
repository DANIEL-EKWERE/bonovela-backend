from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User


@receiver(post_save, sender=User)
def welcome_new_user(sender, instance, created, **kwargs):
    """Every account gets a wallet with the starter balance and a welcome note."""
    if not created:
        return
    from apps.engagement.models import InboxMessage
    from apps.wallet.models import Transaction, Wallet
    from apps.wallet.services import grant_bonus

    Wallet.objects.get_or_create(user=instance, defaults={'coins': 60})
    Transaction.objects.create(user=instance, kind=Transaction.KIND_REWARD, title='Welcome gift', coins=60)
    grant_bonus(instance, 20, 'Welcome bonus', days=7)
    InboxMessage.objects.create(
        user=instance,
        title='Welcome to Bonovela 💗',
        body="We've added 60 coins and 20 bonus to your wallet. Your first 10 chapters of every novel are free.",
    )
