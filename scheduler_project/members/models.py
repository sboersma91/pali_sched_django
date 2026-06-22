from django.db import models
from django.conf import settings

DEFAULT_ORGANIZATION_NAME = 'Default Organization'


def get_default_organization():
    organization, _created = Organization.objects.get_or_create(name=DEFAULT_ORGANIZATION_NAME)
    return organization


def get_user_organization(user):
    if user and user.is_authenticated:
        membership = getattr(user, 'organization_membership', None)
        if membership:
            return membership.organization
    return get_default_organization()


class Organization(models.Model):
    name = models.CharField(max_length=150, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class OrganizationMembership(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='organization_membership',
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('organization__name', 'user__username')

    def __str__(self):
        return f'{self.user} — {self.organization}'
