"""Finance › Author Income Update: approve book earnings (Profit Share,
Reward Share), award Completion Bonuses ($40) and One-time Payments, and
release Carry over into Available to Withdraw. Every change reaches the
author's Income page straight away and is audited."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.catalog.models import Story
from apps.finance.services import audit, money, require
from apps.users.models import User
from apps.wallet import income
from apps.wallet.models import AuthorEarning, IncomeRelease

from .finance import finance_area

TABS = [('profit', 'Profit Share', 'profit_share'), ('completion', 'Completion Bonus', 'completion'),
        ('onetime', 'One-Time Payment', 'one_time'), ('rewards', 'Rewards Share', 'reward_share'),
        ('available', 'Carry Over & Available to Withdraw', None)]


def _author(code):
    """AUT-12, 12 or an email → the author, or None."""
    code = (code or '').strip()
    if not code:
        return None
    digits = code.upper().removeprefix('AUT-')
    if digits.isdigit():
        return User.objects.filter(pk=int(digits)).first()
    return User.objects.filter(email__iexact=code).first()


def _story(code):
    code = (code or '').strip().upper()
    if code.startswith('BV-') and code[3:].isdigit():
        return Story.objects.filter(pk=int(code[3:]) - 1000).first()
    return None


def _cents(raw):
    try:
        return int((Decimal(str(raw).replace('$', '').replace(',', '')) * 100).quantize(Decimal(1)))
    except (InvalidOperation, ValueError):
        return 0


@finance_area
def income_update(request):
    member = request.finance_member
    tab = request.GET.get('tab', 'profit')
    if tab not in [t for t, *_ in TABS]:
        tab = 'profit'
    back = f"{reverse('dashboard:finance_income')}?{request.GET.urlencode()}"
    if request.method == 'POST':
        require(member, 'approve_payouts')
        action = request.POST.get('action')
        try:
            if action in ('approve', 'reject'):
                e = get_object_or_404(AuthorEarning, pk=request.POST.get('id'))
                income.decide(e, action == 'approve', request.user, request.POST.get('note', ''))
                audit(member, f'{"Approved" if action == "approve" else "Rejected"} {e.get_type_display()}',
                      'Approval', f'AUT-{e.author_id}', money(e.amount_cents))
                messages.success(request, f'{e.get_type_display()} {money(e.amount_cents)} '
                                          f'{"approved" if action == "approve" else "rejected"}.')
            elif action == 'approve_all':
                kind = dict((t, k) for t, _, k in TABS)[tab]
                done = 0
                for e in AuthorEarning.objects.filter(type=kind, status=AuthorEarning.STATUS_PENDING):
                    income.decide(e, True, request.user)
                    done += 1
                audit(member, f'Approved {done} pending {kind.replace("_", " ")} lines', 'Approval')
                messages.success(request, f'Approved {done} earning{"s" if done != 1 else ""}.')
            elif action == 'completion':
                story = get_object_or_404(Story, pk=request.POST.get('story'))
                e = income.award(story.author, 'completion', income.COMPLETION_BONUS_CENTS, request.user, story=story,
                                 note=f'Completion Bonus · {story.display_title}')
                audit(member, 'Updated completion bonus', 'Bonus', story.book_code, money(e.amount_cents))
                messages.success(request, f'Completion Bonus $40.00 added for "{story.display_title}".')
            elif action == 'onetime':
                author = _author(request.POST.get('author'))
                if not author:
                    raise ValidationError('No author with that Author ID.')
                story = _story(request.POST.get('book'))
                e = income.award(author, 'one_time', _cents(request.POST.get('amount')), request.user, story=story,
                                 note=request.POST.get('reason', '').strip())
                audit(member, 'Approved one-time payment', 'Bonus', f'AUT-{author.pk}', money(e.amount_cents))
                messages.success(request, f'One-Time Payment {money(e.amount_cents)} added for {author.display_name}.')
            elif action == 'release':
                author = _author(request.POST.get('author'))
                if not author:
                    raise ValidationError('No author with that Author ID.')
                r = income.release(author, _cents(request.POST.get('amount')), request.user,
                                   request.POST.get('note', ''))
                audit(member, 'Made earnings available to withdraw', 'Payout', f'AUT-{author.pk}',
                      money(r.amount_cents))
                b = income.balances(author)
                messages.success(request, f'{money(r.amount_cents)} is now available to withdraw. Carry over: '
                                          f'{money(b["carry_over"])} · Available: {money(b["available"])}.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        return redirect(back)

    ctx = {'tab': tab, 'tabs': TABS, 'q': request.GET.get('q', '').strip(),
           'pending': {k: AuthorEarning.objects.filter(type=k, status=AuthorEarning.STATUS_PENDING).count()
                       for k in ('profit_share', 'reward_share')},
           'can_act': member.can('approve_payouts')}
    if tab in ('profit', 'rewards'):
        kind = 'profit_share' if tab == 'profit' else 'reward_share'
        status = request.GET.get('status', 'pending')
        qs = AuthorEarning.objects.filter(type=kind).select_related('author', 'story', 'decided_by')
        if status in dict(AuthorEarning.STATUS_CHOICES):
            qs = qs.filter(status=status)
        author, story = _author(ctx['q']), _story(ctx['q'])
        if author:
            qs = qs.filter(author=author)
        elif story:
            qs = qs.filter(story=story)
        ctx.update(status=status, statuses=AuthorEarning.STATUS_CHOICES, kind=kind,
                   page=Paginator(qs.order_by('-period', '-amount_cents'), 30).get_page(request.GET.get('page')),
                   total=qs.aggregate(t=Sum('amount_cents'))['t'] or 0)
    elif tab == 'completion':
        has_bonus = AuthorEarning.objects.filter(story=OuterRef('pk'), type='completion').exclude(status='rejected')
        eligible = (Story.objects.filter(is_signed=True, status=Story.STATUS_COMPLETED, is_removed=False)
                    .annotate(paid=Exists(has_bonus)).filter(paid=False).select_related('author'))
        story = _story(ctx['q'])
        if story:
            eligible = eligible.filter(pk=story.pk)
        elif ctx['q']:
            eligible = eligible.filter(Q(title__icontains=ctx['q']) | Q(author__email__icontains=ctx['q']))
        ctx.update(eligible=eligible[:100], bonus=income.COMPLETION_BONUS_CENTS,
                   awarded=AuthorEarning.objects.filter(type='completion').select_related('author', 'story')[:50])
    elif tab == 'onetime':
        ctx['history'] = AuthorEarning.objects.filter(type='one_time').select_related('author', 'story',
                                                                                   'decided_by')[:100]
    else:
        author = _author(ctx['q'])
        if author:
            ctx['author'] = author
            ctx['b'] = income.balances(author)
            ctx['releases'] = IncomeRelease.objects.filter(author=author).select_related('created_by')[:50]
        # Authors with money waiting in Carry over.
        rows = []
        for u in User.objects.filter(earnings__status='approved').distinct()[:500]:
            b = income.balances(u)
            if b['carry_over'] or b['available'] or b['pending_withdrawal']:
                rows.append({'u': u, **b})
        rows.sort(key=lambda r: -r['carry_over'])
        ctx['rows'] = rows[:100]
    return render(request, 'dashboard/finance/income_update.html', ctx)
