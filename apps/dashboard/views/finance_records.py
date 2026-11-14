"""Bonovela Finance: the Overview, transaction records (readers, authors,
staff, revenue streams), the Payment Approval Centre, withdrawals and the
Treasury ledger. Amounts are USD cents; 1 coin = 1¢."""

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Count, Max, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Story
from apps.engagement.models import InboxMessage
from apps.finance import ledger
from apps.finance.models import AuditEntry, FinanceSettings, VerifiedBalance
from apps.finance.services import audit, money, require
from apps.users.models import User
from apps.wallet.models import AuthorEarning, Payout, PayoutEvent, PayoutMethod, Transaction

from .finance import _csv, finance_area

RANGES = [(7, 'Last 7 days'), (30, 'Last 30 days'), (90, 'Last 90 days'), (365, 'Last 12 months')]
CATEGORIES = [('top_up', 'Coin purchase'), ('subscription', 'Subscription'), ('unlock', 'Chapter unlock'),
              ('gift', 'Reader gift')]


def reader_code(u):
    return f'RD-{100000 + u.pk}'


def _person_id(code, prefix):
    code = (code or '').upper()
    if code.startswith(prefix) and code[len(prefix):].isdigit():
        return int(code[len(prefix):])
    raise Http404


def _range(request):
    try:
        days = int(request.GET.get('days', 30))
    except ValueError:
        days = 30
    days = days if days in dict(RANGES) else 30
    end = timezone.localdate() + timedelta(days=1)
    return days, end - timedelta(days=days), end


# ── Overview ─────────────────────────────────────────────────────────────────
@finance_area
def overview(request):
    days, start, end = _range(request)
    p = ledger.position(start, end)
    fs = FinanceSettings.current()
    period = ledger.sales(start, end)
    # Gross vs net per day for the chart.
    by_day = {}
    for t in period.values('created_at__date', 'provider').annotate(c=Sum('price_cents')):
        d = by_day.setdefault(t['created_at__date'], [0, 0])
        d[0] += t['c'] or 0
        d[1] += (t['c'] or 0) - ledger.fee_cents(t['provider'], t['c'] or 0, fs)
    series = [(start + timedelta(days=i), *by_day.get(start + timedelta(days=i), [0, 0])) for i in range(days)]
    peak = max([g for _, g, _ in series] + [1])
    w, h = 640, 180
    step = w / max(len(series) - 1, 1)

    def line(i):
        return ' '.join(f'{k * step:.1f},{h - v * h / peak:.1f}' for k, v in enumerate(x[i] for x in series))

    # Revenue by category: purchases in money, coin spending at coin value.
    spend = Transaction.objects.filter(created_at__date__gte=start, created_at__date__lt=end,
                                       kind__in=(Transaction.KIND_UNLOCK, Transaction.KIND_GIFT))
    cats = [('Coin purchase', ledger._sum(period.filter(kind=Transaction.KIND_TOP_UP), 'price_cents')),
            ('Subscription', ledger._sum(period.filter(kind=Transaction.KIND_SUBSCRIPTION), 'price_cents')),
            ('Reader gift', -ledger._sum(spend.filter(kind=Transaction.KIND_GIFT), 'coins')),
            ('Chapter unlock', -ledger._sum(spend.filter(kind=Transaction.KIND_UNLOCK), 'coins'))]
    cat_peak = max([c for _, c in cats] + [1])
    providers = []
    for row in period.values('provider').annotate(n=Count('pk'), g=Sum('price_cents')).order_by('-g'):
        fee = ledger.fee_cents(row['provider'], row['g'] or 0, fs)
        providers.append({'name': dict(Transaction.PROVIDER_CHOICES).get(row['provider'], 'Not recorded'),
                          'n': row['n'], 'gross': row['g'] or 0, 'fees': fee, 'net': (row['g'] or 0) - fee})
    return render(request, 'dashboard/finance/overview.html', {
        'p': p, 't': p['t'], 'days': days, 'ranges': RANGES, 'range_label': dict(RANGES)[days],
        'gross_line': line(1), 'net_line': line(2), 'chart_w': w, 'chart_h': h, 'peak': peak,
        'grid_lines': [round(h * i / 4) for i in range(5)],
        'first_day': series[0][0], 'last_day': series[-1][0],
        'cats': [(n, c, c * 100 / cat_peak) for n, c in cats], 'providers': providers,
        'activity': AuditEntry.objects.all()[:8],
        'latest': Transaction.objects.select_related('user').order_by('-created_at')[:8],
    })


