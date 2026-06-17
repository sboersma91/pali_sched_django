from .models import Course


SCHEDULE_SLOT_BLOCKS = [
    ('mon_pm1', 'daytime'),
    ('mon_pm2', 'daytime'),
    ('mon_night', 'night'),
    ('tue_am1', 'daytime'),
    ('tue_am2', 'daytime'),
    ('tue_pm1', 'daytime'),
    ('tue_pm2', 'daytime'),
    ('tue_night', 'night'),
    ('wed_am1', 'daytime'),
    ('wed_am2', 'daytime'),
    ('wed_pm1', 'daytime'),
    ('wed_pm2', 'daytime'),
    ('wed_night', 'night'),
    ('thur_am1', 'daytime'),
    ('thur_am2', 'daytime'),
    ('thur_pm1', 'daytime'),
    ('thur_pm2', 'daytime'),
    ('thur_night', 'night'),
    ('fri_am1', 'daytime'),
    ('fri_am2', 'daytime'),
]
DAY_OFFSETS = {'Mon': 0, 'Tue': 5, 'Tues': 5, 'Wed': 10, 'Thur': 15, 'Thurs': 15, 'Fri': 19}


def calculate_school_slot_accounting(arrive, depart, selected_courses):
    required_slots = []
    if arrive in DAY_OFFSETS and depart in DAY_OFFSETS:
        required_slots = SCHEDULE_SLOT_BLOCKS[DAY_OFFSETS[arrive]:DAY_OFFSETS[depart]]

    selected_courses = list(selected_courses)
    selected_daytime = sum(course.course_len for course in selected_courses if course.course_len > 0)
    selected_night = sum(1 for course in selected_courses if course.course_len == 0)
    required_daytime = sum(1 for slot in required_slots if slot[1] == 'daytime')
    required_night = sum(1 for slot in required_slots if slot[1] == 'night')

    def status(selected, required):
        difference = selected - required
        if difference > 0:
            return f'over by {difference}'
        if difference < 0:
            return f'under by {abs(difference)}'
        return 'balanced'

    selected_total = selected_daytime + selected_night
    required_total = required_daytime + required_night
    return {
        'required_daytime': required_daytime,
        'required_night': required_night,
        'required_total': required_total,
        'selected_daytime': selected_daytime,
        'selected_night': selected_night,
        'selected_total': selected_total,
        'daytime_status': status(selected_daytime, required_daytime),
        'night_status': status(selected_night, required_night),
        'total_status': status(selected_total, required_total),
        'has_trip_window': bool(required_slots),
    }


def _form_values(form, field_name):
    if form.is_bound:
        if hasattr(form.data, 'getlist'):
            return form.data.getlist(field_name)
        value = form.data.get(field_name, [])
        return value if isinstance(value, list) else [value]

    instance = getattr(form, 'instance', None)
    if field_name == 'subject' and instance and instance.pk:
        return list(instance.subject.values_list('pk', flat=True))

    return []


def _form_value(form, field_name):
    if form.is_bound:
        return form.data.get(field_name, '')

    instance = getattr(form, 'instance', None)
    return getattr(instance, field_name, '') if instance else ''


def school_slot_accounting_summary(form):
    selected_courses = Course.objects.filter(pk__in=_form_values(form, 'subject'))
    return calculate_school_slot_accounting(
        _form_value(form, 'arrive'),
        _form_value(form, 'depart'),
        selected_courses,
    )
