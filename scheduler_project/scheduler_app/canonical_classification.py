"""Guarded service for the one-way canonical demo classification."""

from dataclasses import dataclass
from typing import Callable

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from members.models import DEFAULT_ORGANIZATION_NAME, DemoSession, Organization


class CanonicalClassificationError(Exception):
    pass


@dataclass(frozen=True)
class CanonicalClassificationResult:
    organization_identifier: str
    original_purpose: str
    final_purpose: str
    already_canonical: bool


def _fail(message):
    raise CanonicalClassificationError(message)


def _locked_matches(identifier):
    return list(
        Organization.objects.select_for_update().filter(name=identifier)[:2]
    )


def _verify_stored_purpose(organization):
    organization.refresh_from_db(fields=('purpose',))
    if organization.purpose != Organization.Purpose.CANONICAL_DEMO:
        _fail('Final verification did not find canonical_demo; classification rolled back.')


def classify_canonical_demo_organization(
    identifier,
    *,
    before_write: Callable[[str, str], None] | None = None,
):
    """Classify only the exact configured customer organization, atomically."""
    if not getattr(settings, 'DEMO_SCAFFOLDING_ENABLED', False):
        _fail('Demo scaffolding is disabled.')

    configured = getattr(settings, 'DEMO_ORGANIZATION_IDENTIFIER', '').strip()
    if not configured:
        _fail('No allowed demo organization identifier is configured.')
    if not identifier:
        _fail('An explicit organization identifier is required.')
    if identifier != configured:
        _fail(
            'The requested organization does not exactly match the configured '
            'demo identifier.'
        )
    if identifier == DEFAULT_ORGANIZATION_NAME:
        _fail('Default Organization can never be classified as canonical demo.')

    with transaction.atomic():
        matches = _locked_matches(identifier)
        if not matches:
            _fail('The configured demo organization does not exist.')
        if len(matches) != 1:
            _fail('The demo organization identifier is ambiguous.')

        organization = matches[0]
        if organization.name != configured:
            _fail('The resolved organization does not match the configured target.')
        if organization.purpose == Organization.Purpose.TEMPORARY_DEMO:
            _fail('A temporary demo organization cannot be classified as canonical.')
        if DemoSession.objects.filter(organization=organization).exists():
            _fail('An organization owned by a DemoSession cannot be classified.')
        if not organization.memberships.exists():
            _fail(
                'The configured demo organization must have an existing '
                'organization membership.'
            )
        if organization.purpose not in (
            Organization.Purpose.CUSTOMER,
            Organization.Purpose.CANONICAL_DEMO,
        ):
            _fail('The organization purpose is not an allowed classification source.')

        original_purpose = organization.purpose
        if before_write:
            before_write(
                original_purpose,
                Organization.Purpose.CANONICAL_DEMO,
            )

        already_canonical = (
            original_purpose == Organization.Purpose.CANONICAL_DEMO
        )
        if not already_canonical:
            organization.purpose = Organization.Purpose.CANONICAL_DEMO
            try:
                organization.full_clean()
            except ValidationError as error:
                raise CanonicalClassificationError(
                    f'Organization validation failed: {error}'
                ) from error
            organization.save(update_fields=('purpose',))

        _verify_stored_purpose(organization)
        return CanonicalClassificationResult(
            organization_identifier=organization.name,
            original_purpose=original_purpose,
            final_purpose=organization.purpose,
            already_canonical=already_canonical,
        )