# ── Transaction records ──────────────────────────────────────────────────────
def _filtered(request, qs, kinds=None):
    cat = request.GET.get('category', '')
    status = request.GET.get('status', '')
    q = request.GET.get('q', '').strip()
    if kinds:
        qs = qs.filter(kind__in=kinds)
    elif cat in dict(CATEGORIES):
        qs = qs.filter(kind=cat)
    if status in dict(Transaction.STATUS_CHOICES):
        qs = qs.filter(status=status)
    if q:
        code = q.upper()
        cond = Q(reference__icontains=q) | Q(title__icontains=q) | Q(user__email__iexact=q)
        if code.startswith('TXN-') and code[4:].isdigit():
            cond |= Q(pk=int(code[4:]))
        if code.startswith('RD-') and code[3:].isdigit():
            cond |= Q(user_id=int(code[3:]) - 100000)
        qs = qs.filter(cond)
    return qs, {'category': cat, 'status': status, 'q': q}


def _tx_page(request, qs, *, template, extra_col=None, kinds=None, ctx=None, filename='transactions'):
    qs, filters = _filtered(request, qs.select_related('user', 'story', 'story__author'), kinds)
    if request.GET.get('export') == 'csv':
        return _csv(f'bonovela-{filename}.csv',
                    ['date', 'transaction', 'reader', 'category', 'product', 'provider', 'usd', 'coins', 'status',
                     'settlement', 'reference'],
                    ([t.created_at.isoformat(), t.code, reader_code(t.user), t.category, t.title,
                      t.get_provider_display(), f'{t.value_cents / 100:.2f}', t.coins, t.status,
                      'settled' if t.settled_at else ('unsettled' if t.is_money else ''), t.reference]
                     for t in qs.iterator()))
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    data = {'page': page, 'filters': filters, 'categories': CATEGORIES, 'statuses': Transaction.STATUS_CHOICES,
            'extra_col': extra_col, 'show_category': kinds is None, 'query': query.urlencode()}
    data.update(ctx or {})
    return render(request, template, data)


@finance_area
def transactions(request):
    return _tx_page(request, Transaction.objects.exclude(kind=Transaction.KIND_REWARD).order_by('-created_at'),
                    template='dashboard/finance/transactions.html', ctx={
                        'eyebrow': 'Records', 'title': 'All Transactions',
                        'blurb': 'Every reader payment and coin spend on Bonovela — succeeded, pending, failed and '
                                 'reversed.'})


@finance_area
def coins(request):
    return _tx_page(request, Transaction.objects.order_by('-created_at'), kinds=[Transaction.KIND_TOP_UP],
                    extra_col='Coins', filename='coin-purchases', template='dashboard/finance/transactions.html', ctx={
                        'eyebrow': 'Revenue streams', 'title': 'Coin Purchase Records',
                        'blurb': 'Every coin package purchase. Coins are credited only after the store or provider '
                                 'confirms payment.'})


@finance_area
def subscription_payments(request):
    return _tx_page(request, Transaction.objects.order_by('-created_at'), kinds=[Transaction.KIND_SUBSCRIPTION],
                    extra_col='Plan', filename='subscriptions', template='dashboard/finance/transactions.html', ctx={
                        'eyebrow': 'Revenue streams', 'title': 'Subscription Management',
                        'blurb': 'Every subscription payment, searchable by Reader ID, plan and transaction '
                                 'reference.'})


@finance_area
def gifts(request):
    return _tx_page(request, Transaction.objects.order_by('-created_at'), kinds=[Transaction.KIND_GIFT],
                    extra_col='Author', filename='gifts', template='dashboard/finance/transactions.html', ctx={
                        'eyebrow': 'Revenue streams', 'title': 'Reader Gifts',
                        'blurb': 'Every gift a reader sends to an author, traced to the book and the author. Gifts '
                                 'are paid in coins (1 coin = 1¢).'})


