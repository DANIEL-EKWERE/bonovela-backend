"""Chief Editor workspace: contracts, App Publishing Control, payment
detail changes, duplicate accounts, IP monitoring, complaints & appeals,
discovery intelligence and the Restore Centre. Chief Editor and admins
only; every decision notifies the author."""

from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.catalog.models import Chapter, Story
from apps.editorial import services, team
from apps.editorial.models import AuthorAppeal, ContractApplication as App, ContractTemplate, PayToReadApplication
from apps.editorial.models import Promotion, ShelfBook
from apps.engagement.models import InboxMessage, ReadingProgress, Report
from apps.users.ids import user_q
from apps.users.models import IPLog, User
from apps.wallet.models import AuthorEarning, PayoutMethod, PayoutMethodChange, Transaction

from .editorial import chief_area, run


def _back(request, **extra):
    q = request.GET.copy()
    for k, v in extra.items():
        q[k] = v
    return redirect(f'{request.path}?{q.urlencode()}')


# ── Contract Management ──────────────────────────────────────────────────────
@chief_area
def contracts(request):
    tab = request.GET.get('tab', 'send')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'send':
            app = get_object_or_404(App, pk=request.POST.get('application'))
            run(request, services.ce_send_contract, app, request.user, request.POST.get('contract_type', 'non_exclusive'),
                ok='Contract sent — awaiting the author\'s signature. The author was emailed.')
        elif action == 'countersign':
            app = get_object_or_404(App, pk=request.POST.get('application'))
            run(request, services.countersign, app, request.user,
                ok='Signed for Bonovela — the book is now signed. Publish it from App Publishing Control.')
        elif action == 'template':
            run(request, services.update_contract_template, request.user, request.POST.get('body', ''),
                ok='Contract template saved as a new version.')
        return _back(request)
    tabs = [('send', 'To send', App.objects.filter(status__in=[App.STATUS_SE_APPROVED, App.STATUS_PENDING])),
            ('sent', 'Contract sent · awaiting signature', App.objects.filter(status=App.STATUS_CONTRACT_SENT)),
            ('countersign', 'Signed by author · sign for Bonovela', App.objects.filter(status=App.STATUS_AUTHOR_SIGNED)),
            ('signed', 'Signed', App.objects.filter(status=App.STATUS_SIGNED))]
    rows = next((qs for k, _, qs in tabs if k == tab), None)
    template = ContractTemplate.current()
    sample = Story.objects.filter(is_signed=True).first()
    return render(request, 'dashboard/editorial/ce_contracts.html', {
        'tab': tab, 'tabs': [(k, label, qs.count()) for k, label, qs in tabs],
        'rows': rows.select_related('story', 'story__author', 'se', 'ce').order_by('status', '-updated_at')[:200]
        if rows is not None else [],
        'template': template, 'versions': ContractTemplate.objects.select_related('updated_by')[:10],
        'preview': template.render(sample) if sample else template.body,
        'contract_types': App.CONTRACT_CHOICES,
    })


# ── App Publishing Control ───────────────────────────────────────────────────
@chief_area
def publishing(request):
    if request.method == 'POST':
        story = get_object_or_404(Story, pk=request.POST.get('story'))
        publish = request.POST.get('action') == 'publish'
        run(request, services.publish_in_app, request.user, story, publish,
            ok=f'"{story.display_title}" is {"now in the app" if publish else "no longer in the app"}.')
        return _back(request)
    tab = request.GET.get('tab', 'ready')
    signed = Story.objects.filter(is_signed=True, is_removed=False).select_related('author', 'genre', 'editor')
    tabs = [('ready', 'Ready to publish', signed.filter(in_app=False)), ('live', 'In the app', signed.filter(in_app=True))]
    book = None
    if request.GET.get('book', '').isdigit():
        book = get_object_or_404(Story.objects.select_related('author', 'genre', 'editor', 'in_app_by'),
                                 pk=request.GET['book'])
        chapters = list(book.chapters.exclude(status=Chapter.STATUS_TRASH).order_by('number'))
        published = [c for c in chapters if c.status == Chapter.STATUS_PUBLISHED]
        book.info = {
            'chapters': chapters, 'published': len(published), 'words': sum(c.word_count for c in published),
            'application': book.applications.select_related('se', 'ce').first(),
            'pay_to_read': book.pay_to_read_applications.first(),
            'readers': ReadingProgress.objects.filter(story=book).count(),
            'coins': -(Transaction.objects.filter(story=book, kind__in=['unlock', 'gift']).aggregate(t=Sum('coins'))['t'] or 0),
            'min_live': settings.BONOVELA['MIN_LIVE_CHAPTERS'],
            'tags': list(book.tags.values_list('name', flat=True)),
        }
    rows = next(qs for k, _, qs in tabs if k == tab) if tab in dict((k, 1) for k, *_ in tabs) else tabs[0][2]
    return render(request, 'dashboard/editorial/ce_publishing.html', {
        'tab': tab, 'tabs': [(k, label, qs.count()) for k, label, qs in tabs], 'rows': rows.order_by('-signed_at')[:200],
        'book': book,
    })


