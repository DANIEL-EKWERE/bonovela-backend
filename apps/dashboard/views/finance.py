"""Bonovela Finance: treasury, payouts, expenses, settings, audit log and
team, plus revenue, reports and the money desks. Amounts are USD cents;
coins are 1¢ each."""

import csv
from datetime import date, datetime, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.finance.models import AuditEntry, Expense, FinanceMember, FinanceSettings
from apps.finance.services import audit, member_for, money, require
from apps.users.models import User
from apps.wallet.models import AuthorEarning, Payout, PayoutMethod, Transaction

PAID_KINDS = (Transaction.KIND_TOP_UP, Transaction.KIND_SUBSCRIPTION)


def finance_area(view):
    """Only people on the finance team (Team & Permissions) get in; platform
    admins are Management. Sets request.finance_member."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        member = member_for(request.user)
        if not member:
            raise PermissionDenied
        FinanceMember.objects.filter(pk=member.pk).update(last_active=timezone.now())
        request.finance_member = member
        return view(request, *args, **kwargs)

    return wrapper


def _minimum():
    return FinanceSettings.current().minimum_payout_cents


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
    """Bonovela Treasury: what is held, what can be released, what is owed."""
    now, today = timezone.now(), timezone.localdate()
    fs = FinanceSettings.current()
    sales = Transaction.objects.filter(kind__in=PAID_KINDS)
    paid_payouts = Payout.objects.filter(status=Payout.STATUS_PAID)
    paid_expenses = Expense.objects.filter(status=Expense.STATUS_PAID)
    money_in = _cents(sales)
    money_out = _cents(paid_payouts, 'amount_cents') + _cents(paid_expenses, 'amount_cents')
    held = money_in - money_out
    pending_payouts = _cents(Payout.objects.filter(status=Payout.STATUS_PENDING), 'amount_cents')
    bills_due = _cents(Expense.objects.filter(status=Expense.STATUS_DUE), 'amount_cents')
    recent_sales = _cents(sales.filter(created_at__gte=now - timedelta(days=90)))
    reserved = int(recent_sales * fs.chargeback_reserve_pct / 100)
    pending = pending_payouts + bills_due

    months = []
    for back in range(5, -1, -1):
        start, end = _month_start(today, back), _month_start(today, back - 1)
        span = {'created_at__date__gte': start, 'created_at__date__lt': end}
        out = (_cents(paid_payouts.filter(paid_at__date__gte=start, paid_at__date__lt=end), 'amount_cents')
               + _cents(paid_expenses.filter(date__gte=start, date__lt=end), 'amount_cents'))
        months.append({'label': start.strftime('%b'), 'in': _cents(sales.filter(**span)), 'out': out})
    peak = max([m['in'] for m in months] + [m['out'] for m in months] + [1])
    for m in months:
        m['in_pct'], m['out_pct'] = round(m['in'] * 100 / peak), round(m['out'] * 100 / peak)

    ledger = []
    for t in sales.select_related('user').order_by('-created_at')[:10]:
        ledger.append({'when': t.created_at, 'title': t.title, 'code': f'TX-{t.pk}', 'account': 'Reader Wallets',
                       'cents': t.price_cents, 'status': 'settled', 'timed': True})
    for p in Payout.objects.select_related('author').order_by('-created_at')[:10]:
        ledger.append({'when': p.paid_at or p.created_at, 'title': f'Author payout — {p.author.display_name}',
                       'code': f'PO-{p.pk}', 'account': 'Author Earnings', 'cents': -p.amount_cents,
                       'status': {'paid': 'settled', 'pending': 'pending', 'failed': 'failed'}[p.status], 'timed': True})
    for e in Expense.objects.order_by('-date', '-pk')[:10]:
        ledger.append({'when': e.paid_at or datetime.combine(e.date, datetime.min.time(), tzinfo=now.tzinfo),
                       'title': e.title, 'code': e.code, 'account': 'Operating Expenses', 'cents': -e.amount_cents,
                       'status': 'settled' if e.status == Expense.STATUS_PAID else 'pending', 'timed': bool(e.paid_at)})
    ledger.sort(key=lambda x: x['when'], reverse=True)

    return render(request, 'dashboard/finance/treasury.html', {
        'held': held, 'settled': max(0, held - pending - reserved), 'pending': pending, 'reserved': reserved,
        'pending_payouts': pending_payouts, 'bills_due': bills_due, 'reserve_pct': fs.chargeback_reserve_pct,
        'months': months, 'ledger': ledger[:12],
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
    minimum = _minimum()
    if request.GET.get('due'):
        rows = [r for r in rows if r['balance'] >= minimum]
    if request.GET.get('export') == 'csv':
        return _csv('bonovela-balances.csv', ['email', 'name', 'earned', 'paid', 'pending', 'balance', 'method'],
                    ([r['user'].email, r['user'].display_name, *(f'{r[k] / 100:.2f}' for k in
                      ('earned', 'paid', 'pending', 'balance')), r['method'].get_method_display() if r['method'] else '']
                     for r in rows))
    return render(request, 'dashboard/finance/payouts.html', {
        'rows': rows, 'minimum': minimum, 'due': bool(request.GET.get('due')), 'tab': 'balances',
        'statuses': Payout.STATUS_CHOICES, 'can_approve': request.finance_member.can('approve_payouts'),
        'due_ready': [r for r in rows if r['balance'] >= minimum and r['method']],
        'due_missing': [r for r in rows if r['balance'] >= minimum and not r['method']],
    })


@finance_area
def payouts(request):
    """Author Payouts: pending / paid / failed payouts, and every balance."""
    tab = request.GET.get('tab', Payout.STATUS_PENDING)
    if tab == 'balances':
        return balances(request)
    qs = Payout.objects.filter(status=tab).select_related('author', 'created_by')
    if request.GET.get('export') == 'csv':
        return _csv(f'bonovela-payouts-{tab}.csv', ['id', 'email', 'name', 'usd', 'period_end', 'method', 'reference'],
                    ([p.pk, p.author.email, p.author.display_name, f'{p.amount_cents / 100:.2f}', p.period_end,
                      p.method, p.reference] for p in qs))
    minimum = _minimum()
    due = [r for r in _balances() if r['balance'] >= minimum]
    return render(request, 'dashboard/finance/payouts.html', {
        'payouts': qs, 'tab': tab, 'total': _cents(qs, 'amount_cents'), 'statuses': Payout.STATUS_CHOICES,
        'due_ready': [r for r in due if r['method']], 'due_missing': [r for r in due if not r['method']],
        'minimum': minimum, 'can_approve': request.finance_member.can('approve_payouts'),
    })


@finance_area
@require_POST
@transaction.atomic
def payout_generate(request):
    require(request.finance_member, 'approve_payouts')
    minimum = _minimum()
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
    if made:
        audit(request.finance_member, 'Created payouts', 'Payout', detail=f'{made} author{"s" if made != 1 else ""}')
    messages.success(request, f'Created {made} payout{"s" if made != 1 else ""}.' if made else 'Nobody is due a payout.')
    return redirect('dashboard:finance_payouts')


@finance_area
@require_POST
def book_commission(request):
    """Record last month's editorial commission as earnings (idempotent)."""
    require(request.finance_member, 'approve_payouts')
    from apps.editorial.team import book_commissions
    last_month = (timezone.localdate().replace(day=1) - timedelta(days=1)).replace(day=1)
    made = book_commissions(last_month)
    messages.success(request, f'Booked {made} commission line{"s" if made != 1 else ""} for {last_month:%B %Y}.'
                     if made else f'{last_month:%B %Y} commission is already booked.')
    return redirect('dashboard:finance_payouts')


