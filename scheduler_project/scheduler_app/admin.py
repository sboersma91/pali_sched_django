from django.contrib import admin
from .models import (
    ActivityCertificationRequirement,
    Certification,
    Course,
    Instructor,
    InstructorCertification,
    InstructorLeadershipRole,
    LeadershipRole,
    Locations,
    Schools,
    TheSched,
)

# admin.site.register(Locations)


@admin.register(Certification)
class CertificationAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization')
    list_filter = ('organization',)
    ordering = ('organization__name', 'name')
    search_fields = ('name', 'organization__name')

@admin.register(Locations)
class LocationsAdmin(admin.ModelAdmin):
    list_display = ('loc_name', 'organization', 'loc_short', 'availible')
    list_filter = ('organization', 'availible')
    ordering = ('organization__name', 'loc_name', 'availible')
    search_fields = ('loc_name', 'loc_short', 'organization__name')


class ActivityCertificationRequirementInline(admin.TabularInline):
    model = ActivityCertificationRequirement
    extra = 0


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('course_name', 'organization', 'course_len')
    list_filter = ('organization', 'course_len')
    ordering = ('organization__name', 'course_name', 'course_len')
    search_fields = ('course_name', 'abriviation', 'organization__name')
    exclude = ('required_instructor_count',)
    inlines = (ActivityCertificationRequirementInline,)


@admin.register(ActivityCertificationRequirement)
class ActivityCertificationRequirementAdmin(admin.ModelAdmin):
    list_display = ('course', 'certification')
    list_filter = ('course__organization', 'certification')
    ordering = ('course__organization__name', 'course__course_name', 'certification__name')
    search_fields = ('course__course_name', 'certification__name')

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


class InstructorCertificationInline(admin.TabularInline):
    model = InstructorCertification
    extra = 0


class InstructorLeadershipRoleInline(admin.TabularInline):
    model = InstructorLeadershipRole
    extra = 0


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = ('fname', 'lname', 'organization', 'ropes_lead', 'school_lead')
    list_filter = ('organization', 'ropes_lead', 'school_lead')
    ordering = ('organization__name', 'lname', 'fname')
    search_fields = ('fname', 'lname', 'organization__name')
    inlines = (InstructorCertificationInline, InstructorLeadershipRoleInline)


@admin.register(InstructorCertification)
class InstructorCertificationAdmin(admin.ModelAdmin):
    list_display = ('instructor', 'certification')
    list_filter = ('instructor__organization', 'certification')
    ordering = ('instructor__organization__name', 'instructor__lname', 'instructor__fname')
    search_fields = ('instructor__fname', 'instructor__lname', 'certification__name')


@admin.register(LeadershipRole)
class LeadershipRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'organization')
    list_filter = ('organization',)
    ordering = ('organization__name', 'name')
    search_fields = ('name', 'organization__name')


@admin.register(InstructorLeadershipRole)
class InstructorLeadershipRoleAdmin(admin.ModelAdmin):
    list_display = ('instructor', 'leadership_role')
    list_filter = ('instructor__organization', 'leadership_role')
    ordering = ('instructor__organization__name', 'instructor__lname', 'instructor__fname')
    search_fields = ('instructor__fname', 'instructor__lname', 'leadership_role__name')
