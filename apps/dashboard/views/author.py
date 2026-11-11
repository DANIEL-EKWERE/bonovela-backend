"""Author Center on the web — same screens as the app's Author Center."""

import calendar
from datetime import date, datetime, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.catalog.models import Chapter, Genre, Story, Tag, word_count
from apps.editorial import services as editorial
from apps.editorial.models import ContractApplication
from apps.engagement.models import Review, ReviewReply
from apps.users.models import User
from apps.wallet.models import AuthorEarning, PayoutMethod
from apps.wallet.services import available_to_withdraw

from ..forms import LANGUAGES, ChapterForm, PayoutForm, StoryBasicsForm, StoryDetailsForm

# Commission is editors' income, not an author card.
HIDDEN_INCOME = AuthorEarning.RETIRED_TYPES | {'editor_commission'}


def author_area(view):
    """Signed-in users only; readers see the 'Become an author' intro."""

    @login_required
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if request.user.role == User.ROLE_READER:
            return render(request, 'dashboard/author/intro.html')
        return view(request, *args, **kwargs)

    return wrapper


def _story(request, pk):
    return get_object_or_404(Story, pk=pk, author=request.user)


@login_required
@require_POST
def become_author(request):
    if request.user.role == User.ROLE_READER:
        request.user.role = User.ROLE_AUTHOR
        request.user.save(update_fields=['role'])
    messages.success(request, 'Welcome, author ✍️ Tap + to start your first story.')
    return redirect('dashboard:author_stories')


# ── Stories ──────────────────────────────────────────────────────────────────
@author_area
def stories(request):
    qs = (Story.objects.filter(author=request.user)
          .prefetch_related('chapters', 'applications', 'reviews'))
    min_live = settings.BONOVELA['MIN_LIVE_CHAPTERS']
    cards = []
    for s in qs:
        pub = [c for c in s.chapters.all() if c.status == Chapter.STATUS_PUBLISHED]
        cards.append({
            'story': s,
            'chapters': len(pub),
            'not_live': s.is_signed and not s.is_short and len(pub) < min_live,
            'can_apply': s.is_info_complete and sum(c.word_count for c in pub) >= settings.BONOVELA['CONTRACT_MIN_WORDS'],
            'min_live': min_live,
            'words': sum(c.word_count for c in pub),
            'comments': len(s.reviews.all()),
            'application': next(iter(s.applications.all()), None),
        })
    return render(request, 'dashboard/author/stories.html', {'cards': cards})


@author_area
@require_POST
def delete_story(request, pk):
    story = _story(request, pk)
    name = story.display_title
    story.delete()
    messages.success(request, f'Deleted "{name}".')
    return redirect('dashboard:author_stories')


@author_area
def story_info(request, pk=None):
    """Novel Information. ?step=1 basics · 2 details · 3 review."""
    story = _story(request, pk) if pk else Story(author=request.user)
    step = int(request.GET.get('step', 1))
    if step > 1 and not story.pk:
        return redirect('dashboard:author_story_new')

    form = None
    if request.method == 'POST':
        action = request.POST.get('action', 'next')
        if step == 1:
            form = StoryBasicsForm(request.POST, request.FILES, instance=story)
            if form.is_valid():
                story = form.save()
                if request.FILES.get('cover'):
                    story.cover_index = -1  # an uploaded photo replaces the palette
                    story.save(update_fields=['cover_index'])
                elif request.POST.get('remove_cover'):
                    story.cover.delete(save=True)
                if action == 'skip':
                    messages.success(request, 'Saved. Finish your book information before publishing.')
                    return redirect('dashboard:author_stories')
                if story.has_basics:
                    return redirect(f"{_url('dashboard:author_story_info', story.pk)}?step=2")
                messages.error(request, 'Add a cover, title, audience and content rating to continue.')
        elif step == 2:
            form = StoryDetailsForm(request.POST, instance=story)
            if form.is_valid():
                story = form.save()
                if action != 'back':
                    if story.has_details:
                        return redirect(f"{_url('dashboard:author_story_info', story.pk)}?step=3")
                    messages.error(request, 'Pick a genre and at least one tag.')
            if action == 'back':  # leave without forcing the step to be finished
                return redirect(f"{_url('dashboard:author_story_info', story.pk)}?step=1")
        elif step == 3:
            if action == 'back':
                return redirect(f"{_url('dashboard:author_story_info', story.pk)}?step=2")
            messages.success(request, f'"{story.display_title}" saved.')
            return redirect('dashboard:author_chapters', story.pk)

    if form is None:
        form = StoryBasicsForm(instance=story) if step == 1 else StoryDetailsForm(instance=story)

    tag_groups = []
    for key, label in Tag.GROUP_CHOICES:
        tag_groups.append({'key': key, 'label': label, 'tags': Tag.objects.filter(group=key)})
    selected = set(story.tags.values_list('id', flat=True)) if story.pk else set()
    if request.method == 'POST' and step == 2:
        selected = {int(x) for x in request.POST.getlist('tags') if x.isdigit()}

    return render(request, 'dashboard/author/story_info.html', {
        'story': story,
        'step': step,
        'form': form,
        'palettes': range(8),
        'languages': LANGUAGES,
        'ratings': Story.RATING_CHOICES,
        'rating_info': Story.RATING_INFO,
        'genres': Genre.objects.filter(is_active=True),
        'tag_groups': tag_groups,
        'selected_tags': selected,
        'types': Story.TYPE_CHOICES,
        'synopsis_words': settings.BONOVELA['SYNOPSIS_WORDS'],
    })