@finance_area
@require_POST
def payout_action(request, pk):
    require(request.finance_member, 'approve_payouts')
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
        audit(request.finance_member, 'Approved payout', 'Approval', f'PO-{payout.pk}', money(payout.amount_cents))
        messages.success(request, f'Marked {payout.author.display_name} as paid.')
    elif action == 'failed' and payout.status == Payout.STATUS_PENDING:
        payout.status = Payout.STATUS_FAILED
        payout.note = request.POST.get('note', '')[:200]
        payout.save(update_fields=['status', 'note'])
        audit(request.finance_member, 'Marked payout failed', 'Payout', f'PO-{payout.pk}', money(payout.amount_cents))
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


# ── Expenses ─────────────────────────────────────────────────────────────────
def _month_arg(request):
    today = timezone.localdate()
    try:
        y, m = (int(x) for x in request.GET.get('month', '').split('-'))
        return date(y, m, 1)
    except ValueError:
        return today.replace(day=1)


@finance_area
def expenses(request):
    member = request.finance_member
    month = _month_arg(request)
    if request.method == 'POST':
        require(member, 'approve_payouts')  # Management and Finance Lead manage spending
        action = request.POST.get('action')
        if action == 'add':
            try:
                cents = round(float(request.POST.get('amount') or 0) * 100)
                when = datetime.strptime(request.POST.get('date', ''), '%Y-%m-%d').date()
            except ValueError:
                cents, when = 0, None
            title = request.POST.get('title', '').strip()[:120]
            category = request.POST.get('category')
            if not title or cents <= 0 or not when or category not in dict(Expense.CATEGORY_CHOICES):
                messages.error(request, 'Add a vendor, category, amount and date.')
            else:
                paid = request.POST.get('status') == Expense.STATUS_PAID
                e = Expense.objects.create(title=title, category=category, amount_cents=cents, date=when,
                                           status=Expense.STATUS_PAID if paid else Expense.STATUS_DUE,
                                           paid_at=timezone.now() if paid else None,
                                           note=request.POST.get('note', '').strip()[:200], created_by=request.user)
                audit(member, 'Added expense', 'Expense', e.code, f'{e.title} · {money(cents)}')
                messages.success(request, f'Added {e.title}.')
                month = when.replace(day=1)
        else:
            e = get_object_or_404(Expense, pk=request.POST.get('expense'))
            if action == 'paid' and e.status == Expense.STATUS_DUE:
                e.status, e.paid_at = Expense.STATUS_PAID, timezone.now()
                e.save(update_fields=['status', 'paid_at'])
                audit(member, 'Paid expense', 'Expense', e.code, f'{e.title} · {money(e.amount_cents)}')
                messages.success(request, f'{e.title} marked paid.')
            elif action == 'delete':
                audit(member, 'Deleted expense', 'Expense', e.code, f'{e.title} · {money(e.amount_cents)}')
                e.delete()
                messages.success(request, 'Expense deleted.')
        return redirect(f"{reverse('dashboard:finance_expenses')}?month={month:%Y-%m}")

    end = _month_start(month, -1)
    qs = Expense.objects.filter(date__gte=month, date__lt=end)
    if request.GET.get('export') == 'csv':
        return _csv(f'bonovela-expenses-{month:%Y-%m}.csv', ['id', 'date', 'vendor', 'category', 'status', 'usd'],
                    ([e.code, e.date, e.title, e.category, e.status, f'{e.amount_cents / 100:.2f}'] for e in qs))
    by_cat = {}
    for e in qs:
        by_cat[e.category] = by_cat.get(e.category, 0) + e.amount_cents
    peak = max(by_cat.values(), default=1)
    cats = [{'name': k, 'cents': v, 'pct': round(v * 100 / peak)} for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]
    today = timezone.localdate()
    return render(request, 'dashboard/finance/expenses.html', {
        'expenses': qs, 'month': month, 'total': _cents(qs, 'amount_cents'),
        'due': _cents(qs.filter(status=Expense.STATUS_DUE), 'amount_cents'), 'cats': cats,
        'biggest': cats[0]['name'] if cats else '—', 'categories': Expense.CATEGORY_CHOICES,
        'prev': _month_start(month, 1), 'next': end if end <= today.replace(day=1) else None, 'today': today,
        'can_edit': request.finance_member.can('approve_payouts'),
    })