# ── Payment detail changes ───────────────────────────────────────────────────
LABELS = {'method': 'Payment method', 'country': 'Account country', 'currency': 'Currency', 'bank_name': 'Bank',
          'swift_code': 'SWIFT / BIC', 'account_detail': 'Account number / PayPal email', 'account_name': 'Account holder',
          'intermediary_swift': 'Intermediary SWIFT', 'nickname': 'Nickname', 'email': 'Email', 'address': 'Address',
          'city': 'City', 'state': 'State / Province', 'postal_code': 'Postal code'}


@chief_area
def payment_changes(request):
    if request.method == 'POST':
        change = get_object_or_404(PayoutMethodChange, pk=request.POST.get('change'))
        approve = request.POST.get('action') == 'approve'
        run(request, services.decide_payout_change, request.user, change, approve, request.POST.get('note', ''),
            ok='Approved — the new details are now used for payments.' if approve else 'Rejected.')
        return _back(request)
    tab = request.GET.get('tab', 'pending')
    qs = PayoutMethodChange.objects.select_related('author', 'decided_by')
    current = None
    if request.GET.get('id', '').isdigit():
        current = get_object_or_404(qs, pk=request.GET['id'])
        current.rows = [{'label': label, 'old': current.previous.get(k, ''), 'new': current.data.get(k, ''),
                         'changed': (current.previous.get(k) or '') != (current.data.get(k) or '')}
                        for k, label in LABELS.items()]
    return render(request, 'dashboard/editorial/ce_payment_changes.html', {
        'tab': tab, 'rows': qs.filter(status=tab) if tab in dict(PayoutMethodChange.STATUS_CHOICES) else qs,
        'counts': {k: qs.filter(status=k).count() for k, _ in PayoutMethodChange.STATUS_CHOICES},
        'statuses': PayoutMethodChange.STATUS_CHOICES, 'current': current,
    })


# ── Duplicate accounts & IP monitoring ───────────────────────────────────────
def _shared_ips():
    """IP → users, for IPs used by more than one author or editor account."""
    shared = (IPLog.objects.values('ip').annotate(n=Count('user', distinct=True)).filter(n__gt=1).values('ip'))
    groups = defaultdict(list)
    for log in IPLog.objects.filter(ip__in=shared).select_related('user').order_by('ip', '-last_seen'):
        groups[log.ip].append(log)
    return groups


@chief_area
def duplicate_accounts(request):
    groups = []
    for ip, logs in _shared_ips().items():
        users = {log.user for log in logs}
        authors = [u for u in users if u.role == User.ROLE_AUTHOR or u.stories.exists()]
        if len(authors) > 1:
            groups.append({'reason': f'Shared IP address {ip}', 'users': sorted(authors, key=lambda u: u.pk),
                           'last_seen': max(log.last_seen for log in logs)})
    # The same legal name on contract applications from different accounts.
    names = defaultdict(set)
    for first, surname, author_id in App.objects.exclude(first_name='').values_list(
            'first_name', 'surname', 'story__author'):
        names[f'{first} {surname}'.strip().lower()].add(author_id)
    for name, ids in names.items():
        if len(ids) > 1:
            groups.append({'reason': f'Same legal name on contract applications: {name.title()}',
                           'users': list(User.objects.filter(pk__in=ids)), 'last_seen': None})
    for g in groups:
        g['books'] = Story.objects.filter(author__in=g['users']).values_list('title', flat=True)[:8]
    return render(request, 'dashboard/editorial/ce_duplicates.html', {'groups': groups})


@chief_area
def ip_monitoring(request):
    q = request.GET.get('q', '').strip()
    logs = IPLog.objects.select_related('user')
    if q:
        logs = logs.filter(Q(ip__icontains=q) | user_q(q, 'user__'))
    shared = {ip for ip in IPLog.objects.values('ip').annotate(n=Count('user', distinct=True))
              .filter(n__gt=1).values_list('ip', flat=True)}
    return render(request, 'dashboard/editorial/ce_ip.html', {'logs': logs[:300], 'q': q, 'shared': shared})


