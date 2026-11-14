"""Editorial people tools: team, authors, performance & targets,
recruitment, commission, escalations, messages and reports."""

import csv
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Chapter, Story
from apps.editorial import team
from apps.editorial.models import (BookTransfer, ContractApplication, Escalation, Recommendation, Target,
                                   TeamMessage, month_start)
from apps.engagement.models import InboxMessage, Report
from apps.users.models import User
from apps.wallet.models import AuthorEarning, Payout

from .editorial import chief_area, editor_area, visible_messages


def _month(request):
    try:
        y, m = (int(x) for x in request.GET.get('month', '').split('-'))
        return date(y, m, 1)
    except ValueError:
        return month_start()


def _months_back(n):
    d = month_start()
    out = []
    for _ in range(n):
        out.append(d)
        d = (d - timedelta(days=1)).replace(day=1)
    return out


# ── Team ─────────────────────────────────────────────────────────────────────
@chief_area
def team_view(request):
    return render(request, 'dashboard/editorial/team.html', {'rows': team.team_table(request.user), 'lv': request.level})


@chief_area
def editor_detail(request, pk):
    ed = get_object_or_404(team.team_editors(request.user), pk=pk)
    if request.method == 'POST' and request.POST.get('action') == 'desk':
        if request.POST.get('desk') in dict(User.DESK_CHOICES):
            ed.desk = request.POST['desk']
            ed.save(update_fields=['desk'])
            messages.success(request, f'{ed.display_name} is now on the {ed.get_desk_display()} desk.')
        return redirect('dashboard:editorial_editor', ed.pk)
    stats = team.editor_stats(ed)
    targets = team.targets_for(ed)
    books = team.annotate_books(Story.objects.filter(editor=ed).exclude(status=Story.STATUS_DRAFT)).order_by('-updated_at')
    for b in books:
        b.risk = team.risk_of(b.last_update, b.status)
    return render(request, 'dashboard/editorial/editor.html', {
        'ed': ed, 'stats': stats, 'progress': team.target_progress(stats, targets),
        'score': team.performance_score(stats, targets), 'books': books,
        'recruits': ed.recruits.filter(role=User.ROLE_AUTHOR)[:20], 'lv': request.level,
        'desks': User.DESK_CHOICES,
    })


# ── Authors & Accounts ───────────────────────────────────────────────────────
@editor_area
def authors(request):
    user = request.user
    pub = Q(stories__chapters__status=Chapter.STATUS_PUBLISHED)
    qs = team.scope_authors(user).annotate(
        n_books=Count('stories', distinct=True),
        n_chapters=Count('stories__chapters', filter=pub, distinct=True),
        n_words=Sum('stories__chapters__word_count', filter=pub),
        last_update=Max('stories__chapters__published_at', filter=pub),
        n_signed=Count('stories', filter=Q(stories__is_signed=True), distinct=True),
    ).select_related('recruited_by')
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(pen_name__icontains=q) | Q(email__icontains=q))
    f = request.GET.get('f', 'all')
    if f == 'contracted':
        qs = qs.filter(n_signed__gt=0)
    elif f == 'awaiting':
        qs = qs.filter(stories__applications__status__in=ContractApplication.OPEN_STATUSES).distinct()
    elif f == 'no_kyc':
        qs = qs.filter(kyc_verified=False, n_signed__gt=0)
    rows = list(qs.order_by('-last_update')[:300])
    complaints = dict(Report.objects.filter(reported_user__in=rows, is_resolved=False)
                      .values_list('reported_user').annotate(n=Count('id')))
    for a in rows:
        a.risk = team.risk_of(a.last_update, 'ongoing') if a.n_chapters else 'at_risk'
        a.complaints = complaints.get(a.pk, 0)
    if f == 'at_risk':
        rows = [a for a in rows if a.risk]
    return render(request, 'dashboard/editorial/authors.html', {'authors': rows, 'f': f, 'q': q, 'lv': request.level})