# ── Finance Settings ─────────────────────────────────────────────────────────
SETTING_FIELDS = [
    # name, label, kind (pct | usd | bool), group
    ('author_share_pct', 'Author share', 'pct', 'Revenue'),
    ('processing_fee_pct', 'Processing fee', 'pct', 'Revenue'),
    ('minimum_payout_cents', 'Minimum payout', 'usd', 'Payouts'),
    ('chargeback_reserve_pct', 'Chargeback reserve', 'pct', 'Payouts'),
    ('weekly_payouts', 'Weekly payouts', 'bool', 'Payouts'),
    ('two_person_approval', 'Two-person approval', 'bool', 'Approvals'),
    ('auto_approve_small_refunds', 'Auto-approve small refunds', 'bool', 'Approvals'),
]


def _show(kind, value):
    if kind == 'pct':
        return f'{float(value):g}%'
    if kind == 'usd':
        return f'${value / 100:,.0f}'
    return 'on' if value else 'off'


@finance_area
def finance_settings(request):
    fs = FinanceSettings.current()
    member = request.finance_member
    if request.method == 'POST':
        require(member, 'change_settings')
        changes, errors = [], []
        for name, label, kind, _ in SETTING_FIELDS:
            old = getattr(fs, name)
            raw = request.POST.get(name, '')
            try:
                if kind == 'bool':
                    new = raw == 'on'
                elif kind == 'usd':
                    new = round(float(raw) * 100)
                    if new < 0:
                        raise ValueError
                else:
                    from decimal import Decimal
                    new = Decimal(raw)
                    if not 0 <= new <= 100:
                        raise ValueError
            except (ValueError, ArithmeticError):
                errors.append(label)
                continue
            if new != old:
                setattr(fs, name, new)
                changes.append((label, _show(kind, old), _show(kind, new)))
        if errors:
            messages.error(request, f"Check {', '.join(errors)}: percentages are 0–100 and amounts can't be negative.")
            return redirect('dashboard:finance_settings')
        fs.save()
        for label, old, new in changes:
            audit(member, f'Changed {label.lower()}', 'Setting', detail=f'{old} → {new}')
        messages.success(request, f"Saved {len(changes)} change{'s' if len(changes) != 1 else ''}." if changes
                         else 'Nothing changed.')
        return redirect('dashboard:finance_settings')
    groups = {}
    for name, label, kind, group in SETTING_FIELDS:
        field = FinanceSettings._meta.get_field(name)
        value = getattr(fs, name)
        groups.setdefault(group, []).append({
            'name': name, 'label': label, 'kind': kind, 'help': field.help_text,
            'value': value / 100 if kind == 'usd' else value,
        })
    return render(request, 'dashboard/finance/settings.html', {
        'groups': groups, 'can_edit': member.can('change_settings'),
    })


