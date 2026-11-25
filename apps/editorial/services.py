"""Editorial workflow transitions. Each step checks the editor's role and
notifies the author through their inbox."""

import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.engagement.models import InboxMessage
from apps.users.models import User
from apps.wallet.models import AuthorEarning

from .models import ContractApplication as App
from .models import PayToReadApplication, Promotion, PromotionRequest, Shelf, ShelfBook, ShelfCategory


def _require(editor, *roles):
    if not (editor.role in roles or editor.is_platform_admin):
        raise PermissionDenied('Your role cannot do this.')


def _require_status(app, *statuses):
    if app.status not in statuses:
        raise ValidationError(f'Application is "{app.get_status_display()}".')


def _notify(story, title, body):
    InboxMessage.objects.create(user=story.author, story=story, title=title, body=body)


def _mark_rejected(app, editor, reason):
    app.rejected_by, app.rejected_at, app.rejection_reason = editor, timezone.now(), reason


# ── Contract applications ────────────────────────────────────────────────────
@transaction.atomic
def se_decide(app, editor, decision: str, feedback: str = ''):
    """Senior Editor reviews the application: approve (→ CE), revise, or reject."""
    _require(editor, User.ROLE_SE, User.ROLE_CE)
    _require_status(app, App.STATUS_PENDING)
    app.se = editor
    app.feedback = feedback
    if decision == 'approve':
        app.status = App.STATUS_SE_APPROVED
        _notify(app.story, 'Your book was approved 🎉', 'Our Chief Editor will send your contract shortly.')
    elif decision == 'revise':
        app.status = App.STATUS_REVISION
        _mark_rejected(app, editor, feedback)
        _notify(app.story, 'Your book needs a few changes', feedback or 'Please revise and apply again.')
    elif decision == 'reject':
        # No reason written: the author gets the standard rejection message.
        app.status = App.STATUS_REJECTED
        app.feedback = feedback or App.AUTO_REJECTION
        _mark_rejected(app, editor, feedback)
        _notify(app.story, 'Application update', app.feedback)
    else:
        raise ValidationError('decision must be approve, revise or reject')
    app.save()


@transaction.atomic
def ce_send_contract(app, editor, contract_type: str = 'non_exclusive', signing_bonus_cents: int = 0):
    """The Chief Editor sends the contract: after the SE's approval, or
    straight from review when helping a busy editor."""
    _require(editor, User.ROLE_CE)
    _require_status(app, App.STATUS_SE_APPROVED, App.STATUS_PENDING)
    if app.status == App.STATUS_PENDING and not app.se_id:
        app.se = editor
    app.ce = editor
    app.contract_type = contract_type
    app.signing_bonus_cents = max(0, signing_bonus_cents)
    app.status = App.STATUS_CONTRACT_SENT
    app.save()
    _notify(app.story, 'Congratulations! Your contract has been approved ✍️',
            f'We sent your {app.get_contract_type_display().lower()} contract to your email. You can also review '
            'and sign it in Author Center.')
    from .emails import send_contract_approved
    transaction.on_commit(lambda: send_contract_approved(app))


INFO_TEMPLATE = (
    'Dear Author,\n\n'
    "I'm {editor}, your editor on Bonovela.\n\n"
    "I hope this email finds you well. I've received your book application, and before we proceed further, I'd like "
    'to confirm a few important details with you.\n\n'
    '1. Have you published this book on any other websites or apps? If yes, please share the links.\n\n'
    '2. Have you signed any contract with another publishing platform for this book? If yes, please provide the '
    "relevant contract details or screenshots showing the book's views and readership performance.\n\n"
    '3. Are you willing to accept a non-exclusive contract? This means your book can be published on other platforms '
    'while remaining available on Bonovela. However, your benefits and earnings will be subject to the terms of your '
    'agreement.\n\n'
    '4. Are you 18 years of age or older?\n'
    'Authors under 18 will require parental or legal guardian consent before a publishing agreement can be completed. '
    'The necessary documents and consent arrangements will be provided accordingly.\n\n'
    '5. There shall be no racial discrimination in the books. Can you understand and accept this?\n'
    '(If there is a rape detail in the story, please delete it, or change the rape to torture, kidnap, or verbal '
    'humiliation. Or let the rape fail.)\n\n'
    '6. Will you be able to make the book over 60k in a month since the date the story is signed?\n\n'
    'We look forward to your responses!\n'
    '---\n'
    'Best Regards,\n'
    '{editor}'
)


