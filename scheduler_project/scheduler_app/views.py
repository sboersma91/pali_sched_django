import csv

from django.shortcuts import get_object_or_404, render
from .models import Locations, Course, Schools, TheSched
from .forms import CourseForm, InstructorForm, LocationsForm, SchedForm, SchoolsForm
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.text import slugify

from .school_accounting import school_slot_accounting_summary

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

SCHEDULE_DAYS = [
    {'name': 'Monday', 'slots': [
        {'label': 'PM1', 'key': 'mon_pm1'}, {'label': 'PM2', 'key': 'mon_pm2'}, {'label': 'Night', 'key': 'mon_night'},
    ]},
    {'name': 'Tuesday', 'slots': [
        {'label': 'AM1', 'key': 'tue_am1'}, {'label': 'AM2', 'key': 'tue_am2'},
        {'label': 'PM1', 'key': 'tue_pm1'}, {'label': 'PM2', 'key': 'tue_pm2'}, {'label': 'Night', 'key': 'tue_night'},
    ]},
    {'name': 'Wednesday', 'slots': [
        {'label': 'AM1', 'key': 'wed_am1'}, {'label': 'AM2', 'key': 'wed_am2'},
        {'label': 'PM1', 'key': 'wed_pm1'}, {'label': 'PM2', 'key': 'wed_pm2'}, {'label': 'Night', 'key': 'wed_night'},
    ]},
    {'name': 'Thursday', 'slots': [
        {'label': 'AM1', 'key': 'thur_am1'}, {'label': 'AM2', 'key': 'thur_am2'},
        {'label': 'PM1', 'key': 'thur_pm1'}, {'label': 'PM2', 'key': 'thur_pm2'}, {'label': 'Night', 'key': 'thur_night'},
    ]},
    {'name': 'Friday', 'slots': [
        {'label': 'AM1', 'key': 'fri_am1'}, {'label': 'AM2', 'key': 'fri_am2'},
    ]},
]
SCHEDULE_DISPLAY_VALUES = {'g_box': '/////', 'empty': '****'}
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        schedule = self.object.create_sched
        schedule_days = SCHEDULE_DAYS
        display_values = SCHEDULE_DISPLAY_VALUES
        schedule_rows = []
        for ag_index, ag in enumerate(schedule.get('ags', [])):
            cells = []
            for day in schedule_days:
                for slot in day['slots']:
                    slot_values = schedule.get(slot['key'], [])
                    value = slot_values[ag_index] if ag_index < len(slot_values) else ''
                    cells.append(display_values.get(value, value))
            schedule_rows.append({'ag': ag, 'cells': cells})

        context['selected_schools'] = self.object.schools.order_by('school_name')
        context['schedule_days'] = schedule_days
        context['schedule_rows'] = schedule_rows
        context['generation_diagnostics'] = getattr(self.object, 'generation_diagnostics', [])
        context['generation_blocked'] = bool(context['generation_diagnostics'])
        context['generation_complete'] = getattr(self.object, 'generation_complete', True)
        context['generation_succeeded'] = not context['generation_blocked'] and context['generation_complete']
        context['generation_incomplete'] = not context['generation_blocked'] and not context['generation_complete']
        return context

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

