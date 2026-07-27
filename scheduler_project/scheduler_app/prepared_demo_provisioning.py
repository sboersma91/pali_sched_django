"""Atomic provisioning for isolated prepared demo sessions."""

from dataclasses import dataclass
import uuid

from django.db import transaction
from django.utils import timezone

from members.models import DemoSession

from .demo_scaffolding import (
    DEMO_SCENARIO,
    PREPARED_DEMO_SCENARIO_VERSION,
    build_demo_scenario_for_organization,
)
from .prepared_scenarios import build_working_scenario, get_prepared_scenario
from .working_demo_scenario import WORKING_DEMO_SCENARIO_VERSION
from .demo_session_provisioning import (
    DEFAULT_CLEAN_DEMO_SESSION_LIFETIME,
    _create_temporary_ownership,
    _enforce_capacity_policy,
    _validated_session_times,
    _verify_temporary_ownership_unit,
)


DEFAULT_PREPARED_DEMO_SESSION_LIFETIME = DEFAULT_CLEAN_DEMO_SESSION_LIFETIME


@dataclass(frozen=True)
class PreparedDemoProvisioningResult:
    demo_session: DemoSession
    user: object
    organization: object
    membership: object
    schedule: object
    completed: bool
    expires_at: object
    validation_summary: dict


def _construct_prepared_scenario(
    organization,
    demo_session,
    scenario_version=PREPARED_DEMO_SCENARIO_VERSION,
):
    if scenario_version == WORKING_DEMO_SCENARIO_VERSION:
        schedules, outcomes = build_working_scenario(organization)
        return (
            None,
            schedules['Halfweek- Lakes, Oak Park, River ONLY'],
            {'coverage': {'complete': True}, 'outcomes': outcomes},
        )
    return build_demo_scenario_for_organization(
        organization,
        demo_session,
        scenario=DEMO_SCENARIO,
        expected_schedule_name=DEMO_SCENARIO['schedule']['name'],
        ownership_context='prepared_visitor',
        allow_stable_creation=True,
        establish_mutable_baseline=True,
        require_generation=True,
        require_full_validation=True,
    )


def _activate_prepared_session(demo_session):
    demo_session.status = DemoSession.Status.ACTIVE
    demo_session.full_clean()
    demo_session.save(update_fields=('status',))
    demo_session.refresh_from_db()
    if demo_session.status != DemoSession.Status.ACTIVE:
        raise RuntimeError('Prepared DemoSession activation verification failed.')


def _final_prepared_verification(
    demo_session,
    user,
    organization,
    membership,
    schedule,
):
    _verify_temporary_ownership_unit(
        demo_session,
        user,
        organization,
        membership,
        expected_mode=DemoSession.Mode.PREPARED,
        expected_status=DemoSession.Status.ACTIVE,
        require_empty=False,
    )
    scenario = get_prepared_scenario(demo_session.scenario_version)
    expected_schedule = (
        scenario['schedule']['name']
        if demo_session.scenario_version == PREPARED_DEMO_SCENARIO_VERSION
        else scenario['schedules'][0][0]
    )
    if (
        schedule.organization_id != organization.pk
        or schedule.sched_name != expected_schedule
    ):
        raise RuntimeError('Prepared scenario final verification failed.')


def provision_prepared_demo_session(
    *,
    lifetime=DEFAULT_PREPARED_DEMO_SESSION_LIFETIME,
    clock=timezone.now,
    identity_factory=uuid.uuid4,
    admission=None,
    scenario_version=PREPARED_DEMO_SCENARIO_VERSION,
):
    """Atomically create one complete isolated prepared demo environment."""
    get_prepared_scenario(scenario_version)
    now, expires_at = _validated_session_times(lifetime, clock)

    admission = _enforce_capacity_policy(
        requested_mode=DemoSession.Mode.PREPARED,
        admission=admission,
    )
    try:
        with transaction.atomic():
            user, organization, membership, demo_session = (
                _create_temporary_ownership(
                    identity_factory=identity_factory,
                    now=now,
                    expires_at=expires_at,
                    mode=DemoSession.Mode.PREPARED,
                    status=DemoSession.Status.PROVISIONING,
                    scenario_version=scenario_version,
                )
            )
            if scenario_version == PREPARED_DEMO_SCENARIO_VERSION:
                _result, schedule, assignment = _construct_prepared_scenario(
                    organization, demo_session,
                )
            else:
                _result, schedule, assignment = _construct_prepared_scenario(
                    organization, demo_session, scenario_version,
                )
            _activate_prepared_session(demo_session)
            _final_prepared_verification(
                demo_session,
                user,
                organization,
                membership,
                schedule,
            )
            result = PreparedDemoProvisioningResult(
                demo_session=demo_session,
                user=user,
                organization=organization,
                membership=membership,
                schedule=schedule,
                completed=True,
                expires_at=expires_at,
                validation_summary={
                    'generation_complete': True,
                    'operational_replay_clean': True,
                    'assignment_complete': assignment['coverage']['complete'],
                },
            )
    except Exception as error:
        from .demo_capacity import finish_demo_provisioning

        finish_demo_provisioning(
            admission,
            failure_category=error.__class__.__name__,
        )
        raise
    from .demo_capacity import finish_demo_provisioning

    finish_demo_provisioning(admission, demo_session=demo_session)
    return result
