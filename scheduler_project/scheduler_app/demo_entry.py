"""Public HTTP boundary for starting an isolated clean demo."""

import logging

from django.conf import settings
from django.contrib.auth import login, logout
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from members.models import DemoSession

from .demo_session_provisioning import (
    CleanDemoProvisioningError,
    provision_clean_demo_session,
)
from .demo_capacity import (
    DemoAdmissionError,
    client_key_from_request,
    release_demo_provisioning,
    reserve_demo_provisioning_capacity,
)
from .demo_session_access import (
    ACCESS_VALID,
    validate_temporary_demo_access,
)
from .demo_scaffolding import (
    DEMO_SCENARIO,
    PREPARED_DEMO_SCENARIO_VERSION,
    DemoSafetyError,
)
from .models import TheSched
from .prepared_demo_provisioning import provision_prepared_demo_session
from .prepared_scenarios import get_prepared_scenario
from .working_demo_scenario import WORKING_DEMO_SCENARIO_VERSION
from .demo_mode_switching import DemoModeSwitchError, switch_demo_mode


logger = logging.getLogger(__name__)
AUTHENTICATION_BACKEND = 'django.contrib.auth.backends.ModelBackend'
SAFE_ENTRY_ERROR = (
    'The temporary demo could not be started. Please try again later.'
)


def _admission_failure_response(error):
    response = HttpResponse(error.visitor_message, status=error.status_code)
    if error.retry_after:
        response['Retry-After'] = str(error.retry_after)
    return response


def _anonymous_admission(request, mode):
    return reserve_demo_provisioning_capacity(
        requested_mode=mode,
        client_key=client_key_from_request(request),
    )


def _temporary_demo_ownership(user, *, expected=None):
    access = validate_temporary_demo_access(user, expected=expected)
    if access.category != ACCESS_VALID:
        return None
    return access.membership, access.organization, access.demo_session


def _active_clean_demo_ownership(user, *, expected=None):
    ownership = _temporary_demo_ownership(user, expected=expected)
    if ownership is None:
        return None
    _membership, _organization, demo_session = ownership
    if (
        demo_session.mode != DemoSession.Mode.CLEAN
        or demo_session.status != DemoSession.Status.ACTIVE
    ):
        return None
    return demo_session


def _active_prepared_demo_ownership(user, *, expected=None):
    ownership = _temporary_demo_ownership(user, expected=expected)
    if ownership is None:
        return None
    _membership, organization, demo_session = ownership
    if (
        demo_session.mode != DemoSession.Mode.PREPARED
        or demo_session.status != DemoSession.Status.ACTIVE
        or demo_session.scenario_version not in {
            PREPARED_DEMO_SCENARIO_VERSION, WORKING_DEMO_SCENARIO_VERSION,
        }
    ):
        return None

    scenario = get_prepared_scenario(demo_session.scenario_version)
    schedule_name = (
        scenario['schedule']['name']
        if demo_session.scenario_version == PREPARED_DEMO_SCENARIO_VERSION
        else scenario['schedules'][0][0]
    )
    schedules = TheSched.objects.filter(
        organization=organization, sched_name=schedule_name,
    )
    if schedules.count() != 1:
        return None
    schedule = schedules.get()
    data = schedule.sched_data
    if (
        not isinstance(data, dict)
        or not isinstance(data.get('generated_schedule'), dict)
        or not data['generated_schedule']
        or data.get('generation_complete') is not True
        or data.get('manual_moves') != []
        or data.get('manual_instructor_overrides') != []
        or data.get('instructor_override_revision') != 0
    ):
        return None
    if expected is not None and schedule.pk != expected.schedule.pk:
        return None
    return demo_session, schedule


def _entry_failure_response(request):
    return render(
        request,
        'sched_app_template/demo_landing.html',
        {
            'demo_entry_enabled': settings.DEMO_ENTRY_ENABLED,
            'entry_error': SAFE_ENTRY_ERROR,
        },
        status=503,
    )


@never_cache
@require_GET
def demo_landing(request):
    return render(
        request,
        'sched_app_template/demo_landing.html',
        {
            'demo_entry_enabled': settings.DEMO_ENTRY_ENABLED,
            'expired': request.GET.get('expired') == '1',
            'unavailable': request.GET.get('unavailable') == '1',
        },
    )


