"""Wallet, store, subscription and rewards API."""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from apps.engagement.models import CheckIn, ReadingTime, TaskClaim
from apps.wallet import services
from apps.wallet.models import (
    BonusBatch,
    ChapterUnlock,
    CoinPackage,
    GiftItem,
    SubscriptionPlan,
    Transaction,
    VoucherEntry,
)

from . import serializers as s

Auth = [permissions.IsAuthenticated]


def _wallet_data(user):
    w = services.wallet_for(user)
    sub = user.subscriptions.filter(ends_at__gt=timezone.now()).first()
    return {
        'coins': w.coins,
        'bonus': services.bonus_balance(user),
        'vouchers': w.vouchers,
        'plus_until': sub.ends_at if sub else None,
    }


@api_view(['GET'])
@permission_classes(Auth)
def wallet(request):
    return Response(_wallet_data(request.user))


@api_view(['GET'])
@permission_classes(Auth)
def transactions(request):
    """?type=purchases (Transaction History) | expenses (Expense Record, 30 days)."""
    qs = Transaction.objects.filter(user=request.user)
    if request.query_params.get('type') == 'expenses':
        qs = qs.filter(kind__in=[Transaction.KIND_UNLOCK, Transaction.KIND_GIFT],
                       created_at__gte=timezone.now() - timedelta(days=30))
    else:
        qs = qs.exclude(kind__in=[Transaction.KIND_UNLOCK, Transaction.KIND_GIFT])
    return Response(s.TransactionSerializer(qs[:200], many=True).data)


@api_view(['GET'])
@permission_classes(Auth)
def bonus_history(request):
    return Response(s.BonusBatchSerializer(BonusBatch.objects.filter(user=request.user)[:200], many=True).data)


@api_view(['GET'])
@permission_classes(Auth)
def voucher_history(request):
    return Response(s.VoucherEntrySerializer(VoucherEntry.objects.filter(user=request.user)[:200], many=True).data)


@api_view(['GET'])
@permission_classes(Auth)
def unlocked(request):
    """Chapter Unlocked: grouped by book."""
    groups = {}
    for u in ChapterUnlock.objects.filter(user=request.user).select_related('chapter__story').order_by('-created_at'):
        story = u.chapter.story
        g = groups.setdefault(story.id, {'book_id': story.id, 'title': story.display_title, 'chapters': [],
                                          'last_unlock': u.created_at})
        g['chapters'].append(u.chapter.number)
    for g in groups.values():
        g['chapters'].sort()
    return Response(list(groups.values()))


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def store(request):
    return Response({
        'packages': s.CoinPackageSerializer(CoinPackage.objects.filter(is_active=True), many=True).data,
        'plans': s.PlanSerializer(SubscriptionPlan.objects.filter(is_active=True), many=True).data,
        'gifts': s.GiftItemSerializer(GiftItem.objects.all(), many=True).data,
    })


@api_view(['POST'])
@permission_classes(Auth)
def top_up(request):
    """Credits a package. In production, verify `receipt` with Google Play /
    the App Store before calling services.top_up — until then this endpoint
    only works when DEBUG is on."""
    if not settings.DEBUG:
        return Response({'detail': 'Store receipt verification is not configured.'}, status=501)
    package = get_object_or_404(CoinPackage, pk=request.data.get('package'), is_active=True)
    services.top_up(request.user, package)
    return Response(_wallet_data(request.user))


@api_view(['POST'])
@permission_classes(Auth)
def subscribe(request):
    if not settings.DEBUG:
        return Response({'detail': 'Store receipt verification is not configured.'}, status=501)
    plan = get_object_or_404(SubscriptionPlan, pk=request.data.get('plan'), is_active=True)
    sub = services.subscribe(request.user, plan)
    return Response({'plus_until': sub.ends_at}, status=201)


@api_view(['POST'])
@permission_classes(Auth)
def redeem(request):
    from apps.marketing.services import redeem as redeem_code
    reward, error = redeem_code(request.user, request.data.get('code'))
    if error:
        return Response({'detail': error[0], 'code': error[1]}, status=400)
    return Response(reward)


# ── Rewards ──────────────────────────────────────────────────────────────────
DAILY_TASKS = {'video': (1, 5), 'visit': (2, 3), 'share': (3, 1)}  # key: (bonus, times per day)
ONE_TIME_TASKS = {'facebook': 3, 'instagram': 3, 'tiktok': 3, 'invite': 50}
READING_MILESTONES = {15: (5, 0), 30: (10, 1), 60: (20, 1)}  # minutes: (bonus, vouchers)


