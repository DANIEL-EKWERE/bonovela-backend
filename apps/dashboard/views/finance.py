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
from django.db.models import Count, Q, Sum
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
    """First day of the month [back] months before d's (negative = after)."""
    y, m = divmod(d.year * 12 + d.month - 1 - back, 12)
    return date(y, m + 1, 1)


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
    pending = dict(Payout.objects.filter(status__in=Payout.OPEN + (Payout.STATUS_APPROVED,)).values_list('author')
                   .annotate(t=Sum('amount_cents')))
    users = User.objects.in_bulk(list(earned))
    methods = {m.author_id: m for m in PayoutMethod.objects.filter(author__in=list(earned))}
    rows = []
    for uid, total in earned.items():
        p, q = paid.get(uid, 0), pending.get(uid, 0)
        rows.append({'user': users[uid], 'earned': total, 'paid': p, 'pending': q, 'balance': total - p - q,
                     'method': methods.get(uid)})
    return sorted(rows, key=lambda r: -r['balance'])


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
    return render(request, 'dashboard/finance/balances.html', {
        'rows': rows, 'minimum': minimum, 'due': bool(request.GET.get('due')),
        'can_approve': request.finance_member.can('approve_payouts'),
        'due_ready': [r for r in rows if r['balance'] >= minimum and r['method']],
        'due_missing': [r for r in rows if r['balance'] >= minimum and not r['method']],
    })


@finance_area
def payouts(request):
    """Old Author Payouts link: balances, or the Payment Approval Centre."""
    if request.GET.get('tab') == 'balances':
        return balances(request)
    return redirect('dashboard:finance_approvals')


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
            staff = r['user'].role in ('se', 'ce', 'marketing', 'finance')
            Payout.objects.create(
                author=r['user'], amount_cents=r['balance'], period_end=today, created_by=request.user,
                category=Payout.CAT_STAFF if staff else Payout.CAT_EARNINGS,
                method=f'{m.get_method_display()} · {m.account_name} · {m.bank_name + " · " if m.bank_name else ""}{m.account_detail}',
            )
            made += 1
    if made:
        audit(request.finance_member, 'Created payment requests', 'Payout',
              detail=f'{made} payee{"s" if made != 1 else ""}')
    messages.success(request, f'Created {made} payment request{"s" if made != 1 else ""}.' if made
                     else 'Nobody is due a payout.')
    return redirect('dashboard:finance_approvals')


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
    return redirect('dashboard:finance_approvals')


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
    ('store_fee_pct', 'App store fee', 'pct', 'Revenue'),
    ('minimum_payout_cents', 'Minimum payout', 'usd', 'Payouts'),
    ('max_withdrawal_cents', 'Maximum single withdrawal', 'usd', 'Payouts'),
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
            if kind != 'bool' and name not in request.POST:
                continue  # not on the submitted form: unchanged
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


# ── Revenue Distribution ─────────────────────────────────────────────────────
AUTHOR_INCOME = ('profit_share', 'reward_share')  # earned from what readers spend on a book


