from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.engagement.models import InboxMessage
from apps.wallet import services as wallet
from apps.wallet.models import Subscription

from .models import Broadcast

User = get_user_model()


def audience(key):
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
    return qs


@transaction.atomic
def send_broadcast(sender, *, title, body, audience_key, story=None, bonus=0) -> Broadcast:
    users = list(audience(audience_key))
    InboxMessage.objects.bulk_create(
        [InboxMessage(user=u, title=title, body=body, story=story) for u in users]
    )
    if bonus:
        for u in users:
            wallet.grant_bonus(u, bonus, f'Gift · {title}', days=7)
    return Broadcast.objects.create(title=title, body=body, audience=audience_key, story=story, bonus=bonus,
                                    recipients=len(users), sent_by=sender)