def info_message(editor):
    return INFO_TEMPLATE.format(editor=editor.get_full_name() or editor.display_name)


def email_author(editor, author, subject, body, story=None):
    """An editor writes to an author: emailed from noreply@bonovela.com as
    "<Editor> via Bonovela", replies go to the editor's own email, and a copy
    lands in the author's inbox."""
    from apps.engagement.models import InboxMessage
    from apps.users.emails import send
    InboxMessage.objects.create(user=author, story=story, title=subject, body=body)
    if not author.email:
        return False
    name = editor.get_full_name() or editor.display_name
    return send(author.email, subject, 'emails/editor_info.html', body, {'message': body},
                reply_to=[editor.email] if editor.email else None, from_name=name)


def send_info_email(app, editor):
    """Step 1 — the editor's [Info] button: the confirmation questions."""
    _require(editor, User.ROLE_SE, User.ROLE_CE)
    _require_status(app, App.STATUS_PENDING, App.STATUS_SE_APPROVED)
    email_author(editor, app.story.author, f'About your Bonovela application: {app.story.display_title}',
                 info_message(editor), story=app.story)
    app.info_sent_at, app.info_sent_by = timezone.now(), editor
    app.save(update_fields=['info_sent_at', 'info_sent_by', 'updated_at'])


def _same_name(a, b):
    return ' '.join((a or '').lower().split()) == ' '.join((b or '').lower().split())


_DRAWN = re.compile(r'^data:image/png;base64,[A-Za-z0-9+/]+=*$')


@transaction.atomic
def author_sign(app, name: str, drawing: str = '', ip=None):
    """Step 3a — the author signs with their real name (typed, and drawn
    if they like). Bonovela then countersigns."""
    _require_status(app, App.STATUS_CONTRACT_SENT)
    name = ' '.join((name or '').split())
    if not _same_name(name, app.signer_name):
        raise ValidationError(f'Sign with your full legal name as on your application: {app.signer_name}.')
    drawing = (drawing or '').strip()
    if drawing and (len(drawing) > 400_000 or not _DRAWN.match(drawing)):
        raise ValidationError('The drawn signature could not be read. Clear it and draw again.')
    app.author_signature_name, app.author_signature_image = name, drawing
    app.author_signed_at, app.author_signed_ip = timezone.now(), ip
    app.status = App.STATUS_AUTHOR_SIGNED
    app.save()
    _notify(app.story, 'Contract signed ✍️', f'Thanks for signing the contract for "{app.story.display_title}". '
            'Bonovela will add its signature shortly.')


@transaction.atomic
def countersign(app, editor):
    """Step 3b — the Chief Editor signs for Bonovela; the book is signed."""
    _require(editor, User.ROLE_CE)
    _require_status(app, App.STATUS_AUTHOR_SIGNED)
    app.platform_signature_name = editor.get_full_name() or editor.display_name
    app.platform_signed_by, app.platform_signed_at = editor, timezone.now()
    app.status = App.STATUS_SIGNED
    app.save()
    story = app.story
    story.is_signed = True
    story.signed_at = timezone.now()
    story.save(update_fields=['is_signed', 'signed_at', 'updated_at'])
    if app.signing_bonus_cents:
        AuthorEarning.objects.create(author=story.author, story=story, type='signing_bonus',
                                     amount_cents=app.signing_bonus_cents, note='Contract signed')
    _notify(story, 'Welcome aboard 💗', f'"{story.display_title}" is now signed by you and Bonovela.')


