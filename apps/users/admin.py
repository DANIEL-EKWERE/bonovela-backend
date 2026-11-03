from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Follow, User


@admin.register(User)
class BonovelaUserAdmin(UserAdmin):
    list_display = ('email', 'username', 'pen_name', 'role', 'is_suspended', 'date_joined')
    list_filter = ('role', 'is_suspended', 'provider', 'is_staff')
    search_fields = ('email', 'username', 'pen_name')
    ordering = ('-date_joined',)
    fieldsets = UserAdmin.fieldsets + (
        ('Bonovela', {'fields': ('role', 'pen_name', 'about', 'avatar', 'provider', 'email_verified',
                                 'reader_type', 'language', 'is_suspended')}),
    )


admin.site.register(Follow)
