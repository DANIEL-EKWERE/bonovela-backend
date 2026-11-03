"""Editorial workflow transitions. Each step checks the editor's role and
notifies the author through their inbox."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.engagement.models import InboxMessage
from apps.users.models import User
from apps.wallet.models import AuthorEarning

from .models import ContractApplication as App
from .models import Promotion, PromotionRequest


def _require(editor, *roles):
    if not (editor.role in roles or editor.is_platform_admin):
        raise PermissionDenied('Your role cannot do this.')


def _require_status(app, *statuses):
    if app.status not in statuses:
        raise ValidationError(f'Application is "{app.get_status_display()}".')


def _notify(story, title, body):
    InboxMessage.objects.create(user=story.author, story=story, title=title, body=body)


# ── Contract applications ────────────────────────────────────────────────────
@transaction.atomic
def ae_review(app, editor, approve: bool, feedback: str = ''):
    """Assistant Editor: first read. Approve → SE queue, or send back."""
    _require(editor, User.ROLE_AE, User.ROLE_SE, User.ROLE_CE)
    _require_status(app, App.STATUS_PENDING)
    app.ae = editor
    app.feedback = feedback
    app.status = App.STATUS_AE_APPROVED if approve else App.STATUS_REVISION
    app.save()
    if not approve:
        _notify(app.story, 'Your book needs a few changes',
                feedback or 'An editor reviewed your application and asked for revisions.')


@transaction.atomic
def se_decide(app, editor, decision: str, feedback: str = ''):
    """Senior Editor: approve (→ CE), revise, or reject."""
    _require(editor, User.ROLE_SE, User.ROLE_CE)
    _require_status(app, App.STATUS_AE_APPROVED)
    app.se = editor
    app.feedback = feedback
    if decision == 'approve':
        app.status = App.STATUS_SE_APPROVED
        _notify(app.story, 'Your book was approved 🎉', 'Our Chief Editor will send your contract shortly.')
    elif decision == 'revise':
        app.status = App.STATUS_REVISION
        _notify(app.story, 'Your book needs a few changes', feedback or 'Please revise and apply again.')
    elif decision == 'reject':
        app.status = App.STATUS_REJECTED
        _notify(app.story, 'Application update', feedback or "Your book wasn't selected for signing this time.")
    else:
        raise ValidationError('decision must be approve, revise or reject')
    app.save()


@transaction.atomic
def ce_send_contract(app, editor, contract_type: str = 'non_exclusive', signing_bonus_cents: int = 0):
    _require(editor, User.ROLE_CE)
    _require_status(app, App.STATUS_SE_APPROVED)
    app.ce = editor
    app.contract_type = contract_type
    app.signing_bonus_cents = max(0, signing_bonus_cents)
    app.status = App.STATUS_CONTRACT_SENT
    app.save()
    _notify(app.story, 'Your contract is ready ✍️',
            f'Open Author Center to review and sign your {app.get_contract_type_display().lower()} contract.')


@transaction.atomic
def sign(app):
    """Author signs: the story goes live in the app."""
    _require_status(app, App.STATUS_CONTRACT_SENT)
    app.status = App.STATUS_SIGNED
    app.save()
    story = app.story
    story.is_signed = True
    story.signed_at = timezone.now()
    story.save(update_fields=['is_signed', 'signed_at', 'updated_at'])
    if app.signing_bonus_cents:
        AuthorEarning.objects.create(author=story.author, story=story, type='signing_bonus',
                                     amount_cents=app.signing_bonus_cents, note='Contract signed')
    _notify(story, 'Welcome aboard 💗', f'"{story.display_title}" is now signed and visible to readers in the app.')


# ── Promotions ───────────────────────────────────────────────────────────────
VALID_SECTIONS = {key for key, _ in Promotion.SECTION_CHOICES}


@transaction.atomic
def request_promotion(editor, story, sections, days: int, reason: str) -> PromotionRequest:
    """SE → CE. SEs can't place books in sections themselves."""
    _require(editor, User.ROLE_SE)
    sections = [s for s in sections if s in VALID_SECTIONS]
    if not sections:
        raise ValidationError('Pick at least one section.')
    if not story.is_signed:
        raise ValidationError('Only signed books can be promoted.')
    return PromotionRequest.objects.create(
        story=story, sections=sections, days=max(1, min(days, 30)), reason=reason, requested_by=editor
    )


@transaction.atomic
def decide_promotion(editor, req, approve: bool, note: str = '', sections=None):
    """CE approves (optionally adjusting sections) or rejects."""
    _require(editor, User.ROLE_CE)
    if req.status != PromotionRequest.STATUS_PENDING:
        raise ValidationError('Already decided.')
    req.status = PromotionRequest.STATUS_APPROVED if approve else PromotionRequest.STATUS_REJECTED
    req.decided_by = editor
    req.decision_note = note
    req.decided_at = timezone.now()
    if approve and sections is not None:
        req.sections = [s for s in sections if s in VALID_SECTIONS] or req.sections
    req.save()
    if approve:
        for section in req.sections:
            promote(editor, req.story, section, req.days, request=req)


def promote(editor, story, section: str, days: int, request=None) -> Promotion:
    _require(editor, User.ROLE_CE)
    if section not in VALID_SECTIONS:
        raise ValidationError('Unknown section.')
    now = timezone.now()
    return Promotion.objects.create(
        story=story, section=section, starts_at=now, ends_at=now + timedelta(days=days), created_by=editor,
        request=request,
    )


def end_promotion(editor, promotion):
    _require(editor, User.ROLE_CE)
    promotion.ends_at = timezone.now()
    promotion.save(update_fields=['ends_at'])