# ── Readers ──────────────────────────────────────────────────────────────────
def _readers_qs():
    return (User.objects.filter(role__in=[User.ROLE_READER, User.ROLE_AUTHOR])
            .annotate(spent=Sum('transactions__price_cents', filter=Q(transactions__status='succeeded')),
                      gift_coins=Sum('transactions__coins', filter=Q(transactions__kind='gift')),
                      n=Count('transactions'), last=Max('transactions__created_at')))


@finance_area
def readers(request):
    qs = _readers_qs()
    q = request.GET.get('q', '').strip()
    if q:
        code = q.upper()
        cond = Q(email__icontains=q) | Q(username__icontains=q)
        if code.startswith('RD-') and code[3:].isdigit():
            cond |= Q(pk=int(code[3:]) - 100000)
        qs = qs.filter(cond)
    page = Paginator(qs.order_by('-spent', '-last'), 25).get_page(request.GET.get('page'))
    for u in page:
        u.code = reader_code(u)
    return render(request, 'dashboard/finance/readers.html', {'page': page, 'q': q})


@finance_area
def reader_detail(request, code):
    u = get_object_or_404(User, pk=_person_id(code, 'RD-') - 100000)
    txs = Transaction.objects.filter(user=u)
    succeeded = txs.filter(status=Transaction.STATUS_SUCCEEDED)
    paid = ledger._sum(succeeded.filter(kind__in=(Transaction.KIND_TOP_UP, Transaction.KIND_SUBSCRIPTION)),
                       'price_cents')
    last = txs.order_by('-created_at').first()
    cards = [
        ('Total amount spent', paid, f'{txs.count()} transactions · {txs.filter(status="failed").count()} failed'),
        ('Coin purchases', ledger._sum(succeeded.filter(kind=Transaction.KIND_TOP_UP), 'price_cents'),
         f'Coin balance: {getattr(getattr(u, "wallet", None), "coins", 0):,}'),
        ('Subscriptions', ledger._sum(succeeded.filter(kind=Transaction.KIND_SUBSCRIPTION), 'price_cents'), ''),
        ('Chapter unlocks', -ledger._sum(txs.filter(kind=Transaction.KIND_UNLOCK), 'coins'), 'Coin value'),
        ('Gifts sent', -ledger._sum(txs.filter(kind=Transaction.KIND_GIFT), 'coins'), 'Coin value'),
        ('Refunds & reversals', ledger._sum(txs.filter(status=Transaction.STATUS_REVERSED), 'price_cents'), ''),
    ]
    return _tx_page(request, txs.exclude(kind=Transaction.KIND_REWARD).order_by('-created_at'),
                    template='dashboard/finance/reader_detail.html', filename=reader_code(u),
                    ctx={'r': u, 'code': reader_code(u), 'cards': cards, 'last': last, 'hide_reader': True})


# ── Authors & staff ──────────────────────────────────────────────────────────
def _payment_totals(user):
    p = Payout.objects.filter(author=user)
    s = ledger._sum
    return {
        'calculated': s(AuthorEarning.objects.filter(author=user), 'amount_cents'),
        'approved': s(p.filter(status__in=[Payout.STATUS_APPROVED, Payout.STATUS_PAID]), 'amount_cents'),
        'pending': s(p.filter(status__in=Payout.OPEN), 'requested_cents'),
        'rejected': p.filter(status=Payout.STATUS_REJECTED).count(),
        'paid': s(p.filter(status=Payout.STATUS_PAID), 'amount_cents'),
        'withdrawn': s(p.filter(status=Payout.STATUS_PAID, category=Payout.CAT_WITHDRAWAL), 'amount_cents'),
        'payable': s(p.filter(status=Payout.STATUS_APPROVED), 'amount_cents'),
    }


def _author_revenue(user):
    spend = Transaction.objects.filter(story__author=user)
    return (-ledger._sum(spend.filter(kind=Transaction.KIND_UNLOCK), 'coins'),
            -ledger._sum(spend.filter(kind=Transaction.KIND_GIFT), 'coins'))


