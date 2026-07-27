from django.contrib import admin
from .models import DemoSession, Organization, OrganizationMembership


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'purpose', 'created_at', 'updated_at')
    list_filter = ('purpose',)
    search_fields = ('name',)
    ordering = ('name',)

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('purpose',)
        return ()


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'created_at')
    list_filter = ('organization',)
    search_fields = ('user__username', 'user__email', 'organization__name')
    autocomplete_fields = ('user', 'organization')


@admin.register(DemoSession)
class DemoSessionAdmin(admin.ModelAdmin):
    list_display = (
        'identifier',
        'user',
        'organization',
        'mode',
        'status',
        'expires_at',
    )
    list_filter = ('mode', 'status', 'expires_at')
    search_fields = ('identifier', 'user__username', 'organization__name')
    autocomplete_fields = ('user', 'organization')
    readonly_fields = (
        'identifier',
        'created_at',
        'updated_at',
        'last_activity_at',
    )
