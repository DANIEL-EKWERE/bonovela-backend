"""Accounts made before email confirmation existed stay usable."""

from django.db import migrations


def mark_verified(apps, schema_editor):
    apps.get_model('users', 'User').objects.filter(email_verified=False).update(email_verified=True)


class Migration(migrations.Migration):
    dependencies = [('users', '0007_author_policy_agreed')]
    operations = [migrations.RunPython(mark_verified, migrations.RunPython.noop)]
