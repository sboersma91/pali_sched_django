import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

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
    class Purpose(models.TextChoices):
        CUSTOMER = 'customer', 'Permanent / customer'
        CANONICAL_DEMO = 'canonical_demo', 'Canonical demo'
        TEMPORARY_DEMO = 'temporary_demo', 'Temporary demo'

    name = models.CharField(max_length=150, unique=True)
    purpose = models.CharField(
        max_length=20,
        choices=Purpose.choices,
        default=Purpose.CUSTOMER,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = (
            models.CheckConstraint(
                condition=Q(
                    purpose__in=(
                        'customer',
                        'canonical_demo',
                        'temporary_demo',
                    )
                ),
                name='organization_valid_purpose',
            ),
        )

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


class DemoSession(models.Model):
    """Durable ownership record; later request services must re-run validation."""

    class Mode(models.TextChoices):
        PREPARED = 'prepared', 'Prepared'
        CLEAN = 'clean', 'Clean'

    class Status(models.TextChoices):
        PROVISIONING = 'provisioning', 'Provisioning'
        ACTIVE = 'active', 'Active'
        EXPIRING = 'expiring', 'Expiring'
        DELETING = 'deleting', 'Deleting'
        FAILED = 'failed', 'Failed'

    identifier = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='demo_session',
    )
    organization = models.OneToOneField(
        Organization,
        on_delete=models.PROTECT,
        related_name='demo_session',
    )
    mode = models.CharField(max_length=10, choices=Mode.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROVISIONING,
    )
    scenario_version = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    cleanup_attempt_count = models.PositiveIntegerField(default=0)
    last_cleanup_error = models.TextField(blank=True, default='')

    class Meta:
        ordering = ('expires_at',)
        constraints = (
            models.CheckConstraint(
                condition=Q(mode__in=('prepared', 'clean')),
                name='demo_session_valid_mode',
            ),
            models.CheckConstraint(
                condition=Q(
                    status__in=(
                        'provisioning',
                        'active',
                        'expiring',
                        'deleting',
                        'failed',
                    )
                ),
                name='demo_session_valid_status',
            ),
            models.CheckConstraint(
                condition=Q(cleanup_attempt_count__gte=0),
                name='demo_session_cleanup_attempts_nonnegative',
            ),
            models.CheckConstraint(
                condition=Q(mode='clean') | ~Q(scenario_version=''),
                name='demo_session_prepared_has_scenario',
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F('created_at')),
                name='demo_session_expiration_after_creation',
            ),
        )

    def __str__(self):
        return str(self.identifier)

    def clean(self):
        super().clean()
        errors = {}

        if self.organization_id:
            if self.organization.purpose != Organization.Purpose.TEMPORARY_DEMO:
                errors['organization'] = (
                    'A demo session organization must have temporary demo purpose.'
                )

        if self.user_id:
            if self.user.is_staff:
                errors['user'] = 'A staff user cannot own a demo session.'
            elif self.user.is_superuser:
                errors['user'] = 'A superuser cannot own a demo session.'

            try:
                membership = self.user.organization_membership
            except OrganizationMembership.DoesNotExist:
                errors['user'] = (
                    'A demo session user must have exactly one organization membership.'
                )
            else:
                if (
                    self.organization_id
                    and membership.organization_id != self.organization_id
                ):
                    errors['organization'] = (
                        'The user membership must match the demo session organization.'
                    )

        if not self.expires_at:
            errors['expires_at'] = 'An expiration time is required.'
        elif not self.created_at and self.expires_at <= timezone.now():
            errors['expires_at'] = (
                'A new demo session expiration must be in the future.'
            )
        elif self.created_at and self.expires_at <= self.created_at:
            errors['expires_at'] = (
                'A demo session expiration must be later than its creation time.'
            )

        if self.mode == self.Mode.PREPARED and not self.scenario_version.strip():
            errors['scenario_version'] = (
                'Prepared demo sessions require a scenario version.'
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class DemoProvisioningAttempt(models.Model):
    class Outcome(models.TextChoices):
        ADMITTED = 'admitted', 'Admitted'
        CAPACITY_DENIED = 'capacity_denied', 'Capacity denied'
        PREPARED_BUSY = 'prepared_busy', 'Prepared operation busy'
        SUCCEEDED = 'succeeded', 'Succeeded'
        FAILED = 'failed', 'Failed'

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    mode = models.CharField(max_length=10, choices=DemoSession.Mode.choices)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    client_key = models.CharField(max_length=64)
    failure_category = models.CharField(max_length=80, blank=True, default='')
    demo_session = models.ForeignKey(
        DemoSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='provisioning_attempts',
    )

    class Meta:
        indexes = (
            models.Index(
                fields=('client_key', 'created_at'),
                name='demo_attempt_client_time',
            ),
            models.Index(
                fields=('mode', 'created_at'),
                name='demo_attempt_mode_time',
            ),
        )
        constraints = (
            models.CheckConstraint(
                condition=Q(mode__in=('prepared', 'clean')),
                name='demo_attempt_valid_mode',
            ),
            models.CheckConstraint(
                condition=Q(
                    outcome__in=(
                        'admitted',
                        'capacity_denied',
                        'prepared_busy',
                        'succeeded',
                        'failed',
                    )
                ),
                name='demo_attempt_valid_outcome',
            ),
        )


class DemoCapacityCoordinator(models.Model):
    """Singleton row used only to serialize cross-worker admission decisions."""

    identifier = models.CharField(
        max_length=20,
        primary_key=True,
        default='demo-capacity',
        editable=False,
    )
    updated_at = models.DateTimeField(auto_now=True)


class DemoCapacityReservation(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    mode = models.CharField(max_length=10, choices=DemoSession.Mode.choices)
    client_key = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    demo_session = models.ForeignKey(
        DemoSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='capacity_reservations',
    )

    class Meta:
        indexes = (
            models.Index(
                fields=('mode', 'expires_at'),
                name='demo_reservation_mode_exp',
            ),
        )
        constraints = (
            models.CheckConstraint(
                condition=Q(mode__in=('prepared', 'clean')),
                name='demo_reservation_valid_mode',
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F('created_at')),
                name='demo_reservation_future_exp',
            ),
        )


class DemoOperationLease(models.Model):
    PREPARED = 'prepared'
    MAINTENANCE = 'demo_maintenance'
    OPERATION_CHOICES = (
        (PREPARED, 'Prepared provisioning or reset'),
        (MAINTENANCE, 'Demo maintenance'),
    )

    operation = models.CharField(
        max_length=20,
        choices=OPERATION_CHOICES,
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    acquired_at = models.DateTimeField()
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        indexes = (
            models.Index(
                fields=('operation', 'expires_at'),
                name='demo_lease_operation_exp',
            ),
        )
        constraints = (
            models.CheckConstraint(
                condition=Q(operation__in=('prepared', 'demo_maintenance')),
                name='demo_lease_valid_operation',
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F('acquired_at')),
                name='demo_lease_future_exp',
            ),
        )


class DemoPreparedResetAttempt(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    demo_session = models.ForeignKey(
        DemoSession,
        on_delete=models.SET_NULL,
        null=True,
        related_name='prepared_reset_attempts',
    )