@finance_area
def revenue(request):
    """Every dollar readers spend, split between authors, Bonovela and payment processors."""
    month = _month_arg(request)
    end = _month_start(month, -1)
    fs = FinanceSettings.current()
    span = {'created_at__date__gte': month, 'created_at__date__lt': end}
    gross = _cents(Transaction.objects.filter(kind__in=PAID_KINDS, status='succeeded', **span))
    fees = int(gross * fs.processing_fee_pct / 100)
    earnings = AuthorEarning.objects.filter(type__in=AUTHOR_INCOME, **span)
    to_authors = _cents(earnings, 'amount_cents')
    net = gross - fees
    to_bonovela = net - to_authors

    # By title: coins spent on each book this month (1 coin ≈ 1¢) and what its author earned.
    spend = {}
    for t in Transaction.objects.filter(kind__in=(Transaction.KIND_UNLOCK, Transaction.KIND_GIFT),
                                        story__isnull=False, **span).values('story_id', 'coins', 'bonus_used'):
        spend[t['story_id']] = spend.get(t['story_id'], 0) + abs(t['coins']) + t['bonus_used']
    by_story = {}
    for e in earnings.filter(story__isnull=False).values('story_id', 'amount_cents'):
        by_story[e['story_id']] = by_story.get(e['story_id'], 0) + e['amount_cents']
    from apps.catalog.models import Story
    stories = Story.objects.select_related('author').in_bulk(set(spend) | set(by_story))
    titles = []
    for sid, story in stories.items():
        g = spend.get(sid, 0)
        a = by_story.get(sid, 0)
        titles.append({'story': story, 'gross': g, 'author': a,
                       'bonovela': g - a - int(g * fs.processing_fee_pct / 100)})
    titles.sort(key=lambda x: -x['gross'])
    if request.GET.get('export') == 'csv':
        return _csv(f'bonovela-revenue-{month:%Y-%m}.csv', ['title', 'author', 'gross', 'to_author', 'to_bonovela'],
                    ([r['story'].display_title, r['story'].author.display_name,
                      *(f'{r[k] / 100:.2f}' for k in ('gross', 'author', 'bonovela'))] for r in titles))
    pct = lambda part, whole: round(part * 100 / whole) if whole else 0  # noqa: E731
    today = timezone.localdate()
    return render(request, 'dashboard/finance/revenue.html', {
        'month': month, 'prev': _month_start(month, 1), 'next': end if end <= today.replace(day=1) else None,
        'gross': gross, 'fees': fees, 'net': net, 'to_authors': to_authors, 'to_bonovela': to_bonovela,
        'authors_pct': pct(to_authors, net), 'bonovela_pct': pct(to_bonovela, net), 'fee_pct': fs.processing_fee_pct,
        'share': fs.author_share_pct, 'titles': titles[:50],
        'split': [('Authors', to_authors, 'var(--f-chart-1)'), ('Bonovela', max(to_bonovela, 0), 'var(--f-chart-2)'),
                  ('Processing fees', fees, 'var(--f-chart-4)')],
    })


# ── Universal Search ─────────────────────────────────────────────────────────
SEARCH_TYPES = ['Transaction', 'Author', 'Reader', 'Payout', 'Invoice']


def _id_of(q, prefix):
    """'TX-123' / 'tx123' → 123 for that prefix, else None."""
    import re
    m = re.fullmatch(rf'{prefix}-?(\d+)', q, re.I)
    return int(m.group(1)) if m else None


@finance_area
def search(request):
    """Search every finance record by name, email, ID or amount."""
    from decimal import Decimal, InvalidOperation
    q = request.GET.get('q', '').strip()
    kind = request.GET.get('type', '')
    results = []
    if q:
        try:
            cents = int(Decimal(q.replace('$', '').replace(',', '')) * 100)
        except InvalidOperation:
            cents = None
        want = lambda t: kind in ('', t)  # noqa: E731
        if want('Transaction'):
            tq = Q(title__icontains=q) | Q(user__email__icontains=q)
            if _id_of(q, 'TX'):
                tq |= Q(pk=_id_of(q, 'TX'))
            if cents:
                tq |= Q(price_cents=cents)
            for t in Transaction.objects.filter(tq).select_related('user').order_by('-created_at')[:15]:
                results.append({'type': 'Transaction', 'title': t.title, 'when': t.created_at, 'cents': t.price_cents,
                                'meta': f'TX-{t.pk} · {t.user.email} · {t.get_kind_display()}'})
        if want('Author') or want('Reader'):
            uq = Q(email__icontains=q) | Q(pen_name__icontains=q) | Q(username__icontains=q)
            if _id_of(q, 'AU') or _id_of(q, 'R'):
                uq |= Q(pk=_id_of(q, 'AU') or _id_of(q, 'R'))
            roles = [r for r, t in ((User.ROLE_AUTHOR, 'Author'), (User.ROLE_READER, 'Reader')) if want(t)]
            for u in User.objects.filter(uq, role__in=roles)[:15]:
                if u.role == User.ROLE_AUTHOR:
                    earned = _cents(AuthorEarning.objects.filter(author=u), 'amount_cents')
                    results.append({'type': 'Author', 'title': u.display_name, 'when': u.date_joined, 'cents': earned,
                                    'meta': f'AU-{u.pk} · {u.email} · {u.stories.count()} titles · lifetime earnings'})
                else:
                    paid = Transaction.objects.filter(user=u, kind__in=PAID_KINDS, status='succeeded')
                    results.append({'type': 'Reader', 'title': u.email, 'when': u.date_joined, 'cents': _cents(paid),
                                    'meta': f'R-{u.pk} · lifetime spend · {paid.count()} purchases'})
        if want('Payout'):
            pq = Q(author__email__icontains=q) | Q(author__pen_name__icontains=q) | Q(reference__icontains=q)
            if _id_of(q, 'PO'):
                pq |= Q(pk=_id_of(q, 'PO'))
            if cents:
                pq |= Q(amount_cents=cents)
            for p in Payout.objects.filter(pq).select_related('author')[:15]:
                results.append({'type': 'Payout', 'title': f'Payout to {p.author.display_name}', 'when': p.created_at,
                                'cents': p.amount_cents, 'meta': f'PO-{p.pk} · {p.method or "—"} · {p.get_status_display()}'})
        if want('Invoice'):
            eq = Q(title__icontains=q) | Q(category__icontains=q) | Q(note__icontains=q)
            if _id_of(q, 'EX'):
                eq |= Q(pk=_id_of(q, 'EX'))
            if cents:
                eq |= Q(amount_cents=cents)
            for e in Expense.objects.filter(eq)[:15]:
                results.append({'type': 'Invoice', 'title': e.title, 'when': datetime.combine(e.date, datetime.min.time()),
                                'cents': e.amount_cents, 'meta': f'{e.code} · {e.category} · {e.get_status_display()}'})
        results.sort(key=lambda r: timezone.make_naive(r['when']) if timezone.is_aware(r['when']) else r['when'],
                     reverse=True)
    return render(request, 'dashboard/finance/search.html', {
        'q': q, 'kind': kind, 'types': SEARCH_TYPES, 'results': results,
    })