def _url(name, *args):
    from django.urls import reverse
    return reverse(name, args=args)


# ── Chapters ─────────────────────────────────────────────────────────────────
@author_area
def chapters(request, pk):
    story = _story(request, pk)
    tab = request.GET.get('tab', 'draft')
    groups = {
        'draft': [Chapter.STATUS_DRAFT, Chapter.STATUS_SCHEDULED, Chapter.STATUS_REVISION],
        'published': [Chapter.STATUS_PUBLISHED],
        'trash': [Chapter.STATUS_TRASH],
    }
    if tab not in groups:
        tab = 'draft'
    qs = story.chapters.filter(status__in=groups[tab]).order_by('-number')
    return render(request, 'dashboard/author/chapters.html', {
        'story': story, 'tab': tab, 'chapters': qs,
        'application': story.applications.first(),
        'counts': {k: story.chapters.filter(status__in=v).count() for k, v in groups.items()},
        'min_contract_words': settings.BONOVELA['CONTRACT_MIN_WORDS'],
    })


@author_area
@require_POST
def chapter_action(request, pk, chapter_id, action):
    story = _story(request, pk)
    chapter = get_object_or_404(Chapter, pk=chapter_id, story=story)
    if action == 'publish':
        if not story.is_info_complete:
            messages.error(request, 'Complete your book information before publishing.')
            return redirect(f"{_url('dashboard:author_story_info', story.pk)}?step=1")
        if not chapter.word_count:
            messages.error(request, 'Write something first.')
        else:
            try:
                chapter.publish()
                messages.success(request, f'Chapter {chapter.label} published.')
            except ValidationError as e:
                messages.error(request, e.messages[0])
    elif action == 'trash':
        chapter.move_to_trash()
        messages.success(request, 'Moved to Trash.')
    elif action == 'restore':
        chapter.restore()
        messages.success(request, 'Restored.')
    elif action == 'delete':
        chapter.delete()
        messages.success(request, 'Deleted forever.')
    else:
        raise Http404
    return redirect(request.POST.get('next') or _url('dashboard:author_chapters', story.pk))


@author_area
def chapter_editor(request, pk, chapter_id=None):
    story = _story(request, pk)
    if chapter_id:
        chapter = get_object_or_404(Chapter, pk=chapter_id, story=story)
    else:
        if not story.is_info_complete:
            messages.error(request, 'Complete your book information first.')
            return redirect(f"{_url('dashboard:author_story_info', story.pk)}?step=1")
        last = story.chapters.aggregate(n=Max('number'))['n'] or 0
        chapter = Chapter(story=story, number=last + 1)

    if request.method == 'POST':
        form = ChapterForm(request.POST, instance=chapter)
        if form.is_valid():
            chapter = form.save(commit=False)
            if not request.POST.get('has_note'):
                chapter.note = ''
            action = request.POST.get('action', 'save')
            is_empty = not chapter.title.strip() and not chapter.content.strip()
            if action == 'save':
                if not is_empty:
                    chapter.save()
                    messages.success(request, 'Chapter updated.' if chapter.status == Chapter.STATUS_PUBLISHED
                                     else 'Saved to drafts.')
                return redirect('dashboard:author_chapters', story.pk)
            length_error = Chapter.length_error(word_count(chapter.content))
            if chapter.word_count == 0 and not chapter.content.strip():
                messages.error(request, 'Write something first.')
            elif length_error:
                chapter.save()  # keep the work as a draft
                messages.error(request, length_error)
            elif action == 'release':
                chapter.save()
                chapter.publish()
                messages.success(request, f'Chapter {chapter.label} released 🎉')
                return redirect(f"{_url('dashboard:author_chapters', story.pk)}?tab=published")
            elif action == 'schedule':
                raw = request.POST.get('scheduled_for', '')
                try:
                    when = timezone.make_aware(datetime.fromisoformat(raw))
                except ValueError:
                    when = None
                if not when or when <= timezone.now():
                    messages.error(request, 'Pick a time in the future.')
                else:
                    chapter.status, chapter.scheduled_for = Chapter.STATUS_SCHEDULED, when
                    chapter.save()
                    messages.success(request, f'Scheduled for {when:%b %d, %H:%M}.')
                    return redirect('dashboard:author_chapters', story.pk)
    else:
        form = ChapterForm(instance=chapter)
    return render(request, 'dashboard/author/editor.html', {
        'story': story, 'chapter': chapter, 'form': form, 'chapter_words': settings.BONOVELA['CHAPTER_WORDS'],
    })


