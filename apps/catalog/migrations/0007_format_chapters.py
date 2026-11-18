"""Give every existing chapter the standard layout (spacing only)."""

import re

from django.db import migrations


def format_all(apps, schema_editor):
    from apps.catalog.formatting import format_chapter
    Chapter = apps.get_model('catalog', 'Chapter')
    for ch in Chapter.objects.only('pk', 'content').iterator():
        new = format_chapter(ch.content)
        if new != ch.content:
            words = len(re.sub(r'<[^>]+>', ' ', new).split())
            Chapter.objects.filter(pk=ch.pk).update(content=new, word_count=words)


class Migration(migrations.Migration):
    dependencies = [('catalog', '0006_load_taxonomy')]
    operations = [migrations.RunPython(format_all, migrations.RunPython.noop)]