# ── Complaints & Appeals ─────────────────────────────────────────────────────
@chief_area
def complaints(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'resolve':
            Report.objects.filter(pk=request.POST.get('report')).update(is_resolved=True)
            messages.success(request, 'Complaint marked resolved.')
        elif action in ('uphold', 'dismiss'):
            appeal = get_object_or_404(AuthorAppeal, pk=request.POST.get('appeal'), status=AuthorAppeal.STATUS_OPEN)
            appeal.status = AuthorAppeal.STATUS_UPHELD if action == 'uphold' else AuthorAppeal.STATUS_DISMISSED
            appeal.response = request.POST.get('response', '').strip()
            appeal.decided_by, appeal.decided_at = request.user, timezone.now()
            appeal.save()
            InboxMessage.objects.create(user=appeal.author, story=appeal.story,
                                        title=f'Your appeal was {appeal.get_status_display().lower()}',
                                        body=appeal.response or f'Your appeal "{appeal.subject}" has been reviewed.')
            messages.success(request, 'Appeal decided; the author was notified.')
        return _back(request)
    tab = request.GET.get('tab', 'complaints')
    return render(request, 'dashboard/editorial/ce_complaints.html', {
        'tab': tab,
        'reports': Report.objects.select_related('reporter', 'story', 'reported_user')[:200],
        'appeals': AuthorAppeal.objects.select_related('author', 'story', 'decided_by')[:200],
        'open_reports': Report.objects.filter(is_resolved=False).count(),
        'open_appeals': AuthorAppeal.objects.filter(status=AuthorAppeal.STATUS_OPEN).count(),
    })


# ── Discovery Intelligence ───────────────────────────────────────────────────
@chief_area
def discovery_intelligence(request):
    since = timezone.now() - timedelta(days=30)
    books = Story.objects.visible_in_app()
    promoted_ids = set(Promotion.objects.filter(ends_at__gt=since).values_list('story', flat=True))
    shelved_ids = set(ShelfBook.objects.filter(status=ShelfBook.STATUS_APPROVED).values_list('story', flat=True))
    readers = dict(ReadingProgress.objects.filter(updated_at__gte=since).values_list('story').annotate(n=Count('user')))
    coins = dict(Transaction.objects.filter(created_at__gte=since, kind__in=['unlock', 'gift'], story__isnull=False)
                 .values_list('story').annotate(t=Sum('coins')))
    rows = []
    for b in books.select_related('author', 'genre'):
        rows.append({'b': b, 'readers': readers.get(b.pk, 0), 'coins': -(coins.get(b.pk) or 0),
                     'promoted': b.pk in promoted_ids, 'shelved': b.pk in shelved_ids})
    rows.sort(key=lambda r: (-r['readers'], -r['coins']))

    def avg(items, key):
        items = list(items)
        return round(sum(r[key] for r in items) / len(items), 1) if items else 0

    summary = {
        'promoted_readers': avg((r for r in rows if r['promoted']), 'readers'),
        'other_readers': avg((r for r in rows if not r['promoted']), 'readers'),
        'shelved_readers': avg((r for r in rows if r['shelved']), 'readers'),
        'unexposed': [r for r in rows if not r['promoted'] and not r['shelved'] and r['readers']][:10],
    }
    by_genre = defaultdict(lambda: {'books': 0, 'readers': 0, 'coins': 0})
    for r in rows:
        g = by_genre[r['b'].genre.name if r['b'].genre else 'No genre']
        g['books'] += 1
        g['readers'] += r['readers']
        g['coins'] += r['coins']
    return render(request, 'dashboard/editorial/ce_discovery.html', {
        'rows': rows[:50], 's': summary,
        'genres': sorted(by_genre.items(), key=lambda kv: -kv[1]['readers']),
        'promotions': Promotion.objects.filter(ends_at__gt=since).select_related('story')[:30],
    })


# ── Restore Centre ───────────────────────────────────────────────────────────
@chief_area
def restore(request):
    if request.method == 'POST':
        kind = request.POST.get('kind')
        if kind == 'book':
            story = get_object_or_404(Story, pk=request.POST.get('id'), is_removed=True)
            story.is_removed = False
            story.save(update_fields=['is_removed', 'updated_at'])
            InboxMessage.objects.create(user=story.author, story=story, title='Your book was restored',
                                        body=f'"{story.display_title}" is visible again.')
            messages.success(request, f'"{story.display_title}" restored.')
        elif kind == 'chapter':
            ch = get_object_or_404(Chapter, pk=request.POST.get('id'),
                                   status__in=[Chapter.STATUS_REMOVED, Chapter.STATUS_TRASH])
            ch.status = ch.previous_status if ch.previous_status not in (Chapter.STATUS_REMOVED, Chapter.STATUS_TRASH) \
                else Chapter.STATUS_DRAFT
            ch.save(update_fields=['status', 'updated_at'])
            messages.success(request, f'Chapter {ch.number} of "{ch.story.display_title}" restored as '
                                      f'{ch.get_status_display().lower()}.')
        elif kind == 'author':
            u = get_object_or_404(User, pk=request.POST.get('id'), is_suspended=True)
            u.is_suspended = False
            u.save(update_fields=['is_suspended'])
            messages.success(request, f'{u.display_name} reactivated.')
        return _back(request)
    return render(request, 'dashboard/editorial/ce_restore.html', {
        'books': Story.objects.filter(is_removed=True).select_related('author')[:200],
        'chapters': Chapter.objects.filter(status__in=[Chapter.STATUS_REMOVED, Chapter.STATUS_TRASH])
        .select_related('story', 'story__author').order_by('-updated_at')[:200],
        'authors': User.objects.filter(is_suspended=True, role=User.ROLE_AUTHOR)[:200],
    })