# ── Financial Reports ────────────────────────────────────────────────────────
def _pl(start, end):
    """Profit & loss figures for [start, end)."""
    span = {'created_at__date__gte': start, 'created_at__date__lt': end}
    revenue_ = _cents(Transaction.objects.filter(kind__in=PAID_KINDS, status='succeeded', **span))
    fees = int(revenue_ * FinanceSettings.current().processing_fee_pct / 100)
    authors = _cents(AuthorEarning.objects.filter(**span), 'amount_cents')
    expenses_ = _cents(Expense.objects.filter(date__gte=start, date__lt=end), 'amount_cents')
    return {'revenue': revenue_, 'fees': fees, 'authors': authors, 'expenses': expenses_,
            'profit': revenue_ - fees - authors - expenses_}


def _quarter(d, back=0):
    q = (d.month - 1) // 3 - back
    y = d.year + q // 4
    q %= 4
    start = date(y, q * 3 + 1, 1)
    return start, _month_start(start, -3), f'Q{q + 1} {y}'


@finance_area
def reports(request):
    today = timezone.localdate()
    this = today.replace(day=1)
    last = _month_start(today, 1)
    now_pl, last_pl = _pl(this, _month_start(today, -1)), _pl(last, this)
    q_start, q_end, q_label = _quarter(today, 1)
    year = today.year - 1
    report = request.GET.get('download')
    member = request.finance_member
    money2 = lambda c: f'{c / 100:.2f}'  # noqa: E731

    if report == 'pl':
        pl = _pl(last, this)
        audit(member, 'Downloaded report', 'Report', detail=f'Profit & Loss · {last:%B %Y}')
        return _csv(f'bonovela-profit-loss-{last:%Y-%m}.csv', ['line', 'usd'], [
            ('Reader revenue', money2(pl['revenue'])), ('Processing fees', money2(-pl['fees'])),
            ('Author earnings', money2(-pl['authors'])), ('Operating expenses', money2(-pl['expenses'])),
            ('Net profit', money2(pl['profit']))])
    if report == 'cash':
        audit(member, 'Downloaded report', 'Report', detail=f'Cash Flow Statement · {last:%B %Y}')
        rows = []
        for t in Transaction.objects.filter(kind__in=PAID_KINDS, status='succeeded', created_at__date__gte=last, created_at__date__lt=this):
            rows.append((t.created_at.date(), 'In', 'Reader Wallets', t.title, money2(t.price_cents)))
        for p in Payout.objects.filter(status=Payout.STATUS_PAID, paid_at__date__gte=last, paid_at__date__lt=this):
            rows.append((p.paid_at.date(), 'Out', 'Author Earnings', f'Payout PO-{p.pk} · {p.author.email}',
                         money2(-p.amount_cents)))
        for e in Expense.objects.filter(status=Expense.STATUS_PAID, date__gte=last, date__lt=this):
            rows.append((e.date, 'Out', 'Operating Expenses', f'{e.code} · {e.title}', money2(-e.amount_cents)))
        return _csv(f'bonovela-cash-flow-{last:%Y-%m}.csv', ['date', 'direction', 'account', 'description', 'usd'],
                    sorted(rows))
    if report == 'authors':
        audit(member, 'Downloaded report', 'Report', detail=f'Author Earnings Summary · {q_label}')
        rows = []
        for u in User.objects.filter(earnings__created_at__date__gte=q_start,
                                     earnings__created_at__date__lt=q_end).distinct():
            earned = _cents(AuthorEarning.objects.filter(author=u, created_at__date__gte=q_start,
                                                         created_at__date__lt=q_end), 'amount_cents')
            paid = _cents(Payout.objects.filter(author=u, status=Payout.STATUS_PAID, paid_at__date__gte=q_start,
                                                paid_at__date__lt=q_end), 'amount_cents')
            rows.append((u.email, u.display_name, money2(earned), money2(paid)))
        return _csv(f'bonovela-author-earnings-{q_label.replace(" ", "-")}.csv',
                    ['email', 'name', 'earned', 'paid'], rows)
    if report == 'tax':
        audit(member, 'Downloaded report', 'Report', detail=f'Tax Summary · {year}')
        rows = []
        for m in range(1, 13):
            start = date(year, m, 1)
            pl = _pl(start, _month_start(start, -1))
            rows.append((f'{start:%Y-%m}', *(money2(pl[k]) for k in ('revenue', 'fees', 'authors', 'expenses', 'profit'))))
        return _csv(f'bonovela-tax-summary-{year}.csv',
                    ['month', 'taxable_revenue', 'processing_fees', 'author_earnings', 'expenses', 'net'], rows)

    change = round((now_pl['revenue'] - last_pl['revenue']) * 100 / last_pl['revenue'], 1) if last_pl['revenue'] else None
    margin = round(now_pl['profit'] * 100 / now_pl['revenue']) if now_pl['revenue'] else None
    return render(request, 'dashboard/finance/reports.html', {
        'this': this, 'last': last, 'now': now_pl, 'change': change, 'margin': margin,
        'reports': [
            ('pl', f'Profit & Loss · {last:%B %Y}', 'Revenue, author payouts, expenses and net profit.'),
            ('cash', f'Cash Flow Statement · {last:%B %Y}', 'Money in and out of the Treasury by source.'),
            ('authors', f'Author Earnings Summary · {q_label}', 'What every author earned and was paid.'),
            ('tax', f'Tax Summary · {year}', 'Taxable revenue, fees, author earnings and expenses by month.'),
        ],
    })


