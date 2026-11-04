"""Finance dashboard: revenue, transactions, author/creator balances and
payouts, monthly statements. Amounts are USD cents; coins are 1¢ each."""

import csv
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.users.models import User
from apps.wallet.models import AuthorEarning, Payout, PayoutMethod, Transaction

from .access import role_area

finance_area = role_area(User.ROLE_FINANCE)
PAID_KINDS = (Transaction.KIND_TOP_UP, Transaction.KIND_SUBSCRIPTION)


def _cents(qs, field='price_cents'):
    return qs.aggregate(t=Sum(field))['t'] or 0


def _month_start(d, back=0):
    y, m = d.year, d.month - back
    while m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def _csv(name, header, rows):
    resp = HttpResponse(content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="{name}"'
    w = csv.writer(resp)
    w.writerow(header)
    w.writerows(rows)
    return resp


def _balances():
    """One row per payee with earnings: earned, paid, pending, balance."""
    earned = dict(AuthorEarning.objects.values_list('author').annotate(t=Sum('amount_cents')))
    paid = dict(Payout.objects.filter(status=Payout.STATUS_PAID).values_list('author').annotate(t=Sum('amount_cents')))
    pending = dict(Payout.objects.filter(status=Payout.STATUS_PENDING).values_list('author').annotate(t=Sum('amount_cents')))
    users = User.objects.in_bulk(list(earned))
    methods = {m.author_id: m for m in PayoutMethod.objects.filter(author__in=list(earned))}
    rows = []
    for uid, total in earned.items():
        p, q = paid.get(uid, 0), pending.get(uid, 0)
        rows.append({'user': users[uid], 'earned': total, 'paid': p, 'pending': q, 'balance': total - p - q,
                     'method': methods.get(uid)})
    return sorted(rows, key=lambda r: -r['balance'])


@finance_area
def home(request):
    now = timezone.now()
    today = timezone.localdate()
    paid = Transaction.objects.filter(kind__in=PAID_KINDS)
    mtd_start = today.replace(day=1)
    last_start = _month_start(today, 1)
    months = []
    for back in range(5, -1, -1):
        start, end = _month_start(today, back), _month_start(today, back - 1)
        months.append({'label': start.strftime('%b'), 'cents': _cents(paid.filter(created_at__date__gte=start,
                                                                                 created_at__date__lt=end))})
    days = [{'label': (today - timedelta(days=i)).strftime('%b %d'),
             'cents': _cents(paid.filter(created_at__date=today - timedelta(days=i)))} for i in range(29, -1, -1)]
    for series in (months, days):
        peak = max((x['cents'] for x in series), default=0) or 1
        for x in series:
            x['pct'] = round(x['cents'] * 100 / peak)

    balances = _balances()
    minimum = settings.BONOVELA['PAYOUT_MINIMUM_CENTS']
    return render(request, 'dashboard/finance/home.html', {
        'mtd': _cents(paid.filter(created_at__date__gte=mtd_start)),
        'last_month': _cents(paid.filter(created_at__date__gte=last_start, created_at__date__lt=mtd_start)),
        'last_30': _cents(paid.filter(created_at__gte=now - timedelta(days=30))),
        'top_ups': _cents(paid.filter(kind=Transaction.KIND_TOP_UP, created_at__date__gte=mtd_start)),
        'subs': _cents(paid.filter(kind=Transaction.KIND_SUBSCRIPTION, created_at__date__gte=mtd_start)),
        'liability': sum(r['balance'] for r in balances),
        'due': sum(r['balance'] for r in balances if r['balance'] >= minimum),
        'pending_payouts': _cents(Payout.objects.filter(status=Payout.STATUS_PENDING), 'amount_cents'),
        'paid_month': _cents(Payout.objects.filter(status=Payout.STATUS_PAID, paid_at__date__gte=mtd_start), 'amount_cents'),
        'months': months, 'days': days, 'minimum': minimum,
    })


@finance_area
def transactions(request):
    qs = Transaction.objects.select_related('user', 'story').order_by('-created_at')
    kind = request.GET.get('kind', '')
    q = request.GET.get('q', '').strip()
    start, end = request.GET.get('from', ''), request.GET.get('to', '')
    if kind == 'paid':
        qs = qs.filter(kind__in=PAID_KINDS)
    elif kind:
        qs = qs.filter(kind=kind)
    if q:
        qs = qs.filter(Q(user__email__icontains=q) | Q(title__icontains=q))
    try:
        if start:
            qs = qs.filter(created_at__date__gte=datetime.strptime(start, '%Y-%m-%d').date())
        if end:
            qs = qs.filter(created_at__date__lte=datetime.strptime(end, '%Y-%m-%d').date())
    except ValueError:
        messages.error(request, 'Dates must look like 2026-11-01.')
    if request.GET.get('export') == 'csv':
        return _csv('bonovela-transactions.csv', ['id', 'date', 'user', 'kind', 'title', 'coins', 'bonus_used', 'usd'],
                    ([t.pk, t.created_at.isoformat(), t.user.email, t.kind, t.title, t.coins, t.bonus_used,
                      f'{t.price_cents / 100:.2f}'] for t in qs.iterator()))
    return render(request, 'dashboard/finance/transactions.html', {
        'tx': qs[:200], 'count': qs.count(), 'total': _cents(qs), 'kind': kind, 'q': q, 'start': start, 'end': end,
        'kinds': Transaction.KIND_CHOICES, 'query': request.GET.urlencode(),
    })


@finance_area
def balances(request):
    rows = _balances()
    minimum = settings.BONOVELA['PAYOUT_MINIMUM_CENTS']
    if request.GET.get('due'):
        rows = [r for r in rows if r['balance'] >= minimum]
    if request.GET.get('export') == 'csv':
        return _csv('bonovela-balances.csv', ['email', 'name', 'earned', 'paid', 'pending', 'balance', 'method'],
                    ([r['user'].email, r['user'].display_name, *(f'{r[k] / 100:.2f}' for k in
                      ('earned', 'paid', 'pending', 'balance')), r['method'].get_method_display() if r['method'] else '']
                     for r in rows))
    return render(request, 'dashboard/finance/balances.html', {'rows': rows, 'minimum': minimum,
                                                                'due': bool(request.GET.get('due'))})


@finance_area
def payouts(request):
    tab = request.GET.get('tab', Payout.STATUS_PENDING)
    qs = Payout.objects.filter(status=tab).select_related('author', 'created_by')
    if request.GET.get('export') == 'csv':
        return _csv(f'bonovela-payouts-{tab}.csv', ['id', 'email', 'name', 'usd', 'period_end', 'method', 'reference'],
                    ([p.pk, p.author.email, p.author.display_name, f'{p.amount_cents / 100:.2f}', p.period_end,
                      p.method, p.reference] for p in qs))
    minimum = settings.BONOVELA['PAYOUT_MINIMUM_CENTS']
    due = [r for r in _balances() if r['balance'] >= minimum]
    return render(request, 'dashboard/finance/payouts.html', {
        'payouts': qs, 'tab': tab, 'total': _cents(qs, 'amount_cents'), 'statuses': Payout.STATUS_CHOICES,
        'due_ready': [r for r in due if r['method']], 'due_missing': [r for r in due if not r['method']],
        'minimum': minimum,
    })


@finance_area
@require_POST
@transaction.atomic
def payout_generate(request):
    minimum = settings.BONOVELA['PAYOUT_MINIMUM_CENTS']
    today = timezone.localdate()
    made = 0
    for r in _balances():
        if r['balance'] >= minimum and r['method']:
            m = r['method']
            Payout.objects.create(
                author=r['user'], amount_cents=r['balance'], period_end=today, created_by=request.user,
                method=f'{m.get_method_display()} · {m.account_name} · {m.bank_name + " · " if m.bank_name else ""}{m.account_detail}',
            )
            made += 1
    messages.success(request, f'Created {made} payout{"s" if made != 1 else ""}.' if made else 'Nobody is due a payout.')
    return redirect('dashboard:finance_payouts')


@finance_area
@require_POST
def book_commission(request):
    """Record last month's editorial commission as earnings (idempotent)."""
    from apps.editorial.team import book_commissions
    last_month = (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)
    made = book_commissions(last_month)
    messages.success(request, f'Booked {made} commission line{"s" if made != 1 else ""} for {last_month:%B %Y}.'
                     if made else f'{last_month:%B %Y} commission is already booked.')
    return redirect('dashboard:finance_payouts')


@finance_area
@require_POST
def payout_action(request, pk):
    payout = get_object_or_404(Payout, pk=pk)
    action = request.POST.get('action')
    if action == 'paid' and payout.status == Payout.STATUS_PENDING:
        ref = request.POST.get('reference', '').strip()
        if not ref:
            messages.error(request, 'Add the bank or PayPal reference first.')
            return redirect('dashboard:finance_payouts')
        payout.status, payout.reference, payout.paid_at = Payout.STATUS_PAID, ref, timezone.now()
        payout.save(update_fields=['status', 'reference', 'paid_at'])
        from apps.engagement.models import InboxMessage
        InboxMessage.objects.create(user=payout.author, title='Payout sent 💸',
                                    body=f'We sent you ${payout.amount_cents / 100:,.2f}. Reference: {ref}.')
        messages.success(request, f'Marked {payout.author.display_name} as paid.')
    elif action == 'failed' and payout.status == Payout.STATUS_PENDING:
        payout.status = Payout.STATUS_FAILED
        payout.note = request.POST.get('note', '')[:200]
        payout.save(update_fields=['status', 'note'])
        messages.success(request, 'Marked as failed. The amount is back in their balance.')
    return redirect(f"{reverse('dashboard:finance_payouts')}?tab={payout.status}")


@finance_area
def statements(request):
    today = timezone.localdate()
    paid = Transaction.objects.filter(kind__in=PAID_KINDS)
    rows = []
    for back in range(11, -1, -1):
        start, end = _month_start(today, back), _month_start(today, back - 1)
        in_month = {'created_at__date__gte': start, 'created_at__date__lt': end}
        top = _cents(paid.filter(kind=Transaction.KIND_TOP_UP, **in_month))
        subs = _cents(paid.filter(kind=Transaction.KIND_SUBSCRIPTION, **in_month))
        earn = _cents(AuthorEarning.objects.filter(**in_month), 'amount_cents')
        out = _cents(Payout.objects.filter(status=Payout.STATUS_PAID, paid_at__date__gte=start, paid_at__date__lt=end),
                     'amount_cents')
        rows.append({'month': start, 'top_ups': top, 'subs': subs, 'gross': top + subs, 'earnings': earn,
                     'net': top + subs - earn, 'paid_out': out})
    rows.reverse()
    if request.GET.get('export') == 'csv':
        return _csv('bonovela-statements.csv', ['month', 'top_ups', 'subscriptions', 'gross', 'creator_earnings', 'net',
                                                'paid_out'],
                    ([r['month'].strftime('%Y-%m'), *(f'{r[k] / 100:.2f}' for k in
                      ('top_ups', 'subs', 'gross', 'earnings', 'net', 'paid_out'))] for r in rows))
    return render(request, 'dashboard/finance/statements.html', {
        'rows': rows, 'totals': {k: sum(r[k] for r in rows) for k in ('top_ups', 'subs', 'gross', 'earnings', 'net', 'paid_out')},
    })
