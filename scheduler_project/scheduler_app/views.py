from re import template
from django.shortcuts import render
from .models import Locations, Course, Schools, TheSched
from .forms import InstructorForm, LocationsForm, CourseForm, SchoolsForm
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy

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
    template_name = "pay_end/locations_form.html"
    # fields = ['loc_name', 'loc_short', 'description','availible']
    fields ="__all__"
    success_url = reverse_lazy('location-list')

class LocationUpdate(UpdateView):
    model = Locations
    template_name = "pay_end/locations_form.html"
    fields = "__all__"
    # ['loc_name', 'loc_short', 'description','availible']
    success_url = reverse_lazy('location-list')
    # ideally change this success url to the detail version of the location.

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
    template_name = 'pay_end/course_form.html'
    fields = "__all__"
    success_url = reverse_lazy('course-list')

class CourseUpdate(UpdateView):
    model = Course
    template_name = 'pay_end/course_form.html'
    fields = "__all__"
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

class SchoolCreate(CreateView):
    model = Schools
    template_name = 'pay_end/school_form.html'
    fields = "__all__"
    success_url = reverse_lazy('school-list')

class SchoolUpdate(UpdateView):
    model = Schools
    template_name = 'pay_end/school_form.html'
    fields = "__all__"
    success_url = reverse_lazy('school-list')

class SchoolDelete(DeleteView):
    model = Schools 
    template_name = 'pay_end/school_confirm_delete.html'
    fields = "__all__"
    success_url = reverse_lazy('school-list')
    context_object_name = "school"

'''Starting of function based views'''
class SchedList(ListView):
    model = TheSched
    template_name = 'pay_end/sched_list.html'
    context_object_name = 'sched'
    ordering = ['sched_name']

class SchedDetail(DetailView):
    model = TheSched
    template_name = 'pay_end/sched_detail.html'
    context_object_name = 'sched'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        schedule = self.object.create_sched
        schedule_days = [
            {
                'name': 'Monday',
                'slots': [
                    {'label': 'PM1', 'key': 'mon_pm1'},
                    {'label': 'PM2', 'key': 'mon_pm2'},
                    {'label': 'Night', 'key': 'mon_night'},
                ],
            },
            {
                'name': 'Tuesday',
                'slots': [
                    {'label': 'AM1', 'key': 'tue_am1'},
                    {'label': 'AM2', 'key': 'tue_am2'},
                    {'label': 'PM1', 'key': 'tue_pm1'},
                    {'label': 'PM2', 'key': 'tue_pm2'},
                    {'label': 'Night', 'key': 'tue_night'},
                ],
            },
            {
                'name': 'Wednesday',
                'slots': [
                    {'label': 'AM1', 'key': 'wed_am1'},
                    {'label': 'AM2', 'key': 'wed_am2'},
                    {'label': 'PM1', 'key': 'wed_pm1'},
                    {'label': 'PM2', 'key': 'wed_pm2'},
                    {'label': 'Night', 'key': 'wed_night'},
                ],
            },
            {
                'name': 'Thursday',
                'slots': [
                    {'label': 'AM1', 'key': 'thur_am1'},
                    {'label': 'AM2', 'key': 'thur_am2'},
                    {'label': 'PM1', 'key': 'thur_pm1'},
                    {'label': 'PM2', 'key': 'thur_pm2'},
                    {'label': 'Night', 'key': 'thur_night'},
                ],
            },
            {
                'name': 'Friday',
                'slots': [
                    {'label': 'AM1', 'key': 'fri_am1'},
                    {'label': 'AM2', 'key': 'fri_am2'},
                ],
            },
        ]
        schedule_rows = []
        for ag_index, ag in enumerate(schedule.get('ags', [])):
            cells = []
            for day in schedule_days:
                for slot in day['slots']:
                    slot_values = schedule.get(slot['key'], [])
                    cells.append(slot_values[ag_index] if ag_index < len(slot_values) else '')
            schedule_rows.append({'ag': ag, 'cells': cells})

        context['schedule_days'] = schedule_days
        context['schedule_rows'] = schedule_rows
        return context

class SchedCreate(CreateView):
    model = TheSched
    template_name = 'pay_end/sched_form.html'
    fields = "__all__"
    success_url = reverse_lazy('sched-list')

class SchedUpdate(UpdateView):
    model = TheSched
    template_name = 'pay_end/sched_form.html'
    fields = "__all__"
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
        if form.is_valid:
            form.save()
            return HttpResponseRedirect('/add_location?submitted=True')
        # if not form.is_valid: probably reload page? or redirect with submitted in the thing?
    else:
        form = LocationsForm
        if 'submitted' in request.GET:
            submitted = True
    
    return render(request, 'pay_end/add_location.html', {'form': form, 'submitted' : submitted, 'location':location})

def add_course(request):
    submitted = False
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid:
            form.save()
            return HttpResponseRedirect('/add_course?submitted=True')
    else:
        form = CourseForm
        if 'submitted' in request.GET:
            submitted = True
            
    return render(request, 'pay_end/add_course.html', {'form': form, 'submitted' : submitted })

def add_instructor(request):
    submitted=False
    if request.method == "POST":
        form = InstructorForm
        if form.is_valid:
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
        if form.is_valid:
            form.save()
            return HttpResponseRedirect('/add_school?submitted=True')
            # added the ppl_temps to the front of ^^^
    else:
        form = SchoolsForm
        if 'submitted' in request.GET:
            submitted = True

    return render(request, 'pay_end/add_school.html', {'form': form, 'submitted' : submitted })

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