# ── Short Story Subscriptions ────────────────────────────────────────────────
@finance_area
def subscriptions(request):
    from apps.finance.models import SubscriberRead, SubscriptionShare
    from apps.finance.services import subscription_pool
    from apps.wallet.models import Subscription
    member = request.finance_member
    month = _month_arg(request)
    pool = subscription_pool(month)
    if request.method == 'POST':
        require(member, 'approve_payouts')
        action = request.POST.get('action')
        waiting = SubscriptionShare.objects.filter(month=month, status=SubscriptionShare.STATUS_WAITING)
        shares = waiting if action == 'approve_all' else waiting.filter(pk=request.POST.get('share'))
        for share in shares.select_related('author'):
            if action == 'skip':
                share.status = SubscriptionShare.STATUS_SKIPPED
                detail = f'{share.author.email} · not paid'
            else:
                try:
                    cents = round(float(request.POST.get('amount')) * 100) if action == 'approve' else share.suggested_cents
                except (TypeError, ValueError):
                    cents = share.suggested_cents
                share.status, share.approved_cents = SubscriptionShare.STATUS_APPROVED, max(0, cents)
                if share.approved_cents:
                    AuthorEarning.objects.create(author=share.author, type='subscription_share',
                                                 amount_cents=share.approved_cents,
                                                 note=f'Short Story Subscriptions · {month:%B %Y}')
                detail = f'{share.author.email} · {money(share.approved_cents)}'
            share.decided_by, share.decided_at = request.user, timezone.now()
            share.save()
            audit(member, 'Decided subscription share', 'Subscription', f'{month:%Y-%m}', detail)
        return redirect(f"{reverse('dashboard:finance_subscriptions')}?month={month:%Y-%m}")

    shares = list(SubscriptionShare.objects.filter(month=month).select_related('author'))
    reads = SubscriberRead.objects.filter(month=month)
    for share in shares:
        mine = reads.filter(author=share.author)
        share.subscribers = mine.values('user').distinct().count()
        share.pct = round(share.reads * 100 / pool['reads'], 1) if pool['reads'] else 0
        share.titles = [(r['story__title'] or 'Unnamed', r['n']) for r in
                        mine.values('story__title').annotate(n=Count('pk')).order_by('-n')]
    if request.GET.get('export') == 'csv':
        return _csv(f'bonovela-subscription-pool-{month:%Y-%m}.csv',
                    ['author', 'email', 'chapters_read', 'share_pct', 'suggested', 'status', 'approved'],
                    ([s.author.display_name, s.author.email, s.reads, s.pct, f'{s.suggested_cents / 100:.2f}',
                      s.get_status_display(), f'{s.approved_cents / 100:.2f}'] for s in shares))
    approved = [s for s in shares if s.status == SubscriptionShare.STATUS_APPROVED]
    waiting = [s for s in shares if s.status == SubscriptionShare.STATUS_WAITING]
    tab = request.GET.get('tab', 'authors')
    today = timezone.localdate()
    end = _month_start(month, -1)
    return render(request, 'dashboard/finance/subscriptions.html', {
        'month': month, 'prev': _month_start(month, 1), 'next': end if end <= today.replace(day=1) else None,
        'pool': pool, 'shares': shares, 'tab': tab,
        'approved_cents': sum(s.approved_cents for s in approved), 'approved_n': len(approved),
        'waiting_cents': sum(s.suggested_cents for s in waiting), 'waiting_n': len(waiting),
        'skipped_cents': sum(s.suggested_cents - s.approved_cents for s in shares if s.status != 'waiting'),
        'subs': Subscription.objects.filter(starts_at__date__lt=end, ends_at__date__gte=month)
        .select_related('user', 'plan')[:200] if tab == 'subscriptions' else [],
        'can_decide': member.can('approve_payouts'),
    })


