"""Safe account and organization context for the shared navigation."""

from .models import Organization, OrganizationMembership


PURPOSE_LABELS = {
    Organization.Purpose.CUSTOMER: 'Customer organization',
    Organization.Purpose.CANONICAL_DEMO: 'Canonical/default organization',
    Organization.Purpose.TEMPORARY_DEMO: 'Temporary demo',
}


def account_indicator(request):
    context = {
        'account_organization': None,
        'account_organization_context': 'No active organization',
        'account_is_temporary_demo': False,
    }
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return context

    membership = (
        OrganizationMembership.objects.select_related('organization')
        .filter(user=user)
        .first()
    )
    if membership is None:
        return context

    organization = membership.organization
    context.update({
        'account_organization': organization,
        'account_organization_context': PURPOSE_LABELS.get(
            organization.purpose,
            'Organization',
        ),
    })
    if organization.purpose != Organization.Purpose.TEMPORARY_DEMO:
        return context

    from scheduler_app.demo_session_access import (
        ACCESS_VALID,
        validate_temporary_demo_access,
    )

    access = getattr(request, 'temporary_demo_access', None)
    if access is None:
        access = validate_temporary_demo_access(user)
    context['account_is_temporary_demo'] = (
        access.category == ACCESS_VALID
        and access.organization.pk == organization.pk
    )
    return context
