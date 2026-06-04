from django import forms
from django.db.models.base import Model
from django.forms import ModelForm, widgets
from .models import Course, Locations, Schools, Instructor, TheSched



class LocationsForm(ModelForm):
    class Meta:
        model = Locations
        fields = "__all__"
        ordering =('loc_name','availible')
        labels = {
            'loc_name':'Location', 
            'loc_short':'Abbrviation',
            'description':'Description', 
            'availible': 'Availible',
        }
        widgets = {
            'loc_name': forms.TextInput(attrs={'class':'form-control'}),
            'loc_short':forms.TextInput(attrs={'class':'form-control'}),
            'description':forms.TextInput(attrs={'class':'form-control'}),
        }

class CourseForm(ModelForm):
    class Meta:
        model = Course
        fields = '__all__'
        ordering = ('prog_name','prog_len')
        labels = {
            'prog_name':'Program Name',
            'abriviation': 'Abriviation',
            'primary_locz':'Primary',
            'prog_len': 'Program Length',
        }
        widgets = {
            'prog_name':forms.TextInput(attrs={'class':'form-control'}),
            'abriviation':forms.TextInput(attrs={'class':'form-control'}),
            'primary_locz': widgets.SelectMultiple(),
            # 'prog_len':widgets.NumberInput()
        }

class SchoolsForm(ModelForm):
    class Meta:
        model = Schools
        fields = "__all__"
        ordering= ('school_name',)
        lables = {
            'school_name': 'School Name',
            'subject':'Subjects',
            'arrive':'Arrival Day',
            'depart':'Departure Day',
            'total_students':'Total Students',
        }
        widgets = {
            'school_name':forms.TextInput(attrs={'class':'form-control'}),
            'subject':forms.SelectMultiple(attrs={'class':'form-control'}),
            'arrive': forms.Select(),
            'depart': forms.Select(),
            'total_students':widgets.NumberInput(),
        }

    def save(self, commit=True):
        school = super().save(commit=False)
        if commit:
            school.save()
            self.save_m2m()
            school.update_sorted_subject_lst()
            school.save(update_fields=['sorted_subject_lst'])
        return school

'''class SchedForm(ModelForm):
    class Meta:
        model = TheSched
        # need to edit fields once data is inserted
        fields = "__all__"
        labels = {
            'sched_name':'Schedule Name',
            'lst_of_school_names':'Schools.chools_list.all',
            # 'sched_data' : 'Schedule'
        }
        widgets = {
            'sched_name':forms.TextInput(attrs={'class':'form-control'}),
            'lst_of_school_names':forms.SelectMultiple(attrs={'class':'form-control'})
        }'''

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