# ── Refunds & Chargebacks ────────────────────────────────────────────────────
def _reader_by_email(email):
    return User.objects.filter(email__iexact=(email or '').strip()).first()


@finance_area
def refunds(request):
    from apps.finance.models import PROCESSORS, RefundCase, SupportTicket
    member = request.finance_member
    if request.method == 'POST':
        require(member, 'approve_refunds')
        reader = _reader_by_email(request.POST.get('email'))
        try:
            cents = round(float(request.POST.get('amount') or 0) * 100)
        except ValueError:
            cents = 0
        if not reader or cents <= 0 or not request.POST.get('reason', '').strip():
            messages.error(request, 'Add the reader’s account email, the amount and a reason.')
            return redirect('dashboard:finance_refunds')
        tx_id = _id_of(request.POST.get('transaction', '').strip(), 'TX') or None
        case = RefundCase.objects.create(
            reader=reader, kind=request.POST.get('kind', 'refund'), reason=request.POST['reason'].strip()[:160],
            processor=request.POST.get('processor', 'Other'), amount_cents=cents, opened_by=request.user,
            transaction=Transaction.objects.filter(pk=tx_id, user=reader).first() if tx_id else None)
        audit(member, 'Opened case', 'Refund', case.code, f'{reader.email} · {money(cents)}')
        fs = FinanceSettings.current()
        if case.kind == RefundCase.KIND_REFUND and fs.auto_approve_small_refunds and cents < fs.SMALL_REFUND_CENTS:
            case.status, case.refunded_cents, case.decided_at = RefundCase.STATUS_REFUNDED, cents, timezone.now()
            case.resolution = 'Approved automatically (under $5).'
            case.save()
            audit(None, 'Approved refund', 'Refund', case.code, money(cents))
            messages.success(request, f'{case.code} was under $5 and was approved automatically.')
            return redirect('dashboard:finance_refunds')
        return redirect('dashboard:finance_refund_case', case.pk)
    tab = request.GET.get('tab', 'open')
    cases = RefundCase.objects.select_related('reader')
    shown = cases.filter(status=RefundCase.STATUS_OPEN) if tab == 'open' else cases.exclude(status=RefundCase.STATUS_OPEN)
    open_cases = cases.filter(status=RefundCase.STATUS_OPEN)
    return render(request, 'dashboard/finance/refunds.html', {
        'cases': shown[:200], 'tab': tab, 'open_n': open_cases.count(), 'at_risk': _cents(open_cases, 'amount_cents'),
        'issues_n': SupportTicket.objects.filter(status=SupportTicket.STATUS_OPEN).count(),
        'processors': PROCESSORS, 'kinds': RefundCase.KIND_CHOICES, 'can_act': member.can('approve_refunds'),
    })