# ── App Publishing Control ───────────────────────────────────────────────────
def publish_in_app(editor, story, publish: bool = True):
    """Chief Editor puts a signed book in the app (or takes it out)."""
    _require(editor, User.ROLE_CE)
    if publish and not story.is_signed:
        raise ValidationError('Only signed books can be published to the app.')
    story.in_app = publish
    story.in_app_at, story.in_app_by = (timezone.now(), editor) if publish else (story.in_app_at, story.in_app_by)
    story.save(update_fields=['in_app', 'in_app_at', 'in_app_by', 'updated_at'])
    if publish:
        live = story.is_short or story.published_chapters().count() >= settings.BONOVELA['MIN_LIVE_CHAPTERS']
        _notify(story, 'Your book is in the app 🎉', f'"{story.display_title}" is published in the Bonovela app'
                + ('.' if live else f' and appears once it has {settings.BONOVELA["MIN_LIVE_CHAPTERS"]} published '
                   'chapters.'))
    return story


def update_contract_template(editor, body: str):
    """Only the Chief Editor and admins change the contract template."""
    from .models import ContractTemplate
    _require(editor, User.ROLE_CE)
    body = (body or '').strip()
    if len(body) < 50:
        raise ValidationError('The contract text is too short.')
    current = ContractTemplate.current()
    if body == current.body:
        return current
    return ContractTemplate.objects.create(body=body, version=current.version + 1, updated_by=editor)


# ── Payment detail changes ───────────────────────────────────────────────────
def decide_payout_change(editor, change, approve: bool, note: str = ''):
    """Chief Editor approves (the new details replace the old) or rejects."""
    from apps.wallet.models import PayoutMethod, PayoutMethodChange
    _require(editor, User.ROLE_CE)
    if change.status != PayoutMethodChange.STATUS_PENDING:
        raise ValidationError('Already decided.')
    change.status = PayoutMethodChange.STATUS_APPROVED if approve else PayoutMethodChange.STATUS_REJECTED
    change.decided_by, change.decided_at, change.note = editor, timezone.now(), note[:200]
    change.save()
    if approve:
        method, _ = PayoutMethod.objects.get_or_create(author=change.author, defaults={
            'method': change.data.get('method', 'bank'), 'account_name': '', 'account_detail': ''})
        for k, v in change.data.items():
            if hasattr(method, k):
                setattr(method, k, v)
        method.save()
        InboxMessage.objects.create(user=change.author, title='Payment details updated',
                                    body='Your new payment details were approved and will be used for your next '
                                         'payment.')
    else:
        InboxMessage.objects.create(user=change.author, title='Payment details not changed',
                                    body=note or 'Your request to change your payment details was not approved. '
                                                 'Your payments still go to your current account.')
    return change


# ── Pay-to-Read ──────────────────────────────────────────────────────────────
@transaction.atomic
def apply_pay_to_read(story, from_chapter: int, message: str = '') -> PayToReadApplication:
    """Author: ask for chapters [from_chapter] onward to be paid."""
    if not story.can_apply_pay_to_read:
        need = story.pay_to_read_min_words
        raise ValidationError(f'Pay-to-Read opens for signed books with {need:,} published words, once per book.')
    last = story.published_chapters().order_by('-number').values_list('number', flat=True).first() or 0
    if not 2 <= from_chapter <= last:
        raise ValidationError(f'Choose a chapter between 2 and {last}.')
    return PayToReadApplication.objects.create(story=story, requested_from=from_chapter, message=message[:2000])