# ── Audit Logs ───────────────────────────────────────────────────────────────
@finance_area
def audit_log(request):
    qs = AuditEntry.objects.select_related('actor')
    category = request.GET.get('category', '')
    q = request.GET.get('q', '').strip()
    if category:
        qs = qs.filter(category=category)
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(reference__icontains=q) | Q(detail__icontains=q)
                       | Q(actor_label__icontains=q))
    if request.GET.get('export') == 'csv':
        return _csv('bonovela-audit-log.csv', ['id', 'when', 'who', 'action', 'category', 'reference', 'detail'],
                    ([e.code, e.created_at.isoformat(), e.actor_label, e.action, e.category, e.reference, e.detail]
                     for e in qs.iterator()))
    return render(request, 'dashboard/finance/audit.html', {
        'entries': qs[:300], 'categories': AuditEntry.CATEGORY_CHOICES, 'category': category, 'q': q,
    })


# ── Team & Permissions ───────────────────────────────────────────────────────
@finance_area
def team(request):
    member = request.finance_member
    if request.method == 'POST':
        require(member, 'manage_team')
        action = request.POST.get('action')
        if action == 'add':
            email = request.POST.get('email', '').strip().lower()
            role = request.POST.get('role')
            user = User.objects.filter(email=email).first()
            if role not in dict(FinanceMember.ROLE_CHOICES):
                messages.error(request, 'Choose a role.')
            elif role == FinanceMember.ROLE_MANAGEMENT and member.role != FinanceMember.ROLE_MANAGEMENT:
                messages.error(request, 'Only Management can add Management.')
            elif not user:
                messages.error(request, f'No Bonovela account uses {email}. Ask them to sign up first, then add them.')
            elif FinanceMember.objects.filter(user=user).exists():
                messages.error(request, f'{user.display_name} is already on the team.')
            else:
                FinanceMember.objects.create(user=user, role=role, added_by=request.user)
                if user.role in (User.ROLE_READER, User.ROLE_AUTHOR):
                    user.role = User.ROLE_FINANCE
                    user.save(update_fields=['role'])
                audit(member, 'Added team member', 'Access', detail=f'{email} · {dict(FinanceMember.ROLE_CHOICES)[role]}')
                messages.success(request, f'{user.display_name} can now open Bonovela Finance.')
        else:
            target = get_object_or_404(FinanceMember.objects.select_related('user'), pk=request.POST.get('member'))
            if target.pk == member.pk:
                messages.error(request, "You can't change your own access.")
            elif target.role == FinanceMember.ROLE_MANAGEMENT and member.role != FinanceMember.ROLE_MANAGEMENT:
                messages.error(request, 'Only Management can change Management.')
            elif action == 'role' and request.POST.get('role') in dict(FinanceMember.ROLE_CHOICES):
                old = target.get_role_display()
                target.role = request.POST['role']
                target.save(update_fields=['role'])
                audit(member, 'Changed role', 'Access', detail=f'{target.user.email} · {old} → {target.get_role_display()}')
                messages.success(request, f'{target.user.display_name} is now {target.get_role_display()}.')
            elif action == 'remove':
                audit(member, 'Removed team member', 'Access', detail=target.user.email)
                if target.user.role == User.ROLE_FINANCE:
                    User.objects.filter(pk=target.user_id).update(role=User.ROLE_READER)
                target.delete()
                messages.success(request, f'{target.user.display_name} no longer has access.')
        return redirect('dashboard:finance_team')
    perms = [(label, [r in roles for r, _ in FinanceMember.ROLE_CHOICES])
             for label, roles in FinanceMember.PERMISSIONS.values()]
    return render(request, 'dashboard/finance/team.html', {
        'members': FinanceMember.objects.select_related('user'), 'roles': FinanceMember.ROLE_CHOICES,
        'perms': perms, 'can_manage': member.can('manage_team'), 'me': member,
    })