@finance_area
def refund_case(request, pk):
    from apps.finance.models import RefundCase
    from apps.wallet import services as wallet
    from apps.wallet.models import ChapterUnlock
    member = request.finance_member
    case = get_object_or_404(RefundCase.objects.select_related('reader', 'transaction'), pk=pk)
    if request.method == 'POST' and case.status == RefundCase.STATUS_OPEN:
        require(member, 'approve_refunds')
        note = request.POST.get('note', '').strip()[:200]
        if request.POST.get('action') == 'refund':
            try:
                cents = min(case.amount_cents, round(float(request.POST.get('amount') or 0) * 100))
                coins = max(0, int(request.POST.get('coins') or 0))
            except ValueError:
                cents, coins = 0, 0
            if cents <= 0:
                messages.error(request, 'Enter how much to refund.')
                return redirect('dashboard:finance_refund_case', case.pk)
            case.coins_removed = wallet.remove_coins(case.reader, coins, f'Refund {case.code}') if coins else 0
            case.status, case.refunded_cents = RefundCase.STATUS_REFUNDED, cents
            case.resolution = note or 'Refunded.'
            audit(member, 'Approved refund', 'Refund', case.code,
                  f'{money(cents)}{f" · {case.coins_removed} coins taken back" if case.coins_removed else ""}')
            from apps.engagement.models import InboxMessage
            InboxMessage.objects.create(user=case.reader, title='Refund approved',
                                        body=f'We approved a refund of {money(cents)} ({case.code}). '
                                             f'Your store returns it to your original payment method.')
        else:
            case.status, case.resolution = RefundCase.STATUS_REJECTED, note or 'Rejected.'
            audit(member, 'Rejected refund', 'Refund', case.code, money(case.amount_cents))
        case.decided_by, case.decided_at = request.user, timezone.now()
        case.save()
        messages.success(request, f'{case.code} {case.get_status_display().lower()}.')
        return redirect('dashboard:finance_refund_case', case.pk)
    reader = case.reader
    return render(request, 'dashboard/finance/refund_case.html', {
        'case': case, 'reader': reader, 'wallet': wallet.wallet_for(reader),
        'purchases': Transaction.objects.filter(user=reader).order_by('-created_at')[:50],
        'unlocks': ChapterUnlock.objects.filter(user=reader).select_related('chapter__story').order_by('-pk')[:50],
        'can_act': member.can('approve_refunds'),
    })


# ── Reader Support ───────────────────────────────────────────────────────────
@finance_area
def support(request):
    from apps.finance.models import SupportTicket
    from apps.wallet import services as wallet
    member = request.finance_member
    if request.method == 'POST':
        require(member, 'approve_refunds')  # same people who settle reader money
        action = request.POST.get('action')
        if action == 'new':
            reader = _reader_by_email(request.POST.get('email'))
            if not reader or not request.POST.get('message', '').strip():
                messages.error(request, 'Add the reader’s account email and what they said.')
            else:
                t = SupportTicket.objects.create(
                    reader=reader, category=request.POST.get('category', 'coins'),
                    issue=request.POST.get('issue', 'not_delivered'), message=request.POST['message'].strip(),
                    payment_ref=request.POST.get('payment_ref', '').strip()[:80])
                audit(member, 'Logged support report', 'Support', t.code, reader.email)
            return redirect('dashboard:finance_support')
        t = get_object_or_404(SupportTicket.objects.select_related('reader'), pk=request.POST.get('ticket'))
        if t.status != SupportTicket.STATUS_OPEN:
            return redirect('dashboard:finance_support')
        note = request.POST.get('note', '').strip()[:200]
        if action == 'credit':
            try:
                coins = max(0, int(request.POST.get('coins') or 0))
                bonus = max(0, int(request.POST.get('bonus') or 0))
                days = max(0, int(request.POST.get('days') or 0))
            except ValueError:
                coins = bonus = days = 0
            if not (coins or bonus or days):
                messages.error(request, 'Enter coins, bonus or Plus days to credit.')
                return redirect('dashboard:finance_support')
            parts = []
            if coins:
                wallet.credit_coins(t.reader, coins, f'Support credit {t.code}')
                parts.append(f'{coins} coins')
            if bonus:
                wallet.grant_bonus(t.reader, bonus, f'Support credit {t.code}', days=30)
                parts.append(f'{bonus} bonus')
            if days:
                try:
                    wallet.extend_plus(t.reader, days)
                except ValueError as e:
                    messages.error(request, str(e))
                    return redirect('dashboard:finance_support')
                parts.append(f'{days} Plus days')
            from apps.engagement.models import InboxMessage
            InboxMessage.objects.create(user=t.reader, title='We’ve credited your account',
                                        body=f'Sorry about that. We added {", ".join(parts)} to your account ({t.code}).')
            t.resolution = note or f'Credited {", ".join(parts)}.'
            audit(member, 'Credited reader', 'Support', t.code, f'{t.reader.email} · {", ".join(parts)}')
        else:
            t.resolution = note or 'Closed without a credit.'
            audit(member, 'Closed support report', 'Support', t.code, t.reader.email)
        t.status, t.resolved_by, t.resolved_at = SupportTicket.STATUS_RESOLVED, request.user, timezone.now()
        t.save()
        messages.success(request, f'{t.code} resolved.')
        return redirect('dashboard:finance_support')
    tab = request.GET.get('tab', 'open')
    q = request.GET.get('q', '').strip()
    tickets = SupportTicket.objects.select_related('reader')
    open_t = tickets.filter(status=SupportTicket.STATUS_OPEN)
    shown = open_t if tab == 'open' else tickets.filter(status=SupportTicket.STATUS_RESOLVED)
    found = None
    if q:
        found = User.objects.filter(Q(email__iexact=q) | Q(pk=_id_of(q, 'R') or 0)).first()
    for t in shown:
        t.payments = Transaction.objects.filter(user=t.reader, kind__in=PAID_KINDS, status='succeeded').order_by('-created_at')[:3]
    return render(request, 'dashboard/finance/support.html', {
        'tickets': shown[:200], 'tab': tab, 'open_n': open_t.count(),
        'ready_n': open_t.filter(issue='not_delivered').count(), 'readers_n': open_t.values('reader').distinct().count(),
        'q': q, 'found': found,
        'found_payments': Transaction.objects.filter(user=found).order_by('-created_at')[:20] if found else [],
        'categories': SupportTicket.CATEGORY_CHOICES, 'issues': SupportTicket.ISSUE_CHOICES,
        'can_act': member.can('approve_refunds'),
    })


