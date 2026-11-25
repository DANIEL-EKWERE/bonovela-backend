"""Account emails from noreply@bonovela.com: confirm a new account, reset a
password. Links point at this site (SITE_URL). Mail problems are logged and
never break the request."""

import logging

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

log = logging.getLogger(__name__)


class _VerifyToken(PasswordResetTokenGenerator):
    """Valid until the email is confirmed (then it stops working)."""

    key_salt = 'bonovela.verify-email'

    def _make_hash_value(self, user, timestamp):
        return f'{user.pk}{user.email}{user.email_verified}{timestamp}'


verify_token = _VerifyToken()


def site(path):
    return settings.SITE_URL.rstrip('/') + path


def send(to, subject, template, text, ctx, reply_to=None, from_name=None):
    """Send one branded email. Returns True when handed to the mail server."""
    ctx = {'logo_url': site(static('img/bonovela-logo.jpg')), **ctx}
    sender = settings.DEFAULT_FROM_EMAIL
    if from_name:
        address = sender.split('<')[-1].rstrip('>') if '<' in sender else sender
        sender = f'{from_name} via Bonovela <{address}>'
    msg = EmailMultiAlternatives(subject, text, sender, [to], reply_to=reply_to or None)
    msg.attach_alternative(render_to_string(template, ctx), 'text/html')
    try:
        msg.send()
        return True
    except Exception:  # noqa: BLE001 — logged; the caller carries on
        log.exception('Email "%s" to %s failed', subject, to)
        return False


def _link(name, user, token):
    return site(reverse(name, args=[urlsafe_base64_encode(force_bytes(user.pk)), token]))


def send_verification(user):
    url = _link('dashboard:verify_email', user, verify_token.make_token(user))
    name = user.first_name or user.display_name
    return send(user.email, 'Activate your Bonovela account', 'emails/verify.html',
                f'Hi {name},\n\nConfirm your email to activate your Bonovela account:\n{url}\n',
                {'name': name, 'button_url': url, 'button_label': 'ACTIVATE MY ACCOUNT'})


def send_password_reset(user):
    url = _link('dashboard:password_reset_confirm', user, default_token_generator.make_token(user))
    name = user.first_name or user.display_name
    return send(user.email, 'Reset your Bonovela password', 'emails/password_reset.html',
                f'Hi {name},\n\nChoose a new Bonovela password here:\n{url}\n\nIgnore this if you didn\'t ask.',
                {'name': name, 'button_url': url, 'button_label': 'CHOOSE A NEW PASSWORD'})


def needs_verification(user):
    """Email/password reader and author accounts confirm their email first."""
    return (getattr(user, 'provider', 'email') == 'email' and not user.email_verified
            and user.role in (user.ROLE_READER, user.ROLE_AUTHOR))
