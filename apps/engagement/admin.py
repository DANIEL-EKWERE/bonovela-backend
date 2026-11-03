from django.contrib import admin

from . import models

for m in (models.LibraryEntry, models.ReadingProgress, models.ReadingTime, models.Review, models.ReviewReply,
          models.InboxMessage, models.CheckIn, models.TaskClaim, models.Report):
    admin.site.register(m)