# ── Bonuses & Incentives ─────────────────────────────────────────────────────
@finance_area
def bonuses(request):
    from apps.finance.models import BonusAward, BonusProgram
    from apps.wallet import services as wallet
    member = request.finance_member
    if request.method == 'POST':
        require(member, 'approve_payouts')
        action = request.POST.get('action')
        if action == 'program':
            try:
                budget = round(float(request.POST.get('budget') or 0) * 100)
            except ValueError:
                budget = 0
            name = request.POST.get('name', '').strip()[:80]
            if not name or budget <= 0:
                messages.error(request, 'Give the programme a name and a monthly budget.')
            else:
                p = BonusProgram.objects.create(name=name, description=request.POST.get('description', '').strip()[:200],
                                                audience=request.POST.get('audience', 'authors'),
                                                monthly_budget_cents=budget)
                audit(member, 'Created bonus programme', 'Bonus', detail=f'{p.name} · {money(budget)}/month')
        elif action == 'award':
            program = get_object_or_404(BonusProgram, pk=request.POST.get('program'))
            recipient = _reader_by_email(request.POST.get('email'))
            try:
                cents = round(float(request.POST.get('amount') or 0) * 100)
                when = datetime.strptime(request.POST.get('date', ''), '%Y-%m-%d').date()
            except ValueError:
                cents, when = 0, None
            if not recipient or cents <= 0 or not when:
                messages.error(request, 'Add the recipient’s account email, an amount and a date.')
            else:
                a = BonusAward.objects.create(program=program, recipient=recipient, amount_cents=cents, date=when,
                                              note=request.POST.get('note', '').strip()[:160], created_by=request.user)
                audit(member, 'Scheduled bonus', 'Bonus', a.code, f'{recipient.email} · {money(cents)}')
        elif action in ('pay', 'cancel'):
            a = get_object_or_404(BonusAward.objects.select_related('program', 'recipient'), pk=request.POST.get('award'))
            if a.status == BonusAward.STATUS_SCHEDULED:
                if action == 'pay':
                    if a.program.audience == 'readers':
                        wallet.credit_coins(a.recipient, a.amount_cents, f'{a.program.name} ({a.code})')
                    else:
                        AuthorEarning.objects.create(author=a.recipient, type='others', amount_cents=a.amount_cents,
                                                     note=f'{a.program.name} ({a.code})')
                    a.status, a.paid_at = BonusAward.STATUS_PAID, timezone.now()
                    audit(member, 'Paid bonus', 'Bonus', a.code, f'{a.recipient.email} · {money(a.amount_cents)}')
                else:
                    a.status = BonusAward.STATUS_CANCELLED
                    audit(member, 'Cancelled bonus', 'Bonus', a.code, a.recipient.email)
                a.save()
        elif action == 'toggle':
            p = get_object_or_404(BonusProgram, pk=request.POST.get('program'))
            p.is_active = not p.is_active
            p.save(update_fields=['is_active'])
            audit(member, 'Paused programme' if not p.is_active else 'Resumed programme', 'Bonus', detail=p.name)
        return redirect('dashboard:finance_bonuses')
    today = timezone.localdate()
    month = today.replace(day=1)
    end = _month_start(month, -1)
    programs = list(BonusProgram.objects.all())
    for p in programs:
        used = p.awards.exclude(status=BonusAward.STATUS_CANCELLED).filter(date__gte=month, date__lt=end)
        p.used = _cents(used, 'amount_cents')
        p.pct = min(100, round(p.used * 100 / p.monthly_budget_cents)) if p.monthly_budget_cents else 0
    return render(request, 'dashboard/finance/bonuses.html', {
        'programs': programs, 'awards': BonusAward.objects.select_related('program', 'recipient')[:50],
        'active_programs': [p for p in programs if p.is_active], 'today': today,
        'audiences': BonusProgram.AUDIENCE_CHOICES, 'can_act': member.can('approve_payouts'),
    })


