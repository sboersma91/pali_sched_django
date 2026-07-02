from django.contrib import admin
from .models import Course, Instructor, Locations, Schools, TheSched

# admin.site.register(Locations)

@admin.register(Locations)
class LocationsAdmin(admin.ModelAdmin):
    list_display = ('loc_name', 'organization', 'loc_short', 'availible')
    list_filter = ('organization', 'availible')
    ordering = ('organization__name', 'loc_name', 'availible')
    search_fields = ('loc_name', 'loc_short', 'organization__name')

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('course_name', 'organization', 'course_len')
    list_filter = ('organization', 'course_len')
    ordering = ('organization__name', 'course_name', 'course_len')
    search_fields = ('course_name', 'abriviation', 'organization__name')

@admin.register(Schools)
class SchoolsAdmin(admin.ModelAdmin):
    list_display = ('school_name', 'organization', 'arrive', 'depart', 'total_students')
    list_filter = ('organization', 'arrive', 'depart')
    ordering = ('organization__name', 'school_name')
    search_fields = ('school_name', 'arrive', 'depart', 'organization__name')


@admin.register(TheSched)
class TheSchedAdmin(admin.ModelAdmin):
    list_display = ('sched_name', 'organization', 'timestamp_og')
    list_filter = ('organization',)
    ordering = ('organization__name', 'sched_name')
    search_fields = ('sched_name', 'organization__name')


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = ('fname', 'lname', 'organization', 'ropes_lead', 'school_lead')
    list_filter = ('organization', 'ropes_lead', 'school_lead')
    ordering = ('organization__name', 'lname', 'fname')
    search_fields = ('fname', 'lname', 'organization__name')