@finance_area
def authors(request):
    q = request.GET.get('q', '').strip()
    qs = User.objects.filter(Q(role=User.ROLE_AUTHOR) | Q(earnings__isnull=False)).exclude(
        role__in=ledger.STAFF_ROLES).distinct()
    if q:
        code = q.upper()
        cond = Q(email__icontains=q) | Q(pen_name__icontains=q)
        if code.startswith('AUT-') and code[4:].isdigit():
            cond |= Q(pk=int(code[4:]))
        qs = qs.filter(cond)
    qs = qs.annotate(books=Count('stories', filter=Q(stories__is_removed=False), distinct=True))
    page = Paginator(qs.order_by('pen_name', 'email'), 25).get_page(request.GET.get('page'))
    rows = []
    for u in page:
        book, gifts_ = _author_revenue(u)
        rows.append({'u': u, 'book': book, 'gifts': gifts_, **_payment_totals(u)})
    rows.sort(key=lambda r: -r['calculated'])
    return render(request, 'dashboard/finance/authors.html', {'page': page, 'rows': rows, 'q': q})


def _requests(user):
    return Payout.objects.filter(author=user).select_related('author')


@finance_area
def author_detail(request, code):
    u = get_object_or_404(User, pk=_person_id(code, 'AUT-'))
    book, gifts_ = _author_revenue(u)
    totals = _payment_totals(u)
    stories = Story.objects.filter(author=u, is_removed=False).select_related('editor')
    return _tx_page(request, Transaction.objects.filter(story__author=u).order_by('-created_at'),
                    template='dashboard/finance/author_detail.html', filename=f'AUT-{u.pk}',
                    ctx={'a': u, 'book': book, 'gifts': gifts_, 'totals': totals, 'stories': stories,
                         'requests': _requests(u), 'method': PayoutMethod.objects.filter(author=u).first()})


STAFF_LABELS = {User.ROLE_SE: ('Editorial', 'Senior Editor'), User.ROLE_CE: ('Editorial', 'Chief Editor'),
                User.ROLE_MARKETING: ('Marketing', 'Marketing'), User.ROLE_FINANCE: ('Finance', 'Finance')}


def _arrangement(u):
    from django.conf import settings
    share = settings.BONOVELA['EDITOR_COMMISSION']
    if u.role == User.ROLE_SE:
        return f'{share["se"] * 100:g}% editor share on assigned books'
    if u.role == User.ROLE_CE:
        return f'{share["ce"] * 100:g}% chief editor share on all books'
    return 'Salary (Expenses)'


@finance_area
def staff(request):
    rows = []
    for u in User.objects.filter(role__in=ledger.STAFF_ROLES).order_by('role', 'email'):
        dept, pos = STAFF_LABELS[u.role]
        rows.append({'u': u, 'dept': dept, 'pos': pos, 'arrangement': _arrangement(u), **_payment_totals(u)})
    return render(request, 'dashboard/finance/staff.html', {'rows': rows})


@finance_area
def staff_detail(request, code):
    u = get_object_or_404(User, pk=_person_id(code, 'STF-'), role__in=ledger.STAFF_ROLES)
    dept, pos = STAFF_LABELS[u.role]
    return render(request, 'dashboard/finance/staff_detail.html', {
        's': u, 'dept': dept, 'pos': pos, 'arrangement': _arrangement(u), 'totals': _payment_totals(u),
        'requests': _requests(u), 'earnings': AuthorEarning.objects.filter(author=u).select_related('story')[:50],
    })


# ── Payment Approval Centre & withdrawals ────────────────────────────────────
TABS = [('decide', 'Needs decision', Payout.OPEN), ('approved', 'Approved · processing', (Payout.STATUS_APPROVED,)),
        ('paid', 'Paid', (Payout.STATUS_PAID,)), ('rejected', 'Rejected', (Payout.STATUS_REJECTED,
                                                                            Payout.STATUS_FAILED)),
        ('all', 'All', None)]