# ── Reconciliation ───────────────────────────────────────────────────────────
def _ledger_for(source, start, end):
    """Bonovela's own total for what a processor or bank should have paid."""
    if source in ('PayPal', 'Bank'):
        method = 'PayPal' if source == 'PayPal' else 'Bank'
        return _cents(Payout.objects.filter(status=Payout.STATUS_PAID, paid_at__date__gte=start,
                                            paid_at__date__lte=end, method__icontains=method), 'amount_cents')
    sales = _cents(Transaction.objects.filter(kind__in=PAID_KINDS, status='succeeded', created_at__date__gte=start, created_at__date__lte=end))
    return sales - int(sales * FinanceSettings.current().processing_fee_pct / 100)


@finance_area
def reconciliation(request):
    from apps.finance.models import PROCESSORS, ProcessorPayout
    member = request.finance_member
    if request.method == 'POST':
        require(member, 'approve_payouts')
        if request.POST.get('action') == 'add':
            try:
                start = datetime.strptime(request.POST.get('start', ''), '%Y-%m-%d').date()
                end = datetime.strptime(request.POST.get('end', ''), '%Y-%m-%d').date()
                cents = round(float(request.POST.get('amount') or 0) * 100)
            except ValueError:
                start = end = None
                cents = 0
            source = request.POST.get('source')
            if not start or not end or end < start or source not in dict(PROCESSORS) or not request.POST.get('reference'):
                messages.error(request, 'Add the source, its payout reference, the period and the amount.')
            else:
                ledger = _ledger_for(source, start, end)
                p = ProcessorPayout.objects.create(
                    source=source, reference=request.POST['reference'].strip()[:80], period_start=start, period_end=end,
                    processor_cents=cents, ledger_cents=ledger, created_by=request.user,
                    status=ProcessorPayout.STATUS_MATCHED if cents == ledger else ProcessorPayout.STATUS_REVIEW)
                audit(member, 'Recorded processor payout', 'Reconciliation', p.reference,
                      f'{source} · {money(cents)} vs ledger {money(ledger)}')
        else:
            p = get_object_or_404(ProcessorPayout, pk=request.POST.get('payout'))
            note = request.POST.get('note', '').strip()[:200]
            if p.status == ProcessorPayout.STATUS_REVIEW and note:
                p.status, p.resolution = ProcessorPayout.STATUS_RESOLVED, note
                p.save(update_fields=['status', 'resolution'])
                audit(member, 'Resolved difference', 'Reconciliation', p.reference, f'{money(p.difference)} · {note}')
            else:
                messages.error(request, 'Explain the difference to resolve it.')
        return redirect('dashboard:finance_reconciliation')
    rows = list(ProcessorPayout.objects.all()[:200])
    review = [r for r in rows if r.status == ProcessorPayout.STATUS_REVIEW]
    return render(request, 'dashboard/finance/reconciliation.html', {
        'rows': rows, 'matched_n': sum(r.status != ProcessorPayout.STATUS_REVIEW for r in rows), 'total_n': len(rows),
        'review_n': len(review), 'unexplained': sum(abs(r.difference) for r in review),
        'processors': PROCESSORS, 'today': timezone.localdate(), 'can_act': member.can('approve_payouts'),
    })
