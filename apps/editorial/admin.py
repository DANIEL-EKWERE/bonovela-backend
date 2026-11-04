from django.contrib import admin

from . import models

admin.site.register(models.ContractApplication, list_display=('story', 'status', 'prior_flag', 'se', 'ce', 'updated_at'),
                    list_filter=('status',))
admin.site.register(models.Promotion, list_display=('story', 'section', 'starts_at', 'ends_at'), list_filter=('section',))
admin.site.register(models.PromotionRequest, list_display=('story', 'requested_by', 'status', 'created_at'),
                    list_filter=('status',))
admin.site.register(models.EditorialNote)
for m in (models.Target, models.Escalation, models.Recommendation, models.TeamMessage,
          models.CompletionApplication, models.BookTransfer):
    admin.site.register(m)
