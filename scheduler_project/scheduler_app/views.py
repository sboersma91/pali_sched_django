import csv
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import get_object_or_404, render
from .models import Locations, Course, Schools, TheSched
from .forms import CourseForm, InstructorForm, LocationsForm, SchedForm, SchoolsForm
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse, reverse_lazy
from django.utils.text import slugify

from .school_accounting import school_slot_accounting_summary
from .schedule_operations import (
    DEFAULT_NEW_MOVE_ACTION,
    MalformedSchedDataError,
    MOVE_CONFLICT_SEVERITY,
    SCHEDULE_DAYS,
    apply_holding_reassignment_proposal,
    apply_move_proposal,
    apply_persisted_overrides,
    build_schedule_blocks,
    diagnose_sched_data_structure,
    evaluate_move_proposal_for_save,
    iter_schedule_blocks,
    persist_manual_move,
    repair_malformed_sched_data,
    validate_schedule_blocks,
)

def home(request):
    return render(request, 'sched_app_template/home.html', {})
'''
Since there is a unique field, there will need to be some sort of validation function made that says w/e place exists -- as of now it returns ValueError.
'''

# Class based views

from django.views.generic.list import ListView
from django.views.generic.detail import DetailView
from django.views.generic.edit import CreateView, UpdateView, DeleteView

# Locations 
class LocationList(ListView):
    model = Locations
    template_name = "pay_end/locations_list.html"
    context_object_name = 'location'
    # paginate_by = 10
    ordering = ['loc_name',]

class LocationDetail(DetailView):
    model = Locations
    template_name = "pay_end/location_detail.html"
    context_object_name = "location"

class LocationCreate(CreateView):
    model = Locations
    form_class = LocationsForm
    template_name = "pay_end/locations_form.html"
    success_url = reverse_lazy('location-list')

class LocationUpdate(UpdateView):
    model = Locations
    form_class = LocationsForm
    template_name = "pay_end/locations_form.html"
    success_url = reverse_lazy('location-list')

class LocationDelete(DeleteView):
    model = Locations
    template_name = "pay_end/location_confirm_delete.html"
    context_object_name = "location"
    success_url = reverse_lazy('location-list')

# Courses 
class CourseList(ListView):
    model = Course
    context_object_name = 'course'
    template_name = "pay_end/course_list.html"
    # paginate_by =10 
    # need to create the buttons to use this lul
    ordering = ['course_name',]
    
class CourseDetail(DetailView):
    model = Course
    template_name = 'pay_end/course_detail.html'
    context_object_name = 'course'

class CourseCreate(CreateView):
    model = Course
    form_class = CourseForm
    template_name = 'pay_end/course_form.html'
    success_url = reverse_lazy('course-list')

class CourseUpdate(UpdateView):
    model = Course
    form_class = CourseForm
    template_name = 'pay_end/course_form.html'
    success_url = reverse_lazy('course-list',)

class CourseDelete(DeleteView):
    model = Course
    template_name = 'pay_end/course_confirm_delete.html'
    fields = "__all__"
    success_url = reverse_lazy('course-list',)
    context_object_name = "school"

# Schools
class SchoolList(ListView):
    model = Schools
    template_name = 'pay_end/school_list.html'
    context_object_name = "school"
    ordering = ['school_name']

class SchoolDetail(DetailView):
    model = Schools
    template_name = 'pay_end/school_detail.html'
    context_object_name = "school"

class SchoolSlotAccountingMixin:
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['slot_summary'] = school_slot_accounting_summary(context['form'])
        return context


class SchoolCreate(SchoolSlotAccountingMixin, CreateView):
    model = Schools
    form_class = SchoolsForm
    template_name = 'pay_end/school_form.html'
    success_url = reverse_lazy('school-list')

class SchoolUpdate(SchoolSlotAccountingMixin, UpdateView):
    model = Schools
    form_class = SchoolsForm
    template_name = 'pay_end/school_form.html'
    success_url = reverse_lazy('school-list')

