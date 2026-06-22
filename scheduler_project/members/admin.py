from django.contrib import admin
from .models import Organization, OrganizationMembership


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at', 'updated_at')
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'organization', 'created_at')
    list_filter = ('organization',)
    search_fields = ('user__username', 'user__email', 'organization__name')
    autocomplete_fields = ('user', 'organization')