@require_POST
def start_clean_demo(request):
    if not settings.DEMO_ENTRY_ENABLED:
        return _entry_failure_response(request)
    if request.user.is_authenticated:
        ownership = _temporary_demo_ownership(request.user)
        if ownership is not None:
            _membership, _organization, demo_session = ownership
            try:
                switch_demo_mode(
                    demo_session=demo_session,
                    mode=DemoSession.Mode.CLEAN,
                )
            except DemoModeSwitchError:
                return _entry_failure_response(request)
            return redirect('home-paid')
        return HttpResponse(
            'This signed-in account cannot start a temporary demo.',
            status=403,
        )

    try:
        admission = _anonymous_admission(request, DemoSession.Mode.CLEAN)
    except DemoAdmissionError as error:
        return _admission_failure_response(error)
    try:
        try:
            result = provision_clean_demo_session(admission=admission)
        finally:
            release_demo_provisioning(
                admission,
                failure_category='entry_boundary_release',
            )
    except (CleanDemoProvisioningError, ValidationError, IntegrityError):
        logger.warning('Clean demo provisioning failed with a controlled error.')
        return _entry_failure_response(request)
    except Exception:
        release_demo_provisioning(
            admission,
            failure_category='unexpected_entry_failure',
        )
        raise

    try:
        login(
            request,
            result.user,
            backend=AUTHENTICATION_BACKEND,
        )
    except Exception:
        logger.error(
            'Clean demo login failed after provisioning.',
            extra={
                'demo_session_id': str(result.demo_session.identifier),
                'temporary_user_id': result.user.pk,
                'temporary_organization_id': result.organization.pk,
            },
        )
        logout(request)
        return _entry_failure_response(request)

    if _active_clean_demo_ownership(request.user, expected=result) is None:
        logger.warning(
            'Clean demo post-login ownership verification failed.',
            extra={
                'demo_session_id': str(result.demo_session.identifier),
                'temporary_user_id': result.user.pk,
                'temporary_organization_id': result.organization.pk,
            },
        )
        logout(request)
        return _entry_failure_response(request)

    return redirect('home-paid')


@require_POST
def start_prepared_demo(request):
    if not settings.DEMO_ENTRY_ENABLED:
        return _entry_failure_response(request)
    if request.user.is_authenticated:
        ownership = _temporary_demo_ownership(request.user)
        if ownership is not None:
            _membership, _organization, demo_session = ownership
            scenario_version = request.POST.get(
                'scenario_version', PREPARED_DEMO_SCENARIO_VERSION
            )
            try:
                switched = switch_demo_mode(
                    demo_session=demo_session,
                    mode=DemoSession.Mode.PREPARED,
                    scenario_version=scenario_version,
                )
            except DemoModeSwitchError:
                return _entry_failure_response(request)
            return redirect('sched-detail', pk=switched.schedule.pk)
        return HttpResponse(
            'This signed-in account cannot start a prepared temporary demo.',
            status=403,
        )

    try:
        admission = _anonymous_admission(request, DemoSession.Mode.PREPARED)
    except DemoAdmissionError as error:
        return _admission_failure_response(error)
    try:
        try:
            scenario_version = request.POST.get('scenario_version')
            if scenario_version:
                result = provision_prepared_demo_session(
                    admission=admission, scenario_version=scenario_version,
                )
            else:
                result = provision_prepared_demo_session(admission=admission)
        finally:
            release_demo_provisioning(
                admission,
                failure_category='entry_boundary_release',
            )
    except (
        CleanDemoProvisioningError,
        DemoSafetyError,
        ValidationError,
        IntegrityError,
    ):
        logger.warning('Prepared demo provisioning failed with a controlled error.')
        return _entry_failure_response(request)
    except Exception:
        release_demo_provisioning(
            admission,
            failure_category='unexpected_entry_failure',
        )
        raise

    try:
        login(
            request,
            result.user,
            backend=AUTHENTICATION_BACKEND,
        )
    except Exception:
        logger.error(
            'Prepared demo login failed after provisioning.',
            extra={
                'demo_session_id': str(result.demo_session.identifier),
                'temporary_user_id': result.user.pk,
                'temporary_organization_id': result.organization.pk,
            },
        )
        logout(request)
        return _entry_failure_response(request)

    prepared = _active_prepared_demo_ownership(
        request.user,
        expected=result,
    )
    if prepared is None:
        logger.warning(
            'Prepared demo post-login verification failed.',
            extra={
                'demo_session_id': str(result.demo_session.identifier),
                'temporary_user_id': result.user.pk,
                'temporary_organization_id': result.organization.pk,
            },
        )
        logout(request)
        return _entry_failure_response(request)

    _demo_session, schedule = prepared
    return redirect('sched-detail', pk=schedule.pk)
