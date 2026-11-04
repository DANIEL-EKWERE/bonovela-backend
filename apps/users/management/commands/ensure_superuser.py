"""Create the first platform admin from environment variables, once.

    DJANGO_SUPERUSER_EMAIL=you@example.com
    DJANGO_SUPERUSER_PASSWORD=...

Does nothing when the variables are missing or the account already exists,
so it is safe to run on every deploy.
"""

import os

from django.core.management.base import BaseCommand

from apps.users.models import User


class Command(BaseCommand):
    help = 'Create the admin account from DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD if missing.'

    def handle(self, *args, **opts):
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip().lower()
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '')
        if not email or not password:
            self.stdout.write('ensure_superuser: DJANGO_SUPERUSER_EMAIL/PASSWORD not set, skipping.')
            return
        if User.objects.filter(email__iexact=email).exists():
            self.stdout.write(f'ensure_superuser: {email} already exists.')
            return
        User.objects.create_superuser(username=email.split('@')[0], email=email, password=password,
                                      pen_name='Bonovela Admin')
        self.stdout.write(self.style.SUCCESS(f'ensure_superuser: created {email}.'))
