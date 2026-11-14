"""Bonovela's financial position, computed from recorded transactions,
earnings, payment requests, expenses and refunds. Amounts are USD cents.

Only succeeded real-money payments (coin packages and subscriptions) are
revenue. Store and gateway fees come off gross to give net; a payment is
settled once Finance records the provider's settlement."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.users.models import User
from apps.wallet.models import AuthorEarning, Payout, Transaction

from .models import Expense, FinanceSettings, RefundCase, VerifiedBalance

STORES = ('google_play', 'app_store')
STAFF_ROLES = (User.ROLE_SE, User.ROLE_CE, User.ROLE_MARKETING, User.ROLE_FINANCE)


def sales(start=None, end=None):
    qs = Transaction.objects.filter(kind__in=(Transaction.KIND_TOP_UP, Transaction.KIND_SUBSCRIPTION),
                                    status=Transaction.STATUS_SUCCEEDED)
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lt=end)
    return qs


def _sum(qs, field):
    return qs.aggregate(t=Sum(field))['t'] or 0


def fee_cents(provider, cents, fs=None):
    fs = fs or FinanceSettings.current()
    pct = fs.store_fee_pct if provider in STORES else fs.processing_fee_pct
    return int((Decimal(cents) * pct / 100).quantize(Decimal(1)))


def money(qs, fs=None):
    """(gross, fees, net) for a queryset of sales, fees per provider."""
    fs = fs or FinanceSettings.current()
    gross = fees = 0
    for provider, cents in qs.values_list('provider').annotate(t=Sum('price_cents')).values_list('provider', 't'):
        gross += cents or 0
        fees += fee_cents(provider, cents or 0, fs)
    return gross, fees, gross - fees


def _earnings(staff, start=None, end=None):
    qs = AuthorEarning.objects.filter(author__role__in=STAFF_ROLES) if staff else \
        AuthorEarning.objects.exclude(author__role__in=STAFF_ROLES)
    if start:
        qs = qs.filter(created_at__date__gte=start)
    if end:
        qs = qs.filter(created_at__date__lt=end)
    return qs


def _payouts(statuses, staff=None):
    qs = Payout.objects.filter(status__in=statuses)
    if staff is True:
        qs = qs.filter(author__role__in=STAFF_ROLES)
    elif staff is False:
        qs = qs.exclude(author__role__in=STAFF_ROLES)
    return qs


def treasury(fs=None):
    """All-time balances of every Treasury account."""
    fs = fs or FinanceSettings.current()
    all_sales = sales()
    gross, fees, net = money(all_sales, fs)
    _, s_fees, settled_net = money(all_sales.filter(settled_at__isnull=False), fs)
    paid_out = _sum(_payouts([Payout.STATUS_PAID]), 'amount_cents')
    expenses = _sum(Expense.objects.filter(status=Expense.STATUS_PAID), 'amount_cents')
    refunds = _sum(RefundCase.objects.all(), 'refunded_cents')
    committed_authors = _sum(_payouts([Payout.STATUS_APPROVED, Payout.STATUS_PAID], staff=False), 'amount_cents')
    committed_staff = _sum(_payouts([Payout.STATUS_APPROVED, Payout.STATUS_PAID], staff=True), 'amount_cents')
    calc_authors = _sum(_earnings(False), 'amount_cents')
    staff_q = _earnings(True)
    calc_editors = _sum(staff_q.exclude(author__role=User.ROLE_CE), 'amount_cents')
    calc_ce = _sum(staff_q.filter(author__role=User.ROLE_CE), 'amount_cents')
    reserved_authors = max(0, calc_authors - committed_authors)
    reserved_staff = max(0, calc_editors + calc_ce - committed_staff)
    ce_share = calc_ce / (calc_editors + calc_ce) if calc_editors + calc_ce else 0
    recent = _sum(sales(start=timezone.localdate() - timedelta(days=90)), 'price_cents')
    chargeback = int(recent * fs.chargeback_reserve_pct / 100)
    payables = _sum(_payouts([Payout.STATUS_APPROVED]), 'amount_cents')
    settled = settled_net - paid_out - expenses - refunds
    reserved = reserved_authors + reserved_staff + chargeback
    return {
        'gross': gross, 'fees': fees, 'net': net, 'settled_net': settled_net, 'pending_settlement': net - settled_net,
        'settled': settled, 'reserved': reserved, 'reserved_authors': reserved_authors,
        'reserved_editors': round(reserved_staff * (1 - ce_share)), 'reserved_ce': round(reserved_staff * ce_share),
        'chargeback': chargeback, 'payables': payables, 'paid_out': paid_out, 'expenses': expenses,
        'refunds': refunds, 'available': settled - reserved - payables,
        'retained': net - calc_authors - calc_editors - calc_ce,
    }


ACCOUNTS = [
    # key, name, description, type
    ('pending_settlement', 'Pending settlements', 'Confirmed by payment providers, not yet in the bank', 'Asset'),
    ('settled', 'Settled funds', 'Received in Bonovela settlement accounts, less payouts, expenses and refunds',
     'Asset'),
    ('fees', 'Provider & store fees', 'App store commission and gateway fees', 'Expense'),
    ('reserved_authors', 'Reserved: author earnings', 'Calculated author shares, not yet approved', 'Liability'),
    ('reserved_editors', 'Reserved: editor commissions', 'Calculated Senior Editor shares, not yet approved',
     'Liability'),
    ('reserved_ce', 'Reserved: chief editor', 'Calculated Chief Editor share, not yet approved', 'Liability'),
    ('chargeback', 'Chargeback reserve', 'Share of the last 90 days of revenue held for disputes', 'Liability'),
    ('payables', 'Approved payables', 'Finance-approved, awaiting payout', 'Liability'),
    ('retained', 'Platform retained revenue', 'Bonovela share after authors and staff', 'Equity'),
]


def accounts(t=None):
    t = t or treasury()
    latest = {}
    for v in VerifiedBalance.objects.all():
        latest.setdefault(v.account, v)
    rows = []
    for key, name, desc, kind in ACCOUNTS:
        v = latest.get(key)
        rows.append({'key': key, 'name': name, 'desc': desc, 'type': kind, 'recorded': t[key], 'verified': v,
                     'difference': (v.cents - t[key]) if v else None})
    return rows


def _at(d):
    return datetime.combine(d, time(12), tzinfo=timezone.get_current_timezone())


def journal(limit=60):
    """Lines of the Settled funds account, oldest first for the running
    balance, returned newest first: settlements in, payouts, expenses and
    refunds out."""
    fs = FinanceSettings.current()
    lines = []
    for t in sales().filter(settled_at__isnull=False).select_related('user'):
        net = t.price_cents - fee_cents(t.provider, t.price_cents, fs)
        lines.append({'when': t.settled_at, 'kind': 'Settlement', 'code': f'JRN-S{t.pk}',
                      'desc': f'Settlement from {t.get_provider_display() or "provider"}', 'ref': t.code,
                      'debit': net, 'credit': 0, 'officer': t.settlement_ref or 'system'})
    for p in _payouts([Payout.STATUS_PAID]).select_related('author'):
        lines.append({'when': p.paid_at or p.created_at, 'kind': 'Payout', 'code': f'JRN-P{p.pk}',
                      'desc': f'{p.get_category_display()} — {p.author.display_name}', 'ref': p.code,
                      'debit': 0, 'credit': p.amount_cents, 'officer': p.reference})
    for e in Expense.objects.filter(status=Expense.STATUS_PAID):
        lines.append({'when': e.paid_at or _at(e.date), 'kind': 'Expense', 'code': f'JRN-E{e.pk}', 'desc': e.title,
                      'ref': e.code, 'debit': 0, 'credit': e.amount_cents, 'officer': ''})
    for r in RefundCase.objects.filter(refunded_cents__gt=0):
        lines.append({'when': r.decided_at or r.created_at, 'kind': 'Refund', 'code': f'JRN-R{r.pk}',
                      'desc': f'{r.get_kind_display()} — {r.reason}', 'ref': f'RF-{r.pk}', 'debit': 0,
                      'credit': r.refunded_cents, 'officer': ''})
    lines.sort(key=lambda x: x['when'])
    balance = 0
    for line in lines:
        line['previous'] = balance
        balance += line['debit'] - line['credit']
        line['balance'] = balance
    return list(reversed(lines))[:limit]


def position(start, end):
    """The Finance Overview figures for [start, end)."""
    fs = FinanceSettings.current()
    t = treasury(fs)
    period = sales(start, end)
    gross, fees, net = money(period, fs)
    _, _, settled = money(period.filter(settled_at__isnull=False), fs)
    every = Transaction.objects.filter(created_at__date__gte=start, created_at__date__lt=end,
                                       kind__in=(Transaction.KIND_TOP_UP, Transaction.KIND_SUBSCRIPTION))
    authors = _sum(_earnings(False, start, end), 'amount_cents')
    staff = _sum(_earnings(True, start, end), 'amount_cents')
    in_period = Q(approved_at__date__gte=start, approved_at__date__lt=end)
    open_requests = Payout.objects.filter(status__in=Payout.OPEN)
    return {
        't': t, 'gross': gross, 'fees': fees, 'net': net, 'settled': settled,
        'count': every.count(), 'failed': every.filter(status=Transaction.STATUS_FAILED).count(),
        'author_earnings': authors, 'staff_commissions': staff,
        'approved_authors': _sum(_payouts([Payout.STATUS_APPROVED, Payout.STATUS_PAID], staff=False)
                                 .filter(in_period), 'amount_cents'),
        'pending_requests': _sum(open_requests, 'requested_cents'), 'pending_n': open_requests.count(),
        'withdrawals_paid': _sum(Payout.objects.filter(category=Payout.CAT_WITHDRAWAL, status=Payout.STATUS_PAID,
                                                       paid_at__date__gte=start, paid_at__date__lt=end),
                                 'amount_cents'),
        'expenses': _sum(Expense.objects.filter(status=Expense.STATUS_PAID, date__gte=start, date__lt=end),
                         'amount_cents'),
        'refunds': _sum(RefundCase.objects.filter(created_at__date__gte=start, created_at__date__lt=end),
                        'refunded_cents'),
        'retained': net - authors - staff,
    }