def _request_list(request, qs, template, ctx):
    tab = request.GET.get('tab', 'decide')
    tabs = []
    for key, label, statuses in TABS:
        sub = qs.filter(status__in=statuses) if statuses else qs
        tabs.append({'key': key, 'label': label, 'n': sub.count(), 'on': key == tab})
        if key == tab:
            shown = sub
    shown = shown if any(t['on'] for t in tabs) else qs
    page = Paginator(shown.select_related('author'), 25).get_page(request.GET.get('page'))
    open_pk = request.GET.get('open')
    current = qs.filter(pk=open_pk).select_related('author', 'approved_by', 'second_approved_by').first() \
        if open_pk and open_pk.isdigit() else None
    fs = FinanceSettings.current()
    data = {'tabs': tabs, 'tab': tab, 'page': page, 'current': current, 'fs': fs,
            'threshold': FinanceSettings.TWO_PERSON_THRESHOLD_CENTS if fs.two_person_approval else None,
            'can_approve': request.finance_member.can('approve_payouts')}
    if current:
        from apps.wallet.services import available_to_withdraw
        data['events'] = current.events.select_related('actor')
        data['method'] = PayoutMethod.objects.filter(author=current.author).first()
        data['unreleased'] = available_to_withdraw(current.author) + (
            current.amount_cents if current.status in Payout.OPEN else 0)
    data.update(ctx)
    return render(request, template, data)


@finance_area
def approvals(request):
    return _request_list(request, Payout.objects.all(), 'dashboard/finance/approvals.html', {})


@finance_area
def withdrawals(request):
    return _request_list(request, Payout.objects.filter(category=Payout.CAT_WITHDRAWAL),
                         'dashboard/finance/withdrawals.html', {})


def _log(payout, member, action, note=''):
    PayoutEvent.objects.create(payout=payout, action=action, note=note[:200], actor=member.user)


@finance_area
@require_POST
@db_transaction.atomic
def request_action(request, pk):
    member = request.finance_member
    require(member, 'approve_payouts')
    p = get_object_or_404(Payout.objects.select_for_update(), pk=pk)
    action = request.POST.get('action')
    note = request.POST.get('note', '').strip()
    back = request.POST.get('next') or reverse('dashboard:finance_approvals')
    fs = FinanceSettings.current()
    if action == 'approve' and p.status in Payout.OPEN:
        try:
            cents = round(float(request.POST.get('amount', '')) * 100)
        except ValueError:
            cents = 0
        if not 0 < cents <= p.requested_cents:
            messages.error(request, f'Approve between $0.01 and the requested {money(p.requested_cents)}.')
            return redirect(back)
        needs_two = fs.two_person_approval and cents >= FinanceSettings.TWO_PERSON_THRESHOLD_CENTS
        if needs_two and not p.approved_by_id:
            p.approved_by, p.approved_at, p.amount_cents = request.user, timezone.now(), cents
            p.save(update_fields=['approved_by', 'approved_at', 'amount_cents'])
            _log(p, member, f'First approval: {money(cents)}', note)
            audit(member, 'First approval of payment', 'Approval', p.code, money(cents))
            messages.success(request, 'First approval recorded — a second, different approver must confirm this '
                                      'amount.')
            return redirect(back)
        if needs_two:
            if p.approved_by_id == request.user.pk:
                messages.error(request, 'A second, different approver must confirm this amount.')
                return redirect(back)
            if cents != p.amount_cents:
                messages.error(request, f'The second approval must confirm the same amount ({money(p.amount_cents)}).')
                return redirect(back)
            p.second_approved_by, p.second_approved_at = request.user, timezone.now()
        else:
            p.approved_by, p.approved_at = request.user, timezone.now()
        p.amount_cents, p.status = cents, Payout.STATUS_APPROVED
        p.save()
        _log(p, member, f'Approved {money(cents)}' + (f' of {money(p.requested_cents)} requested'
                                                     if cents != p.requested_cents else ''), note)
        audit(member, 'Approved payment', 'Approval', p.code, money(cents))
        messages.success(request, f'Approved {money(cents)} · {p.code}')
    elif action == 'reject' and p.status in Payout.OPEN:
        if not note:
            messages.error(request, 'An internal rejection reason is required.')
            return redirect(back)
        p.status, p.rejection_reason = Payout.STATUS_REJECTED, note[:200]
        p.save(update_fields=['status', 'rejection_reason'])
        _log(p, member, 'Rejected', note)
        audit(member, 'Rejected payment (confidential)', 'Approval', p.code, money(p.requested_cents))
        messages.success(request, 'Rejected confidentially. No notification was sent.')
    elif action in ('review', 'hold', 'pending') and p.status in Payout.OPEN:
        p.status = {'review': Payout.STATUS_REVIEW, 'hold': Payout.STATUS_HOLD, 'pending': Payout.STATUS_PENDING}[action]
        p.save(update_fields=['status'])
        _log(p, member, f'Moved to {p.get_status_display()}', note)
        audit(member, f'Moved payment to {p.get_status_display()}', 'Approval', p.code)
        messages.success(request, f'Moved to {p.get_status_display()}.')
    elif action == 'paid' and p.status == Payout.STATUS_APPROVED:
        ref = request.POST.get('reference', '').strip()
        if not ref:
            messages.error(request, 'A payout confirmation reference is required.')
            return redirect(back)
        p.status, p.reference, p.paid_at = Payout.STATUS_PAID, ref[:120], timezone.now()
        p.save(update_fields=['status', 'reference', 'paid_at'])
        _log(p, member, f'Paid · {ref}', note)
        InboxMessage.objects.create(user=p.author, title='Payment sent 💸',
                                    body=f'We sent you {money(p.amount_cents)}. Reference: {ref}.')
        audit(member, 'Confirmed payout', 'Payout', p.code, money(p.amount_cents))
        messages.success(request, 'Payout confirmed and recorded in the Treasury.')
    elif action == 'failed' and p.status == Payout.STATUS_APPROVED:
        p.status, p.note = Payout.STATUS_FAILED, note[:200]
        p.save(update_fields=['status', 'note'])
        _log(p, member, 'Payout failed', note)
        audit(member, 'Marked payout failed', 'Payout', p.code, money(p.amount_cents))
        messages.success(request, 'Marked as failed. The amount is back in their balance.')
    return redirect(back)


