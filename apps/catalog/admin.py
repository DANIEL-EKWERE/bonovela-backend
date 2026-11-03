from django.contrib import admin

from .models import Chapter, Contest, Genre, Story, Tag


class ChapterInline(admin.TabularInline):
    model = Chapter
    fields = ('number', 'title', 'status', 'word_count', 'published_at')
    readonly_fields = ('word_count',)
    extra = 0


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = ('display_title', 'author', 'genre', 'status', 'is_signed', 'is_short', 'views', 'updated_at')
    list_filter = ('status', 'is_signed', 'is_short', 'genre', 'content_rating')
    search_fields = ('title', 'author__email', 'author__pen_name')
    filter_horizontal = ('tags', 'contests')
    inlines = [ChapterInline]


@admin.register(Chapter)
class ChapterAdmin(admin.ModelAdmin):
    list_display = ('story', 'number', 'title', 'status', 'word_count', 'published_at')
    list_filter = ('status',)
    search_fields = ('story__title', 'title')


admin.site.register(Genre)
admin.site.register(Tag, list_display=('name', 'group'), list_filter=('group',))
admin.site.register(Contest)