class SchoolDelete(DeleteView):
    model = Schools 
    template_name = 'pay_end/school_confirm_delete.html'
    fields = "__all__"
    success_url = reverse_lazy('school-list')
    context_object_name = "school"

CSV_ACTIVITY_VALUES = {'g_box': 'Unavailable / Not present', 'empty': 'Unassigned'}


def schedule_csv_export(request, pk):
    schedule_record = get_object_or_404(TheSched, pk=pk)
    generated_schedule = schedule_record.create_sched
    diagnostics = getattr(schedule_record, 'generation_diagnostics', [])
    if diagnostics:
        diagnostic_text = '; '.join(
            f"{diagnostic['school']} — {diagnostic['activity']}: {diagnostic['reason']}"
            for diagnostic in diagnostics
        )
        return HttpResponse(
            f'Schedule CSV export is unavailable because generation is blocked. {diagnostic_text}',
            status=409,
            content_type='text/plain',
        )

    generation_status = 'Complete' if getattr(schedule_record, 'generation_complete', True) else 'Incomplete'
    filename = slugify(schedule_record.sched_name) or f'schedule-{schedule_record.pk}'
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Schedule Name', 'Generation Status', 'Day', 'Time Block', 'Activity Group', 'Activity', 'Location'])

    for group_index, activity_group in enumerate(generated_schedule.get('ags', [])):
        for day in SCHEDULE_DAYS:
            for slot in day['slots']:
                slot_values = generated_schedule.get(slot['key'], [])
                value = slot_values[group_index] if group_index < len(slot_values) else 'empty'
                writer.writerow([
                    schedule_record.sched_name,
                    generation_status,
                    day['name'],
                    slot['label'],
                    activity_group,
                    CSV_ACTIVITY_VALUES.get(value, value),
                    '',
                ])
    return response


'''Starting of function based views'''
class SchedList(ListView):
    model = TheSched
    template_name = 'pay_end/sched_list.html'
    context_object_name = 'sched'
    ordering = ['sched_name']

    def get_queryset(self):
        return super().get_queryset().prefetch_related('schools')