def _streak(user, today):
    last = CheckIn.objects.filter(user=user).first()
    if last and last.date == today:
        return last.streak_day, True
    if last and last.date == today - timedelta(days=1) and last.streak_day < 7:
        return last.streak_day, False
    return 0, False


@api_view(['GET'])
@permission_classes(Auth)
def rewards(request):
    user, today = request.user, timezone.localdate()
    streak, checked_in = _streak(user, today)
    claims = TaskClaim.objects.filter(user=user)
    minutes = ReadingTime.objects.filter(user=user, date=today).values_list('minutes', flat=True).first() or 0
    return Response({
        'checkin': {'streak': streak, 'checked_in_today': checked_in, 'rewards': settings.BONOVELA['CHECKIN_REWARDS']},
        'daily_tasks': {k: {'bonus': b, 'max': m, 'done': claims.filter(key=k, date=today).count()}
                        for k, (b, m) in DAILY_TASKS.items()},
        'one_time_tasks': {k: {'bonus': b, 'done': claims.filter(key=k, date=None).exists()}
                           for k, b in ONE_TIME_TASKS.items()},
        'reading': {'minutes_today': minutes, 'milestones': [
            {'minutes': m, 'bonus': b, 'vouchers': v, 'claimed': claims.filter(key=f'read_{m}', date=today).exists()}
            for m, (b, v) in READING_MILESTONES.items()
        ]},
    })


@api_view(['POST'])
@permission_classes(Auth)
@transaction.atomic
def check_in(request):
    user, today = request.user, timezone.localdate()
    streak, checked_in = _streak(user, today)
    if checked_in:
        return Response({'detail': 'Already checked in today.'}, status=400)
    day = streak + 1
    bonus = settings.BONOVELA['CHECKIN_REWARDS'][day - 1]
    CheckIn.objects.create(user=user, date=today, streak_day=day, bonus=bonus)
    services.grant_bonus(user, bonus, f'Check-in day {day}')
    return Response({'streak': day, 'bonus': bonus})


@api_view(['POST'])
@permission_classes(Auth)
@transaction.atomic
def complete_task(request, key):
    user, today = request.user, timezone.localdate()
    if key in DAILY_TASKS:
        bonus, limit = DAILY_TASKS[key]
        if TaskClaim.objects.filter(user=user, key=key, date=today).count() >= limit:
            return Response({'detail': 'Done for today.'}, status=400)
        TaskClaim.objects.create(user=user, key=key, date=today, bonus=bonus)
    elif key in ONE_TIME_TASKS:
        bonus = ONE_TIME_TASKS[key]
        if TaskClaim.objects.filter(user=user, key=key, date=None).exists():
            return Response({'detail': 'Already claimed.'}, status=400)
        TaskClaim.objects.create(user=user, key=key, date=None, bonus=bonus)
    else:
        return Response({'detail': 'Unknown task.'}, status=404)
    services.grant_bonus(user, bonus, key.replace('_', ' ').title())
    return Response({'bonus': bonus})


@api_view(['POST'])
@permission_classes(Auth)
def reading_time(request):
    """App reports reading minutes (e.g. every minute in the reader)."""
    minutes = max(0, min(int(request.data.get('minutes', 1)), 30))
    rt, _ = ReadingTime.objects.get_or_create(user=request.user, date=timezone.localdate())
    rt.minutes += minutes
    rt.save(update_fields=['minutes'])
    return Response({'minutes_today': rt.minutes})


@api_view(['POST'])
@permission_classes(Auth)
@transaction.atomic
def claim_reading(request, minutes):
    if minutes not in READING_MILESTONES:
        return Response({'detail': 'Unknown milestone.'}, status=404)
    user, today = request.user, timezone.localdate()
    read = ReadingTime.objects.filter(user=user, date=today).values_list('minutes', flat=True).first() or 0
    key = f'read_{minutes}'
    if read < minutes:
        return Response({'detail': f'{minutes - read} more minutes to go.'}, status=400)
    if TaskClaim.objects.filter(user=user, key=key, date=today).exists():
        return Response({'detail': 'Already claimed today.'}, status=400)
    bonus, vouchers = READING_MILESTONES[minutes]
    TaskClaim.objects.create(user=user, key=key, date=today, bonus=bonus)
    services.grant_bonus(user, bonus, f'Read for {minutes} minutes')
    if vouchers:
        services.grant_vouchers(user, vouchers, 'Reading task')
    return Response({'bonus': bonus, 'vouchers': vouchers})
