from django.contrib import admin

from .models import Drama, DramaSave, Episode, EpisodeUnlock, WatchProgress


class EpisodeInline(admin.TabularInline):
    model = Episode
    extra = 0
    fields = ('number', 'title', 'status', 'video', 'video_url', 'duration_seconds', 'plays')


@admin.register(Drama)
class DramaAdmin(admin.ModelAdmin):
    list_display = ('title', 'creator', 'kind', 'is_published', 'is_removed', 'plays', 'updated_at')
    list_filter = ('kind', 'is_published', 'is_removed', 'genre')
    search_fields = ('title', 'creator__email', 'creator__pen_name')
    raw_id_fields = ('creator', 'story')
    inlines = [EpisodeInline]


admin.site.register(EpisodeUnlock)
admin.site.register(DramaSave)
admin.site.register(WatchProgress)
