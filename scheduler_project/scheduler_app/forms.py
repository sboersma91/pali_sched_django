from django import forms
from django.core.exceptions import ValidationError
from django.db.models.base import Model
from django.forms import ModelForm, widgets
from django.db.models.functions import Lower
from .models import Course, Locations, Schools, Instructor, TheSched
from .school_accounting import calculate_school_slot_accounting



class LocationsForm(ModelForm):
    class Meta:
        model = Locations
        fields = "__all__"
        labels = {
            'loc_name': 'Location Name',
            'loc_short': 'Abbreviation',
            'description': 'Operator Notes',
            'availible': 'Available for Scheduling',
        }
        help_texts = {
            'loc_short': 'Short label used in schedules and operational displays. Maximum 5 characters.',
            'description': 'Add access details, restrictions, setup notes, or other information staff should know.',
            'availible': 'Uncheck to keep this location record while marking it unavailable for scheduling.',
        }
        widgets = {
            'loc_name': forms.TextInput(attrs={'class': 'form-control'}),
            'loc_short': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'availible': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

class CourseForm(ModelForm):
    COURSE_LENGTH_CHOICES = (
        (2, 'Two-block daytime activity'),
        (1, 'One-block daytime activity'),
        (0, 'Night activity'),
    )

    course_len = forms.TypedChoiceField(
        choices=COURSE_LENGTH_CHOICES,
        coerce=int,
        label='Schedule Length',
        help_text='Schedule length controls how this activity consumes schedule blocks.',
        widget=forms.RadioSelect(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        locations_by_availability = {True: [], False: []}
        for location in Locations.objects.order_by('-availible', Lower('loc_name')):
            locations_by_availability[location.availible].append(location)

        self.fields['primary_locs'].choices = [
            (
                group_label,
                [
                    (
                        location.pk,
                        f'{location.loc_name} — {location.loc_short or "No abbreviation"}'
                        f'{" — unavailable" if not location.availible else ""}',
                    )
                    for location in locations_by_availability[available]
                ],
            )
            for available, group_label in (
                (True, 'Available Locations'),
                (False, 'Unavailable Locations'),
            )
            if locations_by_availability[available]
        ]

    class Meta:
        model = Course
        fields = '__all__'
        labels = {
            'course_name': 'Activity Name',
            'abriviation': 'Abbreviation',
            'primary_locs': 'Primary Locations',
        }
        help_texts = {
            'primary_locs': 'Select every location where this activity can normally be scheduled.',
        }
        widgets = {
            'course_name': forms.TextInput(attrs={'class': 'form-control'}),
            'abriviation': forms.TextInput(attrs={'class': 'form-control'}),
            'primary_locs': forms.CheckboxSelectMultiple(),
        }

class SchoolsForm(ModelForm):
    ACTIVITY_GROUPS = (
        (2, 'Two-block daytime activities', '2 daytime blocks'),
        (1, 'One-block daytime activities', '1 daytime block'),
        (0, 'Night activities', 'night activity'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        courses_by_length = {
            course_len: [] for course_len, _group_label, _cost_label in self.ACTIVITY_GROUPS
        }
        for course in Course.objects.order_by('-course_len', Lower('course_name')):
            courses_by_length[course.course_len].append(course)

        self.fields['subject'].choices = [
            (
                group_label,
                [(course.pk, f'{course.course_name} — {cost_label}') for course in courses_by_length[course_len]],
            )
            for course_len, group_label, cost_label in self.ACTIVITY_GROUPS
            if courses_by_length[course_len]
        ]

    def clean(self):
        cleaned_data = super().clean()
        arrive = cleaned_data.get('arrive')
        depart = cleaned_data.get('depart')
        subjects = cleaned_data.get('subject', Course.objects.none())
        if not arrive or not depart:
            return cleaned_data

        summary = calculate_school_slot_accounting(arrive, depart, subjects)
        errors = []
        for label, selected_key, required_key, status_key in (
            ('Daytime blocks', 'selected_daytime', 'required_daytime', 'daytime_status'),
            ('Night blocks', 'selected_night', 'required_night', 'night_status'),
            ('Total blocks', 'selected_total', 'required_total', 'total_status'),
        ):
            if summary[selected_key] != summary[required_key]:
                errors.append(
                    f'{label}: required {summary[required_key]}, selected {summary[selected_key]} '
                    f'({summary[status_key]}).'
                )

        if errors:
            raise ValidationError([
                'Selected activities must exactly match the required trip blocks before the School can be saved.',
                *errors,
            ])
        return cleaned_data

    class Meta:
        model = Schools
        fields = "__all__"
        ordering= ('school_name',)
        labels = {
            'school_name': 'School Name',
            'subject':'Subjects',
            'arrive':'Arrival Day',
            'depart':'Departure Day',
            'total_students':'Total Students',
        }
        widgets = {
            'school_name':forms.TextInput(attrs={'class':'form-control'}),
            'subject': forms.CheckboxSelectMultiple(),
            'arrive': forms.Select(attrs={'class': 'form-select'}),
            'depart': forms.Select(attrs={'class': 'form-select'}),
            'total_students': widgets.NumberInput(attrs={'class': 'form-control'}),
            'ag_num': widgets.NumberInput(attrs={'class': 'form-control'}),
            'attending_year': widgets.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'sorted_subject_lst': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def save(self, commit=True):
        school = super().save(commit=False)
        if commit:
            school.save()
            self.save_m2m()
            school.update_sorted_subject_lst()
            school.save(update_fields=['sorted_subject_lst'])
        return school

class SchedForm(ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['schools'].queryset = Schools.schools_list.order_by(Lower('school_name'))

    def save(self, commit=True):
        schedule = super().save(commit=False)
        if schedule.sched_data is None:
            schedule.sched_data = {}
        if commit:
            schedule.save()
            self.save_m2m()
        return schedule

    class Meta:
        model = TheSched
        fields = ('sched_name', 'schools')
        labels = {
            'sched_name': 'Schedule Name',
            'schools': 'Schools to Schedule',
        }
        help_texts = {
            'sched_name': 'Use a clear name that helps operators identify this schedule.',
            'schools': 'Select the Schools that should be generated together in this Schedule.',
        }
        widgets = {
            'sched_name': forms.TextInput(attrs={'class': 'form-control'}),
            'schools': forms.CheckboxSelectMultiple(),
        }

class InstructorForm(ModelForm):
    class Meta:
        model = Instructor
        fields = ('fname', 'lname', 'ropes_lead', 'school_lead',
        # 'days_incabin', 
        'cpr', 'firstaid')
        labels = {'fname': 'First Name', 'lname': 'Last Name', 'ropes_lead':'Ropes Lead', 
        # 'days_incabin':'Days in Cabin', 
        'cpr': 'CPR', 'firstaid': 'First Aid' }
        widgets = {
            'fname': forms.TextInput(attrs={'class':'form-control'}),
            'lname': forms.TextInput(attrs={'class':'form-control'}),
            # 'ropes_lead': forms.CheckboxInput(attrs={'class':'form-control'}),
            # 'days_incabin': forms.(attrs={'class':'form-control'}),
            'cpr': widgets.SelectMultiple(attrs={'class':'form-control'}),
            # 'firstaid': forms.(attrs={'class':'form-control'}),
        }