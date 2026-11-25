"""Emails to authors about their contract."""

import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse

log = logging.getLogger(__name__)


def _abs(path):
    return settings.SITE_URL.rstrip('/') + path


def send_contract_approved(app):
    """"Congratulations! Your contract has been approved" with the book's
    information and a link to review and sign. Never raises: a mail problem
    must not block the editor."""
    story, author = app.story, app.story.author
    if not author.email:
        return False
    editor = app.se or app.ce
    ctx = {
        'logo_url': _abs(static('img/bonovela-logo.jpg')),
        'title': story.display_title, 'author': author.pen_name or author.display_name,
        'contract': app.get_contract_type_display(), 'editor': editor.display_name if editor else 'Bonovela',
        'contract_url': _abs(reverse('dashboard:author_signing', args=[story.pk])),
        'dashboard_url': _abs(reverse('dashboard:author_stories')),
    }
    text = (f'Congratulations! Your contract has been approved.\n\n'
            f'Book: {ctx["title"]}\nAuthor: {ctx["author"]}\nContract: {ctx["contract"]}\nEditor: {ctx["editor"]}\n\n'
            f'Sign your contract: {ctx["contract_url"]}\n\nBonovela — Stories that stay with you')
    msg = EmailMultiAlternatives('Congratulations! Your Bonovela contract has been approved', text,
                                 settings.DEFAULT_FROM_EMAIL, [author.email])
    msg.attach_alternative(render_to_string('emails/contract_approved.html', ctx), 'text/html')
    try:
        msg.send()
        return True
    except Exception:  # noqa: BLE001 — logged; the inbox message still reaches the author
        log.exception('Contract email to %s failed', author.email)
        return False