# ── Treasury ─────────────────────────────────────────────────────────────────
@finance_area
def treasury(request):
    member = request.finance_member
    if request.method == 'POST':
        require(member, 'approve_payouts')
        action = request.POST.get('action')
        if action == 'verify':
            keys = dict((k, n) for k, n, *_ in ledger.ACCOUNTS)
            key, ref = request.POST.get('account'), request.POST.get('statement_ref', '').strip()
            try:
                cents = round(float(request.POST.get('amount', '')) * 100)
                as_of = timezone.datetime.strptime(request.POST.get('as_of', ''), '%Y-%m-%d').date()
            except ValueError:
                messages.error(request, 'Enter the statement amount and date.')
                return redirect('dashboard:finance_treasury')
            if key not in keys or not ref:
                messages.error(request, 'Choose an account and add the statement reference.')
                return redirect('dashboard:finance_treasury')
            VerifiedBalance.objects.create(account=key, cents=cents, statement_ref=ref[:80], as_of=as_of,
                                           recorded_by=request.user)
            audit(member, f'Recorded verified balance: {keys[key]}', 'Reconciliation', ref[:60], money(cents))
            messages.success(request, f'{keys[key]}: verified balance recorded.')
        elif action == 'settle':
            provider = request.POST.get('provider', '')
            ref = request.POST.get('settlement_ref', '').strip()
            try:
                upto = timezone.datetime.strptime(request.POST.get('upto', ''), '%Y-%m-%d').date()
            except ValueError:
                upto = None
            if not (upto and ref):
                messages.error(request, 'Add the settlement date and the provider payout reference.')
                return redirect('dashboard:finance_treasury')
            qs = ledger.sales(end=upto + timedelta(days=1)).filter(settled_at__isnull=True)
            qs = qs.filter(provider=provider if provider != 'none' else '')
            n = qs.update(settled_at=timezone.now(), settlement_ref=ref[:80])
            audit(member, f'Recorded settlement of {n} payments', 'Reconciliation', ref[:60])
            messages.success(request, f'{n} payment{"s" if n != 1 else ""} marked settled.')
        return redirect('dashboard:finance_treasury')
    t = ledger.treasury()
    return render(request, 'dashboard/finance/treasury.html', {
        't': t, 'accounts': ledger.accounts(t), 'journal': ledger.journal(),
        'providers': Transaction.PROVIDER_CHOICES, 'can_record': member.can('approve_payouts'),
        'today': timezone.localdate(),
    })