@transaction.atomic
def decide_pay_to_read(editor, app, approve: bool, from_chapter: int | None = None, note: str = ''):
    """Editor approves (keeping or changing the first paid chapter) or rejects.
    Approved: chapters from that one on are locked, the book leaves the Free
    section, and the author is told which chapters were approved."""
    _require(editor, User.ROLE_SE, User.ROLE_CE)
    if app.status != PayToReadApplication.STATUS_PENDING:
        raise ValidationError('Already decided.')
    story = app.story
    app.decided_by, app.decided_at, app.note = editor, timezone.now(), note[:2000]
    if approve:
        chapter = from_chapter or app.requested_from
        last = story.published_chapters().order_by('-number').values_list('number', flat=True).first() or 0
        if not 2 <= chapter <= last:
            raise ValidationError(f'Choose a chapter between 2 and {last}.')
        app.status, app.approved_from = PayToReadApplication.STATUS_APPROVED, chapter
        story.paid_from_chapter = chapter
        story.save(update_fields=['paid_from_chapter', 'updated_at'])
        changed = '' if chapter == app.requested_from else \
            f' Your editor chose chapter {chapter} instead of chapter {app.requested_from}.'
        _notify(story, 'Pay-to-Read approved 💰',
                f'"{story.display_title}" is now Pay-to-Read. Chapters 1–{chapter - 1} stay free; chapter {chapter} '
                f'and every future chapter are locked.{changed}' + (f'\n\nEditor\'s note: {note}' if note else ''))
    else:
        app.status = PayToReadApplication.STATUS_REJECTED
        _notify(story, 'Pay-to-Read application update',
                note or "Your book wasn't approved for Pay-to-Read this time. Keep improving it and apply again.")
    app.save()
    return app


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


# ── Curated shelves ──────────────────────────────────────────────────────────
def add_to_shelf(editor, shelf, story) -> ShelfBook:
    """Editors suggest books; the CE's own picks go straight onto the shelf."""
    _require(editor, User.ROLE_SE, User.ROLE_CE)
    if story.is_short != (shelf.category.kind == ShelfCategory.KIND_SHORT):
        raise ValidationError('Short stories go on Short Stories shelves, novels on novel shelves.')
    if not story.is_signed or story.is_removed:
        raise ValidationError('Only signed books can be shelved.')
    chief = editor.role == User.ROLE_CE or editor.is_platform_admin
    entry, created = ShelfBook.objects.get_or_create(shelf=shelf, story=story, defaults={'added_by': editor})
    if not created and entry.status != ShelfBook.STATUS_REJECTED:
        raise ValidationError('That book is already on this shelf.')
    entry.added_by = editor
    entry.status = ShelfBook.STATUS_APPROVED if chief else ShelfBook.STATUS_PENDING
    entry.decided_by = editor if chief else None
    entry.order = _next_order(shelf) if chief else 0
    entry.save()
    return entry


def _next_order(shelf):
    last = shelf.entries.filter(status=ShelfBook.STATUS_APPROVED).order_by('-order').first()
    return last.order + 1 if last else 0


def decide_shelf_book(editor, entry, approve: bool):
    _require(editor, User.ROLE_CE)
    if entry.status != ShelfBook.STATUS_PENDING:
        raise ValidationError('Already decided.')
    entry.status = ShelfBook.STATUS_APPROVED if approve else ShelfBook.STATUS_REJECTED
    entry.decided_by = editor
    entry.order = _next_order(entry.shelf) if approve else 0
    entry.save()


def remove_from_shelf(editor, entry):
    _require(editor, User.ROLE_CE)
    entry.delete()


def move(editor, obj, step: int):
    """Swap a shelf book, shelf or category with its neighbour (step ±1)."""
    _require(editor, User.ROLE_CE)
    if isinstance(obj, ShelfBook):
        siblings = obj.shelf.entries.filter(status=ShelfBook.STATUS_APPROVED)
    elif isinstance(obj, Shelf):
        siblings = obj.category.shelves.all()
    else:
        siblings = ShelfCategory.objects.filter(kind=obj.kind)
    items = list(siblings.order_by('order', 'pk'))
    i = items.index(obj)
    j = i + step
    if 0 <= j < len(items):
        items[i], items[j] = items[j], items[i]
        for n, item in enumerate(items):
            if item.order != n:
                item.order = n
                item.save(update_fields=['order'])


def rename(editor, obj, name: str, layout: str | None = None):
    """Rename a shelf or category; shelves can also change layout."""
    _require(editor, User.ROLE_CE)
    name = name.strip()
    if not name:
        raise ValidationError('Give it a name.')
    if isinstance(obj, Shelf):
        obj.title = name[:60]
        if layout in dict(Shelf.LAYOUT_CHOICES):
            obj.layout = layout
    else:
        obj.name = name[:60]
    obj.save()