class SchedDetail(DetailView):
    model = TheSched
    template_name = 'pay_end/sched_detail.html'
    context_object_name = 'sched'

    def get_move_proposal_input(self):
        target_slot_key = self.request.GET.get('target_slot')
        target_group_value = self.request.GET.get('target_group')
        return {
            'requested': target_slot_key is not None or target_group_value is not None,
            'source_kind': self.request.GET.get('source_kind') or 'grid',
            'source_block_id': self.request.GET.get('selected_block'),
            'source_holding_id': self.request.GET.get('selected_holding'),
            'source_activity_id_value': self.request.GET.get('source_activity_id'),
            'source_activity_name': self.request.GET.get('source_activity_name'),
            'source_occurrence_id': self.request.GET.get('source_occurrence_id'),
            'source_group_value': self.request.GET.get('source_group'),
            'source_slot_key': self.request.GET.get('source_slot') or None,
            'target_slot_key': target_slot_key,
            'target_group_value': target_group_value,
            'action_type': self.request.GET.get('action_type') or DEFAULT_NEW_MOVE_ACTION,
            'recomputed_server_side': self.request.GET.get('proposal_confirmed') == '1',
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        schedule = self.object.create_sched
        schedule_days = SCHEDULE_DAYS
        schedule_rows = build_schedule_blocks(schedule)
        replay_result = apply_persisted_overrides(self.object, schedule_rows)
        proposal_input = self.get_move_proposal_input()
        selected_block_id = proposal_input['source_block_id']
        proposal_result = None
        if proposal_input['requested']:
            try:
                target_group_index = int(proposal_input['target_group_value'])
            except (TypeError, ValueError):
                target_group_index = None
            try:
                source_activity_id = int(proposal_input['source_activity_id_value'])
            except (TypeError, ValueError):
                source_activity_id = None
            try:
                source_group_index = int(proposal_input['source_group_value'])
            except (TypeError, ValueError):
                source_group_index = None
            proposal = {
                'source_block_id': selected_block_id,
                'source_holding_id': proposal_input['source_holding_id'],
                'source_activity_id': source_activity_id,
                'source_activity_name': proposal_input['source_activity_name'],
                'source_occurrence_id': proposal_input['source_occurrence_id'],
                'source_group_index': source_group_index,
                'source_slot_key': proposal_input['source_slot_key'],
                'target_slot_key': proposal_input['target_slot_key'],
                'target_group_index': target_group_index,
                'action_type': proposal_input['action_type'],
            }
            if proposal_input['source_kind'] == 'holding':
                proposal_result = apply_holding_reassignment_proposal(
                    schedule_rows,
                    replay_result['holding_area'],
                    proposal,
                )
            else:
                proposal_result = apply_move_proposal(schedule_rows, proposal)
            if proposal_result['applied']:
                selected_block_id = proposal_result['target_block_id']

        conflict_summaries = validate_schedule_blocks(schedule_rows)
        existing_replay_conflicts = {
            (
                conflict.get('type'),
                conflict.get('override_index'),
                conflict.get('message'),
            )
            for conflict in conflict_summaries
        }
        conflict_summaries.extend(
            conflict
            for conflict in replay_result['replay_conflicts']
            if (
                conflict.get('type'),
                conflict.get('override_index'),
                conflict.get('message'),
            ) not in existing_replay_conflicts
        )
        save_readiness = None
        if proposal_result:
            proposal_result['conflicts'] = conflict_summaries
            save_readiness = evaluate_move_proposal_for_save(proposal_result)
        blocks_by_id = {
            block['block_id']: block
            for block in iter_schedule_blocks(schedule_rows)
        }
        grouped_conflicts = {}
        for conflict in conflict_summaries:
            summary_severity = (
                MOVE_CONFLICT_SEVERITY.get(conflict['type'], conflict['severity'])
                if proposal_result
                else conflict['severity']
            )
            group_key = (summary_severity, conflict['type'])
            grouped_conflicts.setdefault(group_key, []).append({
                **conflict,
                'related_blocks': [
                    {
                        'block_id': block['block_id'],
                        'group_label': block['group_label'],
                        'slot_label': block['slot_label'],
                        'slot_key': block['slot_key'],
                    }
                    for block_id in conflict['related_block_ids']
                    if (block := blocks_by_id.get(block_id))
                ],
            })
        conflict_summary_groups = [
            {
                'severity': severity,
                'type': conflict_type,
                'conflicts': conflicts,
            }
            for (severity, conflict_type), conflicts in grouped_conflicts.items()
        ]
        selected_block = next(
            (
                block
                for block in iter_schedule_blocks(schedule_rows)
                if block['is_activity'] and block['block_id'] == selected_block_id
            ),
            None,
        )
        selected_holding = next(
            (
                item
                for item in replay_result['holding_area']
                if item['holding_id'] == proposal_input['source_holding_id']
            ),
            None,
        )

        context['selected_schools'] = self.object.schools.order_by('school_name')
        context['schedule_days'] = schedule_days
        context['schedule_rows'] = schedule_rows
        context['selected_block'] = selected_block
        context['selected_holding'] = selected_holding
        context['selected_occurrence_id'] = selected_block['occurrence_id'] if selected_block else None
        context['proposal_result'] = proposal_result
        context['override_replay_result'] = replay_result
        context['displacement_preview'] = replay_result
        context['holding_area_preview'] = replay_result['holding_area']
        context['save_readiness'] = save_readiness
        context['proposal_recomputed_server_side'] = proposal_input['recomputed_server_side']
        context['conflict_summaries'] = conflict_summaries
        context['conflict_summary_groups'] = conflict_summary_groups
        context['has_blocking_conflicts'] = any(
            group['severity'] == 'error'
            for group in conflict_summary_groups
        )
        context['sched_data_diagnostic'] = diagnose_sched_data_structure(self.object.sched_data)
        context['generation_diagnostics'] = getattr(self.object, 'generation_diagnostics', [])
        context['generation_blocked'] = bool(context['generation_diagnostics'])
        context['generation_complete'] = getattr(self.object, 'generation_complete', True)
        context['generation_succeeded'] = not context['generation_blocked'] and context['generation_complete']
        context['generation_incomplete'] = not context['generation_blocked'] and not context['generation_complete']
        return context


class SchedMoveConfirm(SchedDetail):
    http_method_names = ['post']

    def get_move_proposal_input(self):
        return {
            'requested': True,
            'source_kind': self.request.POST.get('source_kind') or 'grid',
            'source_block_id': self.request.POST.get('source_block'),
            'source_holding_id': self.request.POST.get('source_holding'),
            'source_activity_id_value': self.request.POST.get('source_activity_id'),
            'source_activity_name': self.request.POST.get('source_activity_name'),
            'source_occurrence_id': self.request.POST.get('source_occurrence_id'),
            'source_group_value': self.request.POST.get('source_group'),
            'source_slot_key': self.request.POST.get('source_slot') or None,
            'target_slot_key': self.request.POST.get('target_slot'),
            'target_group_value': self.request.POST.get('target_group'),
            'action_type': self.request.POST.get('action_type') or DEFAULT_NEW_MOVE_ACTION,
            'recomputed_server_side': True,
        }

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        context = self.get_context_data(object=self.object)
        proposal_result = context['proposal_result']
        save_readiness = context['save_readiness']
        if proposal_result and proposal_result.get('applied'):
            if save_readiness and save_readiness['warning_conflicts']:
                messages.warning(
                    request,
                    'Proposal confirmed server-side with operational warnings. Review before saving.',
                )
            else:
                messages.success(request, 'Proposal confirmed server-side and is ready to save.')
        else:
            messages.error(
                request,
                proposal_result['message'] if proposal_result else 'Move proposal could not be confirmed.',
            )
        return HttpResponseRedirect(self.get_proposal_redirect_url(confirmed=True))

    def get_proposal_redirect_url(self, confirmed=False):
        query = {
            'selected_block': self.request.POST.get('source_block', ''),
            'selected_holding': self.request.POST.get('source_holding', ''),
            'source_kind': self.request.POST.get('source_kind', 'grid'),
            'source_activity_id': self.request.POST.get('source_activity_id', ''),
            'source_activity_name': self.request.POST.get('source_activity_name', ''),
            'source_occurrence_id': self.request.POST.get('source_occurrence_id', ''),
            'source_group': self.request.POST.get('source_group', ''),
            'source_slot': self.request.POST.get('source_slot', ''),
            'target_slot': self.request.POST.get('target_slot', ''),
            'target_group': self.request.POST.get('target_group', ''),
            'action_type': self.request.POST.get('action_type', DEFAULT_NEW_MOVE_ACTION),
        }
        if confirmed:
            query['proposal_confirmed'] = '1'
        return f'{reverse("sched-detail", args=[self.object.pk])}?{urlencode(query)}'


class SchedMoveSave(SchedMoveConfirm):
    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        context = self.get_context_data(object=self.object)
        proposal_result = context['proposal_result']
        save_readiness = context['save_readiness']

        if not proposal_result or not proposal_result.get('source_identity_verified'):
            messages.error(
                request,
                'Move was not saved because the source proposal is invalid or stale.'
            )
        elif not save_readiness or not save_readiness['can_save']:
            messages.error(
                request,
                'Move was not saved because the recomputed proposal is not saveable.'
            )
        else:
            try:
                persist_manual_move(self.object, proposal_result)
            except MalformedSchedDataError as error:
                messages.error(request, error.operator_message)
                if request.user.is_staff:
                    messages.warning(request, f'Administrator diagnostic: {error.debug_detail}')
            except ValueError as error:
                messages.error(request, f'Move was not saved: {error}')
            else:
                messages.success(
                    request,
                    'Move saved as a manual override. It is now applied to the operational schedule.',
                )
                return HttpResponseRedirect(reverse('sched-detail', args=[self.object.pk]))

        return HttpResponseRedirect(self.get_proposal_redirect_url(confirmed=True))


class SchedDataRepair(DetailView):
    model = TheSched
    http_method_names = ['post']

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            repair_malformed_sched_data(self.object)
        except MalformedSchedDataError as error:
            messages.error(request, error.operator_message)
            if request.user.is_staff:
                messages.warning(request, f'Administrator diagnostic: {error.debug_detail}')
        except ValueError as error:
            messages.error(request, f'Operational data repair failed: {error}')
        else:
            messages.success(
                request,
                'Legacy operational data was repaired. Schedule edits can now be saved.',
            )
        return HttpResponseRedirect(reverse('sched-detail', args=[self.object.pk]))


class SchedCreate(CreateView):
    model = TheSched
    template_name = 'pay_end/sched_form.html'
    form_class = SchedForm
    success_url = reverse_lazy('sched-list')

class SchedUpdate(UpdateView):
    model = TheSched
    template_name = 'pay_end/sched_form.html'
    form_class = SchedForm
    success_url = reverse_lazy('sched-list')

class SchedDelete(DeleteView):
    model = TheSched
    template_name = 'pay_end/sched_confirm_delete.html'
    fields = "__all__"
    success_url = reverse_lazy('sched-list')
    context_object_name = "sched"

def class_view(request):
    location = Locations.objects.all()
    return render(request, 'pay_end/class_view.html', {'location':location,})

def add_location(request):
    submitted = False
    location = Locations.objects
    if request.method == "POST":
        form = LocationsForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect('/add_location?submitted=True')
        # if not form.is_valid: probably reload page? or redirect with submitted in the thing?
    else:
        form = LocationsForm()
        if 'submitted' in request.GET:
            submitted = True
    
    return render(request, 'pay_end/add_location.html', {'form': form, 'submitted' : submitted, 'location':location})

def add_course(request):
    submitted = False
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect('/add_course?submitted=True')
    else:
        form = CourseForm()
        if 'submitted' in request.GET:
            submitted = True
            
    return render(request, 'pay_end/add_course.html', {'form': form, 'submitted' : submitted })

def add_instructor(request):
    submitted=False
    if request.method == "POST":
        form = InstructorForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect('/add_instructor.html?sumbitted=True')
        else:
            return render(request, 'pay_end/home_pay.html',{})
    else:
        form = InstructorForm            
        if 'submitted' in request.GET:
            submitted=True
    return render(request, 'pay_end/add_instructor.html',{'form':form, 'submitted': submitted})

def add_school(request):
    submitted = False
    if request.method == "POST":
        form = SchoolsForm(request.POST)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect('/add_school?submitted=True')
            # added the ppl_temps to the front of ^^^
    else:
        form = SchoolsForm()
        if 'submitted' in request.GET:
            submitted = True

    return render(request, 'pay_end/add_school.html', {
        'form': form,
        'submitted' : submitted,
        'slot_summary': school_slot_accounting_summary(form),
    })

def home_paid(request):
    return render(request, 'pay_end/home_pay.html',{})

# not sure if this is working (belive not)
def search_results(request):
    print(request.POST.get('search_box'))
    if request.method == "POST":
        search_box = request.POST.get('search_box')

        return render(request, 'pay_end/search_results.html',{'search_box':search_box})
    
    else:    
        return render(request, 'pay_end/search_results.html',{})
