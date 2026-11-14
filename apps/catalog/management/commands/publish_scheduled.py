"""Publish chapters whose scheduled time has passed. Run every few minutes
(Render cron job in render.yaml):

    python manage.py publish_scheduled
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.catalog.models import Chapter
from apps.engagement.models import InboxMessage


class Command(BaseCommand):
    help = 'Publish scheduled chapters and send scheduled broadcasts that are due.'

    def handle(self, *args, **options):
        due = Chapter.objects.filter(status=Chapter.STATUS_SCHEDULED, scheduled_for__lte=timezone.now())
        published = failed = 0
        for chapter in due.select_related('story__author'):
            try:
                chapter.publish()
                published += 1
            except ValidationError as e:
                # Edited out of range after scheduling: back to drafts, tell the author.
                chapter.status, chapter.scheduled_for = Chapter.STATUS_DRAFT, None
                chapter.save(update_fields=['status', 'scheduled_for'])
                InboxMessage.objects.create(
                    user=chapter.story.author, story=chapter.story,
                    title=f"Chapter {chapter.number} wasn't published",
                    body=f'{e.messages[0]} It is back in your drafts.',
                )
                failed += 1
        from apps.marketing.services import send_due_broadcasts
        sent = send_due_broadcasts()
        self.stdout.write(f'Published {published}, returned {failed} to drafts, sent {sent} scheduled broadcasts.')