# ── Statistics & signing ─────────────────────────────────────────────────────
@author_area
def stats(request, pk):
    story = _story(request, pk)
    pub = list(story.published_chapters())
    today = timezone.localdate()
    yesterday = today.toordinal() - 1
    words_yesterday = sum(c.word_count for c in pub if c.published_at and c.published_at.date().toordinal() == yesterday)
    update_days = {c.published_at.date() for c in pub if c.published_at and c.published_at.date().month == today.month
                   and c.published_at.date().year == today.year}
    reviews = story.reviews.all()
    readers = {'female': 0, 'male': 0, 'unknown': 0}
    for entry in story.shelved_by.select_related('user'):
        readers[entry.user.reader_type or 'unknown'] += 1
    total_readers = sum(readers.values()) or 1
    return render(request, 'dashboard/author/stats.html', {
        'story': story,
        'release': [('Word count', words_yesterday, 'yesterday'),
                    ('Ave. chapter length', (sum(c.word_count for c in pub) // len(pub)) if pub else 0, 'all chapters'),
                    ('Updating days', len(update_days), 'this month')],
        'reading': [('Library adds', story.shelved_by.count(), 'accumulated'),
                    ('Views', story.views, 'accumulated'),
                    ('Chapters', len(pub), 'published')],
        'interactive': [('Comments', reviews.count(), 'accumulated'),
                        ('Gifts', story.gifts.count(), 'accumulated'),
                        ('Votes', sum(v.count for v in story.votes.all()), 'accumulated')],
        'gender': [(k.title() if k != 'unknown' else 'Rather not say', round(v * 100 / total_readers)) for k, v in readers.items()],
        'promotions': story.promotions.order_by('-starts_at')[:5],
    })


@author_area
def signing(request, pk):
    """Apply to sign / review & sign the contract."""
    story = _story(request, pk)
    app = story.applications.first()
    published = story.published_chapters().count()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'completion':
            from apps.editorial.models import CompletionApplication as Completion
            open_ = story.completion_applications.filter(
                status__in=[Completion.STATUS_PENDING, Completion.STATUS_RECOMMENDED]).exists()
            if story.status != Story.STATUS_COMPLETED or not story.is_signed:
                messages.error(request, 'Only signed, completed books can apply.')
            elif open_:
                messages.error(request, 'This book already has a completion review in progress.')
            else:
                Completion.objects.create(story=story, message=request.POST.get('message', '')[:2000])
                messages.success(request, 'Applied! Your editor will review the full manuscript.')
        elif action == 'sign' and app and app.status == ContractApplication.STATUS_CONTRACT_SENT:
            if not request.POST.get('agree'):
                messages.error(request, 'Tick the box to accept the contract terms.')
            else:
                editorial.sign(app)
                messages.success(request, f'"{story.display_title}" is signed and now visible in the app 🎉')
        return redirect('dashboard:author_signing', story.pk)
    completion = story.completion_applications.first()
    return render(request, 'dashboard/author/signing.html', {
        'story': story, 'app': story.applications.first(), 'published': published,
        'completion': completion,
        'completion_open': completion and completion.status in ('pending', 'recommended'),
        'min_words': settings.BONOVELA['CONTRACT_MIN_WORDS'],
    })


def _can_start_application(request, story):
    app = story.applications.first()
    if app and (app.is_open or app.status == ContractApplication.STATUS_SIGNED):
        messages.error(request, 'This story already has an open application.')
        return False
    if not story.can_apply_for_contract:
        need = settings.BONOVELA['CONTRACT_MIN_WORDS']
        messages.error(request, f'Complete your book information and publish at least {need:,} words first.')
        return False
    return True


@author_area
def contract_application(request, pk):
    """Apply for Contract: personal & ID → address → contract & book →
    review & submit. One form; the steps are shown one at a time."""
    from apps.editorial import geo
    from apps.editorial.forms import ContractApplicationForm, editors_for
    from apps.editorial.history import submit

    story = _story(request, pk)
    if not _can_start_application(request, story):
        return redirect('dashboard:author_signing', story.pk)
    form = ContractApplicationForm(request.POST or None, request.FILES or None, story=story)
    if request.method == 'POST' and form.is_valid():
        submit(story, request, form=form)
        messages.success(request, 'Application submitted. Status: Pending review. Editors usually reply within '
                                  '30 business days.')
        return redirect('dashboard:author_signing', story.pk)
    return render(request, 'dashboard/author/contract.html', {
        'story': story, 'form': form, 'editors': editors_for(story), 'countries': geo.countries(),
        'step': form.first_error_step() if form.errors else 1,
    })


# ── Discover / Calendar / Comments / Income tabs ─────────────────────────────
@author_area
def discover(request):
    """Writer's Benefits 2026."""
    return render(request, 'dashboard/author/discover.html')


@author_area
def update_calendar(request):
    stories = Story.objects.filter(author=request.user)
    story = stories.filter(pk=request.GET.get('story')).first() or stories.first()
    today = timezone.localdate()
    try:
        y, m = (int(x) for x in request.GET.get('month', today.strftime('%Y-%m')).split('-'))
        month = date(y, m, 1)
    except ValueError:
        month = today.replace(day=1)
    words = {}
    if story:
        for c in story.chapters.filter(status=Chapter.STATUS_PUBLISHED, published_at__year=month.year,
                                       published_at__month=month.month):
            d = timezone.localtime(c.published_at).day
            words[d] = words.get(d, 0) + c.word_count
    days_in = calendar.monthrange(month.year, month.month)[1]
    lead = (month.weekday() + 1) % 7  # Sunday first
    cells = [None] * lead + [
        {'day': d, 'words': words.get(d), 'future': date(month.year, month.month, d) > today} for d in range(1, days_in + 1)
    ]
    prev_m = (month.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    next_m = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return render(request, 'dashboard/author/calendar.html', {
        'stories': stories, 'story': story, 'month': month, 'cells': cells, 'total': sum(words.values()),
        'prev_month': prev_m, 'next_month': next_m.strftime('%Y-%m') if next_m <= today.replace(day=1) else None,
    })


@author_area
def comments(request):
    stories = Story.objects.filter(author=request.user)
    qs = Review.objects.filter(story__author=request.user, is_hidden=False).select_related('user', 'story', 'chapter')
    if request.GET.get('book'):
        qs = qs.filter(story_id=request.GET['book'])
    if request.GET.get('stars'):
        qs = qs.filter(stars=request.GET['stars'])
    if request.method == 'POST':
        review = get_object_or_404(Review, pk=request.POST.get('review'), story__author=request.user)
        text = request.POST.get('text', '').strip()
        if text:
            ReviewReply.objects.create(review=review, user=request.user, text=text[:1000])
            messages.success(request, 'Reply sent.')
        return redirect(request.get_full_path())
    return render(request, 'dashboard/author/comments.html', {
        'stories': stories, 'reviews': qs.prefetch_related('replies__user')[:200],
        'book': request.GET.get('book', ''), 'stars': request.GET.get('stars', ''),
    })


@author_area
def income(request):
    today = timezone.localdate()
    try:
        y, m = (int(x) for x in request.GET.get('month', today.strftime('%Y-%m')).split('-'))
        month = date(y, m, 1)
    except ValueError:
        month = today.replace(day=1)
    earnings = AuthorEarning.objects.filter(author=request.user)
    in_month = earnings.filter(created_at__year=month.year, created_at__month=month.month)
    totals = {k: 0 for k, _ in AuthorEarning.TYPE_CHOICES}
    for e in in_month:
        totals[e.type] += e.amount_cents
    cards = [{'key': k, 'label': label, 'cents': totals[k]} for k, label in AuthorEarning.TYPE_CHOICES
             if k not in HIDDEN_INCOME]
    # Second card, where MAB used to be: the balance the author can withdraw now.
    cards.insert(1, {'key': 'available', 'label': 'Available to withdraw',
                     'cents': available_to_withdraw(request.user)})

    payout = PayoutMethod.objects.filter(author=request.user).first()
    payout_form = PayoutForm(request.POST or None, instance=payout)
    if request.method == 'POST' and payout_form.is_valid():
        obj = payout_form.save(commit=False)
        obj.author = request.user
        obj.save()
        messages.success(request, 'Payment info saved.')
        return redirect(request.get_full_path())

    prev_m = (month - timedelta(days=1)).strftime('%Y-%m')
    next_first = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return render(request, 'dashboard/author/income.html', {
        'month': month, 'cards': cards, 'month_total': sum(totals.values()),
        'total': sum(e.amount_cents for e in earnings), 'payout': payout, 'payout_form': payout_form,
        'prev_month': prev_m, 'next_month': next_first.strftime('%Y-%m') if next_first <= today.replace(day=1) else None,
        'recent': in_month[:20],
    })
