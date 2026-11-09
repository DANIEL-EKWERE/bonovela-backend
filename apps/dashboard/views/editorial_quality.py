"""Editorial quality tools: AI Completion Review, Plagiarism Checker and
Duplicate Chapter Detector."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.editorial import completion, similarity, team
from apps.editorial.models import CompletionApplication as Completion
from apps.engagement.models import InboxMessage

from .editorial import editor_area


@editor_area
def completion_list(request):
    tab = request.GET.get('tab', 'open')
    qs = Completion.objects.filter(story__in=team.scope_books(request.user)).select_related(
        'story__author', 'story__editor', 'reviewer')
    if tab == 'open':
        qs = qs.filter(status__in=[Completion.STATUS_PENDING, Completion.STATUS_RECOMMENDED])
    else:
        qs = qs.exclude(status__in=[Completion.STATUS_PENDING, Completion.STATUS_RECOMMENDED])
    return render(request, 'dashboard/editorial/completion_list.html', {
        'apps': qs, 'tab': tab, 'ai_enabled': completion.ai_enabled(), 'lv': request.level,
    })


@editor_area
def completion_detail(request, pk):
    app = get_object_or_404(Completion.objects.select_related('story__author', 'story__genre'), pk=pk,
                            story__in=team.scope_books(request.user))
    lv = request.level
    if request.method == 'POST':
        action = request.POST.get('action')
        note = request.POST.get('note', '').strip()
        story = app.story
        if action == 'analyse':
            completion.run(app)
            messages.success(request, 'Manuscript analysed.' + (' AI review added.' if app.ai_review else ''))
        elif action in ('recommend', 'changes') and app.status in (Completion.STATUS_PENDING, Completion.STATUS_RECOMMENDED):
            app.reviewer, app.reviewer_note = request.user, note
            app.status = Completion.STATUS_RECOMMENDED if action == 'recommend' else Completion.STATUS_CHANGES
            app.save()
            if action == 'changes':
                InboxMessage.objects.create(user=story.author, story=story, title='Completion review: changes needed',
                                            body=note or 'Your editor asked for changes before the completion review.')
            messages.success(request, 'Recommended to the Chief Editor.' if action == 'recommend' else 'Sent back to the author.')
        elif action in ('approve', 'reject') and lv == team.CE:
            app.decided_by = request.user
            app.status = Completion.STATUS_APPROVED if action == 'approve' else Completion.STATUS_REJECTED
            app.reviewer_note = note or app.reviewer_note
            app.save()
            InboxMessage.objects.create(
                user=story.author, story=story,
                title='Completion review approved 🎉' if action == 'approve' else 'Completion review result',
                body=note or (f'Congratulations on finishing "{story.title}".' if action == 'approve'
                              else 'Not approved this time.'))
            messages.success(request, f'Application {app.get_status_display().lower()}.')
        return redirect('dashboard:editorial_completion_detail', app.pk)
    if not app.analysed_at:
        app.analysis = completion.analyse(app.story)  # quick checks; AI runs on demand
    return render(request, 'dashboard/editorial/completion_detail.html', {
        'app': app, 'a': app.analysis, 'lv': lv, 'ai_enabled': completion.ai_enabled(),
    })


@editor_area
def plagiarism(request):
    books = team.scope_books(request.user).order_by('title')
    story = books.filter(pk=request.GET.get('book') or 0).first()
    report = similarity.plagiarism_report(story) if story else None
    text_result = None
    if request.method == 'POST':
        text_result = similarity.check_text(request.POST.get('text', ''))
    return render(request, 'dashboard/editorial/plagiarism.html', {
        'books': books, 'story': story, 'report': report, 'text_result': text_result,
        'text': request.POST.get('text', ''),
    })


@editor_area
def duplicates(request):
    books = team.scope_books(request.user).order_by('title')
    story = books.filter(pk=request.GET.get('book') or 0).first()
    report = similarity.duplicate_report(story) if story else None
    scan = None
    if request.GET.get('scan'):
        scan = []
        for b in books.filter(is_removed=False)[:200]:
            r = similarity.duplicate_report(b)
            if r['pairs']:
                scan.append({'story': b, 'pairs': len(r['pairs']), 'top': r['pairs'][0]['pct']})
    return render(request, 'dashboard/editorial/duplicates.html', {
        'books': books, 'story': story, 'report': report, 'scan': scan,
    })


