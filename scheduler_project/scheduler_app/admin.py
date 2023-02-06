from django.contrib import admin
from .models import Locations, Course, Schools

# admin.site.register(Locations)

@admin.register(Locations)
class LocationsAdmin(admin.ModelAdmin):
    list_display = ('loc_name', 'loc_short', 'availible')
    ordering = ('loc_name', 'availible')
    search_fields = ('loc_name','loc_short')

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('course_name', 'course_len')
    ordering = ('course_name', 'course_len')
    search_fields = ('course_name', 'abriviation')

@admin.register(Schools)
class SchoolsAdmin(admin.ModelAdmin):
    list_display = ('school_name', 'arrive', 'depart', 'total_students')
    ordering = ('school_name',)
    search_fields = ('school_name', 'arrive','depart')