from django.contrib import admin

from .models import Banner, Broadcast


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ('title', 'placement', 'target', 'is_active', 'starts_at', 'ends_at', 'impressions', 'clicks')
    list_filter = ('placement', 'is_active')


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ('title', 'audience', 'recipients', 'bonus', 'sent_by', 'sent_at')
