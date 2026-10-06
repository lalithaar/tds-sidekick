from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Approval', {
            'fields': (
                'display_name',
                'role',
                'is_approved',
                'approval_key',
                'approval_requested_at',
                'approved_at',
                'approved_by',
            )
        }),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Approval', {
            'fields': ('display_name', 'email', 'role', 'is_approved')
        }),
    )
    list_display = ('username', 'display_name', 'email', 'role', 'is_approved', 'approval_key', 'approval_requested_at')
    list_filter = ('is_approved', 'role')
    search_fields = ('username', 'display_name', 'email')