@editor_area
def author_detail(request, pk):
    author = get_object_or_404(team.scope_authors(request.user), pk=pk)
    lv = request.level
    if request.method == 'POST':
        action = request.POST.get('action')
        text = request.POST.get('text', '').strip()
        if action == 'message' and text:
            InboxMessage.objects.create(user=author, title=request.POST.get('title') or 'Message from your editor',
                                        body=text)
            messages.success(request, 'Sent to the author\'s inbox.')
        elif action == 'claim' and not author.recruited_by_id:
            author.recruited_by = request.user
            author.save(update_fields=['recruited_by'])
            messages.success(request, 'Added to your recruits.')
        elif action == 'kyc' and lv == team.CE:
            author.kyc_verified = not author.kyc_verified
            author.save(update_fields=['kyc_verified'])
            messages.success(request, 'KYC verified.' if author.kyc_verified else 'KYC cleared.')
        elif action == 'recommend_promotion':
            Recommendation.objects.create(kind='promote_author', about_user=author, note=text,
                                          recommended_by=request.user)
            messages.success(request, 'Recommendation sent to the Chief Editor.')
        return redirect('dashboard:editorial_author', author.pk)

    stories = list(team.annotate_books(author.stories.exclude(status=Story.STATUS_DRAFT)))
    for s in stories:
        s.risk = team.risk_of(s.last_update, s.status)
    today = timezone.localdate()
    per_day = dict(Chapter.objects.filter(story__author=author, status=Chapter.STATUS_PUBLISHED,
                                          published_at__date__gte=today - timedelta(days=13))
                   .values_list('published_at__date').annotate(n=Count('id')))
    days = [{'label': (today - timedelta(days=i)).strftime('%d'), 'n': per_day.get(today - timedelta(days=i), 0)}
            for i in range(13, -1, -1)]
    peak = max((d['n'] for d in days), default=0) or 1
    for d in days:
        d['pct'] = round(d['n'] * 100 / peak)
    from apps.users.models import IPLog
    ips = list(IPLog.objects.filter(user=author))
    shared = {}
    for log in IPLog.objects.filter(ip__in=[i.ip for i in ips]).exclude(user=author).select_related('user'):
        shared.setdefault(log.ip, []).append(log.user)
    for i in ips:
        i.shared = shared.get(i.ip, [])
    return render(request, 'dashboard/editorial/author.html', {
        'author': author, 'stories': stories, 'days': days, 'lv': lv, 'ips': ips,
        'complaints': Report.objects.filter(Q(reported_user=author) | Q(story__author=author)).select_related(
            'reporter', 'story')[:20],
        'apps': ContractApplication.objects.filter(story__author=author).select_related('story')[:10],
    })


# ── Team Performance & targets ───────────────────────────────────────────────
@chief_area
def performance(request):
    user, lv = request.user, request.level
    month = _month(request)
    if request.method == 'POST':
        ed = get_object_or_404(team.team_editors(user), pk=request.POST.get('editor'))
        for key, _ in Target.METRIC_CHOICES:
            raw = request.POST.get(key, '')
            if raw.isdigit():
                Target.objects.update_or_create(user=ed, month=month, metric=key,
                                                defaults={'value': int(raw), 'set_by': user})
        TeamMessage.objects.create(sender=user, recipient=ed, kind='target', title=f'Targets for {month:%B %Y}',
                                   body='Your monthly targets were updated. See Team Performance.')
        messages.success(request, f'Targets saved for {ed.display_name}.')
        return redirect(f"{request.path}?month={month:%Y-%m}")
    rows = team.team_table(user, month)
    avg = round(sum(r['books'] for r in rows) / len(rows), 1) if rows else 0
    for r in rows:  # the average sits at the middle of the bar
        r['load_pct'] = min(100, round(r['books'] * 50 / avg)) if avg else 0
    return render(request, 'dashboard/editorial/performance.html', {
        'rows': rows, 'avg_books': avg, 'month': month, 'months': _months_back(6), 'lv': lv,
        'metrics': Target.METRIC_CHOICES,
        'overloaded': [r for r in rows if r.get('overloaded')], 'underloaded': [r for r in rows if r.get('underloaded')],
    })


