"""Spot books that editors turned down before.

When an author applies for a contract, every earlier application that was
rejected (or sent back for revision) is checked against the new one:

    same book       the same story applying again
    same author     the same account, with a different book
    shared IP       a different account that used the same IP address
    similar text    the opening chapters or title match another rejected book

Matches are stored on the new application (prior_flag / prior_matches) so the
editor dashboard can flag it and show the details.
"""

import re

from django.db.models import Q

from apps.catalog.models import Chapter
from apps.users.models import IPLog

from . import similarity
from .models import ContractApplication as App

TEXT_MATCH_PCT = 30
OPENING_CHAPTERS = 3


def _norm_title(t):
    return re.sub(r'[^a-z0-9]+', ' ', (t or '').lower()).strip()


def _opening(story):
    idx = similarity._index()
    text = ' '.join(story.chapters.filter(status__in=[Chapter.STATUS_PUBLISHED, Chapter.STATUS_DRAFT])
                    .order_by('number').values_list('content', flat=True)[:OPENING_CHAPTERS])
    return similarity._distinctive(similarity.shingles(text), idx)


def _ips(user, extra=None):
    ips = set(IPLog.objects.filter(user=user).values_list('ip', flat=True))
    if extra:
        ips.add(extra)
    return ips


def senior_of(editor):
    """The SE responsible for an editor (an SE is their own)."""
    if not editor:
        return None
    if editor.role in ('se', 'ce', 'admin'):
        return editor
    return editor.supervisor


def find_prior(app):
    story = app.story
    author = story.author
    earlier = (App.objects.filter(Q(status__in=[App.STATUS_REJECTED, App.STATUS_REVISION]) | Q(rejected_by__isnull=False))
               .exclude(pk=app.pk).filter(created_at__lte=app.created_at)
               .select_related('story__author', 'story__editor', 'rejected_by__supervisor', 'se'))
    if not earlier.exists():
        return []
    my_ips = _ips(author, app.submitted_ip)
    my_text = None
    my_title = _norm_title(story.title)
    current_se = senior_of(story.editor)
    out = []
    for old in earlier:
        reasons = []
        if old.story_id == story.pk:
            reasons.append({'kind': 'same_book', 'label': 'Same book'})
        elif old.story.author_id == author.pk:
            reasons.append({'kind': 'same_author', 'label': 'Same author account'})
        else:
            shared = my_ips & _ips(old.story.author, old.submitted_ip)
            if shared:
                reasons.append({'kind': 'shared_ip', 'label': f'Shared IP address ({", ".join(sorted(shared)[:3])})'})
        if old.story_id != story.pk:
            if my_title and _norm_title(old.story.title) == my_title:
                reasons.append({'kind': 'same_title', 'label': 'Same title'})
            if my_text is None:
                my_text = _opening(story)
            theirs = _opening(old.story)
            if len(my_text) >= 20 and theirs:
                pct = round(len(my_text & theirs) * 100 / len(my_text))
                if pct >= TEXT_MATCH_PCT:
                    reasons.append({'kind': 'similar_text', 'label': f'Opening chapters {pct}% the same', 'pct': pct})
        if not reasons:
            continue
        rejecter = old.rejected_by or old.se
        old_se = senior_of(rejecter)
        out.append({
            'application_id': old.pk,
            'story_id': old.story_id,
            'title': old.story.title,
            'author': old.story.author.display_name,
            'author_email': old.story.author.email,
            'status': old.status,
            'status_label': old.get_status_display(),
            'rejected_by': rejecter.display_name if rejecter else '',
            'rejected_by_role': rejecter.get_role_display() if rejecter else '',
            'rejected_by_team': old_se.display_name if old_se else '',
            'reason': old.rejection_reason or old.feedback or '',
            'rejected_at': (old.rejected_at or old.updated_at).isoformat(),
            'applied_at': old.created_at.isoformat(),
            'other_team': bool(old_se and current_se and old_se.pk != current_se.pk),
            'reasons': reasons,
        })
    out.sort(key=lambda m: m['rejected_at'], reverse=True)  # newest first…
    out.sort(key=lambda m: m['status'] != App.STATUS_REJECTED)  # …rejections before revisions
    return out


def flag(app):
    """Check [app] against earlier rejections and store the result."""
    matches = find_prior(app)
    if any(m['status'] == App.STATUS_REJECTED for m in matches):
        level = App.FLAG_REJECTED
    elif matches:
        level = App.FLAG_REVISION
    else:
        level = ''
    App.objects.filter(pk=app.pk).update(prior_flag=level, prior_matches=matches)
    app.prior_flag, app.prior_matches = level, matches
    return app


def submit(story, request=None, message='', form=None):
    """Create a contract application (from a valid ContractApplicationForm
    when given), record the IP, hand an unassigned book to the editor the
    author chose, and run the prior-rejection check."""
    from django.utils import timezone

    from apps.catalog.models import Story
    from apps.users.ip import record

    ip = record(story.author, request, 'submit') if request else None
    app = form.save(commit=False) if form else App(story=story)
    app.message, app.submitted_ip = message[:2000], ip
    app.save()
    if app.chosen_editor_id and not story.editor_id:
        Story.objects.filter(pk=story.pk).update(editor=app.chosen_editor, assigned_at=timezone.now())
    return flag(app)