# ── Recruitment ──────────────────────────────────────────────────────────────
@editor_area
def recruitment(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        author = User.objects.filter(email__iexact=email).first()
        if not author:
            messages.error(request, 'No account with that email. Ask the author to sign up first.')
        elif author.recruited_by_id and author.recruited_by_id != request.user.pk:
            messages.error(request, f'Already recruited by {author.recruited_by.display_name}.')
        else:
            if author.role == User.ROLE_READER:
                author.role = User.ROLE_AUTHOR
            author.recruited_by = request.user
            author.save(update_fields=['recruited_by', 'role'])
            messages.success(request, f'{author.display_name} added to your recruits.')
        return redirect('dashboard:editorial_recruitment')
    data = team.recruitment_funnel(request.user)
    stage = request.GET.get('stage', '')
    rows = [r for r in data['rows'] if not stage or r['flags'].get(stage)]
    return render(request, 'dashboard/editorial/recruitment.html', {**data, 'rows': rows, 'stage': stage,
                                                                    'lv': request.level})


# ── Commission ───────────────────────────────────────────────────────────────
@editor_area
def commission(request):
    user = request.user
    month = _month(request)
    data = team.commission(user, month)
    history = [{'month': m, 'total': team.commission(user, m)['total']} for m in _months_back(6)]
    team_rows = [{'editor': ed, 'total': team.commission(ed, month)['total']} for ed in team.team_editors(user)]
    payouts = Payout.objects.filter(author=user)
    booked = AuthorEarning.objects.filter(author=user, type='editor_commission').aggregate(t=Sum('amount_cents'))['t'] or 0
    paid = payouts.filter(status=Payout.STATUS_PAID).aggregate(t=Sum('amount_cents'))['t'] or 0
    pending = payouts.filter(status__in=Payout.OPEN + (Payout.STATUS_APPROVED,)).aggregate(t=Sum('amount_cents'))['t'] or 0
    return render(request, 'dashboard/editorial/commission.html', {
        **data, 'history': history, 'team_rows': sorted(team_rows, key=lambda r: -r['total']),
        'payouts': payouts[:20], 'booked': booked, 'paid': paid, 'pending': pending,
        'months': _months_back(6), 'rate_pct': round(data['rate'] * 100, 1), 'lv': request.level,
    })


# ── Escalations & recommendations ───────────────────────────────────────────
def _escalations_for(user, lv):
    qs = Escalation.objects.select_related('raised_by', 'story', 'about_user', 'resolved_by')
    if lv == team.CE:
        return qs
    ids = team.team_ids(user)
    return qs.filter(Q(raised_by=user) | Q(raised_by_id__in=ids, is_confidential=False))


def _apply_recommendation(rec, by):
    story = rec.story
    if rec.kind == 'suspend_book' and story:
        story.is_removed = True
        story.save(update_fields=['is_removed', 'updated_at'])
    elif rec.kind == 'transfer_out' and story and rec.target_editor:
        BookTransfer.objects.create(story=story, from_editor=story.editor, to_editor=rec.target_editor, by=by,
                                    note='Approved transfer')
        story.editor, story.assigned_at = rec.target_editor, timezone.now()
        story.save(update_fields=['editor', 'assigned_at', 'updated_at'])
    elif rec.kind == 'promote_author' and rec.about_user:
        InboxMessage.objects.create(user=rec.about_user, title='You\'ve been recognised 🌟',
                                    body='Our editors picked you as a featured author. Keep the chapters coming!')


@editor_area
def escalations(request):
    user, lv = request.user, request.level
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'raise':
            story = team.scope_books(user).filter(pk=request.POST.get('story') or 0).first()
            Escalation.objects.create(
                raised_by=user, category=request.POST.get('category', 'other'),
                subject=request.POST.get('subject', '').strip()[:140] or 'Escalation',
                body=request.POST.get('body', '').strip() or '—', story=story,
                about_user=User.objects.filter(email__iexact=request.POST.get('about', '').strip()).first(),
                is_confidential=bool(request.POST.get('confidential')),
            )
            messages.success(request, 'Sent to the Chief Editor.')
        elif action == 'resolve' and lv == team.CE:
            esc = get_object_or_404(Escalation, pk=request.POST.get('id'))
            esc.status, esc.resolved_by, esc.resolved_at = Escalation.STATUS_RESOLVED, user, timezone.now()
            esc.resolution = request.POST.get('resolution', '')
            esc.save()
            TeamMessage.objects.create(sender=user, recipient=esc.raised_by, kind='instruction',
                                       title=f'Resolved: {esc.subject}', body=esc.resolution or 'Resolved.')
            messages.success(request, 'Escalation resolved.')
        elif action in ('approve', 'reject') and lv == team.CE:
            rec = get_object_or_404(Recommendation, pk=request.POST.get('id'), status=Recommendation.STATUS_PENDING)
            rec.status = Recommendation.STATUS_APPROVED if action == 'approve' else Recommendation.STATUS_REJECTED
            rec.decided_by, rec.decided_at = user, timezone.now()
            rec.decision_note = request.POST.get('note', '')
            rec.save()
            if action == 'approve':
                _apply_recommendation(rec, user)
            TeamMessage.objects.create(sender=user, recipient=rec.recommended_by, kind='instruction',
                                       title=f'{rec.get_kind_display()}: {rec.get_status_display()}',
                                       body=rec.decision_note or '—')
            messages.success(request, f'Recommendation {rec.get_status_display().lower()}.')
        return redirect(f"{request.path}?tab={request.POST.get('tab', 'issues')}")

    tab = request.GET.get('tab', 'issues')
    recs = Recommendation.objects.select_related('story', 'about_user', 'target_editor', 'recommended_by', 'decided_by')
    if lv != team.CE:
        recs = recs.filter(recommended_by=user)
    return render(request, 'dashboard/editorial/escalations.html', {
        'tab': tab, 'lv': lv, 'escalations': _escalations_for(user, lv)[:100], 'recs': recs[:100],
        'categories': Escalation.CATEGORY_CHOICES, 'books': team.scope_books(user).order_by('title'),
        'prefill_story': request.GET.get('story', ''), 'prefill_category': request.GET.get('category', ''),
    })


# ── Messages ─────────────────────────────────────────────────────────────────
def _recipients(user, lv):
    if lv == team.CE:
        return User.objects.filter(role=User.ROLE_SE).exclude(pk=user.pk)
    ids = [*team.team_editors(user).values_list('pk', flat=True)]
    if user.supervisor_id:
        ids.append(user.supervisor_id)
    return User.objects.filter(pk__in=ids)


@editor_area
def team_messages(request):
    user, lv = request.user, request.level
    if request.method == 'POST':
        to = request.POST.get('to', '')
        title = request.POST.get('title', '').strip()[:140]
        body = request.POST.get('body', '').strip()
        kind = request.POST.get('kind', 'message')
        if not title or not body:
            messages.error(request, 'Add a title and a message.')
        elif to == 'team':
            if lv != team.CE:
                raise PermissionDenied
            TeamMessage.objects.create(sender=user, kind=kind, title=title, body=body)
            messages.success(request, 'Announcement sent to every Senior Editor.')
        else:
            recipient = get_object_or_404(_recipients(user, lv), pk=to or 0)
            TeamMessage.objects.create(sender=user, recipient=recipient, kind=kind, title=title, body=body)
            messages.success(request, f'Sent to {recipient.display_name}.')
        return redirect('dashboard:editorial_messages')
    msgs = list(visible_messages(user)[:100])
    for m in msgs:
        m.unread = m.sender_id != user.pk and not m.read_by.filter(pk=user.pk).exists()
    for m in msgs:
        if m.unread:
            m.read_by.add(user)
    kinds = TeamMessage.KIND_CHOICES if lv == team.CE else [k for k in TeamMessage.KIND_CHOICES
                                                            if k[0] in ('message', 'feedback')]
    return render(request, 'dashboard/editorial/messages.html', {
        'msgs': msgs, 'recipients': _recipients(user, lv), 'kinds': kinds, 'lv': lv,
    })


# ── Reports ──────────────────────────────────────────────────────────────────
def _report(user, since):
    books = team.scope_books(user)
    ids = team.team_ids(user)
    recruits = User.objects.filter(role=User.ROLE_AUTHOR, date_joined__gte=since)
    if ids is not None:
        recruits = recruits.filter(recruited_by_id__in=ids)
    apps = ContractApplication.objects.filter(story__in=books, updated_at__gte=since)
    return {
        'recruited': recruits.count(),
        'contracts': apps.filter(status=ContractApplication.STATUS_SIGNED).count(),
        'book_reviews': apps.exclude(status=ContractApplication.STATUS_PENDING).count(),
        'chapter_reviews': Chapter.objects.filter(story__in=books, reviewed_at__gte=since).count(),
        'chapters_published': Chapter.objects.filter(story__in=books, published_at__gte=since).count(),
        'new_books': books.filter(created_at__gte=since).count(),
        'completed': books.filter(status=Story.STATUS_COMPLETED, updated_at__gte=since).count(),
        'at_risk': len(team.at_risk_authors(user)),
        'interventions': books.filter(needs_intervention=True).count(),
        'escalations': Escalation.objects.filter(created_at__gte=since).filter(
            Q() if ids is None else Q(raised_by_id__in=ids)).count(),
        'revenue': team.book_revenue_cents(books, since=since),
    }


REPORT_LABELS = [
    ('recruited', 'Authors recruited'), ('contracts', 'Contracts signed'), ('book_reviews', 'Book reviews done'),
    ('chapter_reviews', 'Chapter reviews done'), ('chapters_published', 'Chapters published'),
    ('new_books', 'New books'), ('completed', 'Books completed'), ('at_risk', 'Authors at risk (now)'),
    ('interventions', 'Books needing attention (now)'), ('escalations', 'Escalations raised'),
    ('revenue', 'Book revenue (coins)'),
]


@editor_area
def reports(request):
    user, lv = request.user, request.level
    period = request.GET.get('period', 'week')
    since = timezone.now() - timedelta(days=7 if period == 'week' else 30)
    mine = _report(user, since)
    rows = []
    if lv == team.CE:
        rows = [{'who': se, **_report(se, since)} for se in User.objects.filter(role=User.ROLE_SE)]
    elif lv == team.SE:
        rows = [{'who': ed, **_report(ed, since)} for ed in team.team_editors(user)]
    if request.GET.get('export') == 'csv':
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="bonovela-editorial-{period}.csv"'
        w = csv.writer(resp)
        w.writerow(['who', *(k for k, _ in REPORT_LABELS)])
        w.writerow([user.display_name + ' (scope)', *(mine[k] for k, _ in REPORT_LABELS)])
        for r in rows:
            w.writerow([r['who'].display_name, *(r[k] for k, _ in REPORT_LABELS)])
        return resp
    return render(request, 'dashboard/editorial/reports.html', {
        'period': period, 'since': since, 'mine': [(label, mine[k], k) for k, label in REPORT_LABELS],
        'rows': rows, 'labels': REPORT_LABELS, 'lv': lv,
        'challenges': team.at_risk_authors(user, limit=5),
    })
