from django.db.models import CASCADE
from django.db import models
from django.db.models.fields import BooleanField, CharField, IntegerField, SmallIntegerField, TextField, DateField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models.fields.related import ManyToManyField, ForeignKey
from django.db import connection
from django.db.models import JSONField

from .schedule_blocks import (
    DAY_OFFSETS,
    SCHEDULE_SLOT_BLOCKS,
    SCHEDULE_SLOT_KEYS,
    UNASSIGNED_SLOT_VALUE,
    UNAVAILABLE_SLOT_VALUE,
    WEEKDAY_CHOICES,
)

# from django.db.models.signals import pre_save, post_save
# from .custom_fields import CommaSepField


wk_days = WEEKDAY_CHOICES
# ('stored in DB', "shown on screen" )

class Locations(models.Model):
    loc_name = CharField(max_length=100, unique=True)
    # capicty = SmallIntegerField()
    loc_short = CharField(max_length=5, null=True, default='5 character max')
    description = TextField(default= 'Notes for the new people', blank=True, null=True)
    availible = BooleanField(default=True)

    def __str__(self):
        return self.loc_name

    # a secondary locations, it cannot also be  foreign key otherwise it errors
    # coures length Night == 0 will need to add help tool here.
    # num_staff = SmallIntegerField()

class Course(models.Model):
    course_name =  CharField(max_length=120,unique=True,)
    abriviation = CharField(max_length=5, unique=True, blank=True, null=True, default='5 character max')
    primary_locs =  ManyToManyField(Locations)
    course_len = SmallIntegerField(
        default=1, 
        validators=[MaxValueValidator(2), MinValueValidator(0)]
    )
    # need to restrict range currently select outside of range, and unknown what not_valid will do.

    def __str__(self):
        return self.course_name


master_locs = []
def create_master_locs():
    for l in Locations.objects.raw('SELECT loc_name, id FROM scheduler_app_locations WHERE availible = 1'):
        master_locs.append(l.loc_name)
    return master_locs

class_len = {}
class_locs = {}
def create_class_len_dict():
    for t11 in Course.objects.raw('SELECT * FROM scheduler_app_course'):
        class_len[t11.course_name] = t11.course_len
    return class_locs
def create_class_locs_dict():
    with connection.cursor() as cursor:
        query = """SELECT scheduler_app_course.course_name, scheduler_app_locations.loc_name 
        FROM 
        scheduler_app_course 
        LEFT JOIN 
        scheduler_app_course_primary_locs 
        ON 
        scheduler_app_course.id = scheduler_app_course_primary_locs.course_id 
        LEFT JOIN 
        scheduler_app_locations 
        ON 
        scheduler_app_course_primary_locs.locations_id = scheduler_app_locations.id 
        WHERE scheduler_app_locations.availible = 1"""
        cursor.execute(query)
        row = cursor.fetchall()

    for r in row:
        if r[0] not in class_locs:
            class_locs[r[0]]= [r[1],]
            continue
        if r[0] in class_locs and r[1] not in class_locs[r[0]]:
            class_locs[r[0]].append(r[1])
    return class_locs


scheduling_data_initialized = False
GENERATION_SEARCH_MAX_ATTEMPTS = 100000


class GenerationSearchLimitExceeded(Exception):
    pass


def location_capacity_for_generation(location_name, class_locs_lookup):
    if location_name == 'Various':
        return 100
    if location_name == 'Manz':
        return 10
    if location_name in class_locs_lookup.get('Ropes', []):
        return 3
    return 1


def initialize_scheduling_data(force=False):
    global scheduling_data_initialized
    if scheduling_data_initialized and not force:
        return

    master_locs.clear()
    class_locs.clear()
    class_len.clear()
    create_master_locs()
    create_class_locs_dict()
    create_class_len_dict()
    scheduling_data_initialized = True


class Schools(models.Model):
    school_name = CharField(max_length=150, unique=True,)
    subject = ManyToManyField(Course)
    arrive = CharField(max_length=50, choices=wk_days)
    depart = CharField(max_length=50, choices=wk_days)
    total_students = IntegerField()
    ag_num = IntegerField(default=1)
    # Distinguishes repeat visits; value is supplied through the existing forms.
    attending_year = DateField()

    sorted_subject_lst = TextField(blank=True)
    # this needs to be a textfield -- 
    
    schools_list = models.Manager()


    '''@property
    def ag_numbers(self):
        if self.total_students % 16 > 0:
            self.ag_num = (self.total_students//16) + 1
        else:
            self.ag_num = self.total_students//16
        return self.ag_num
        
        # Because it messes up how you access the number it is now going to be a manuel entry -- ideally it will later become an auto calculation that can be overriden
        '''

    def update_sorted_subject_lst(self, class_len_lookup=None, class_locs_lookup=None):
        if class_len_lookup is None or class_locs_lookup is None:
            initialize_scheduling_data(force=True)
            class_len_lookup = class_len
            class_locs_lookup = class_locs
        # Sorted order = Two Block w/ loc, two block no loc, one block w/ loc, one block no loc, night
        ropes = []
        various_one = []
        various_two = []
        one_block = []
        two_block = []
        night = []
        self.sorted_subjects = []

        self.subjects = [sub.course_name for sub in self.subject.all()]

        for c in range(len(self.subjects)):
            if self.subjects[c] in ["WM",'LCR','SLIDE']:
                ropes.append(self.subjects[c])
            elif class_len_lookup[self.subjects[c]] == 2 and class_locs_lookup[self.subjects[c]] == 'Various':
                various_two.append(self.subjects[c])
            elif class_len_lookup[self.subjects[c]] == 2:
                two_block.append(self.subjects[c])
            elif class_len_lookup[self.subjects[c]] == 1 and class_locs_lookup[self.subjects[c]]== 'Various' :
                various_one.append(self.subjects[c])
            elif class_len_lookup[self.subjects[c]] == 1:
                one_block.append(self.subjects[c])
            elif class_len_lookup[self.subjects[c]] == 0:
                # 'N' = 0 now.
                night.append(self.subjects[c])
            else:
                print(c)
                print(self.subjects[c])
                raise ValueError("There is no length for said class" )
        
        self.sorted_subjects.extend(ropes)
        self.sorted_subjects.extend(two_block)
        self.sorted_subjects.extend(various_two)
        self.sorted_subjects.extend(one_block)
        self.sorted_subjects.extend(various_one)
        self.sorted_subjects.extend(night)
        self.sorted_subject_lst = ','.join(self.sorted_subjects)
        return self.sorted_subject_lst

    @property
    def sort_subjects(self):
        return self.update_sorted_subject_lst().replace(',', ', ')
    
    def save(self, *args, **kwargs):
        if self.pk:
            self.update_sorted_subject_lst()
        # Turn this into a string and it will save as needed 
        super().save(*args, **kwargs)

    @property
    def create_ags(self):
        self.ag_subjects = {}
        for ind in range(self.ag_num):
            self.ag_subjects[self.school_name + ' ' + str(ind)] = self.sorted_subjects
        return self.ag_subjects
    
    def __str__(self):
        return self.school_name # + ' ' + self.attending_year # need to change data type of attending_year??


class TheSched(models.Model):
    sched_name = CharField(max_length=150)
    schools = ManyToManyField(Schools)
    sched_data = JSONField(null=True)
    timestamp_og = DateField(auto_now_add=True)

 

    def __str__(self):
        return self.sched_name

    def get_scheduling_diagnostics(self, class_locs_lookup=None):
        class_locs_lookup = class_locs if class_locs_lookup is None else class_locs_lookup
        diagnostics = []
        for school in self.schools.all():
            for activity in school.subject.all():
                if not activity.primary_locs.exists():
                    reason = "Activity is not connected to any scheduling Locations."
                elif not activity.primary_locs.filter(availible=True).exists():
                    reason = "Activity has no available scheduling Locations."
                elif activity.course_name not in class_locs_lookup:
                    reason = "Activity does not appear in current scheduling Location lookups."
                else:
                    continue

                diagnostics.append({
                    "school": school.school_name,
                    "activity": activity.course_name,
                    "reason": reason,
                })
        return diagnostics

    def get_activity_capacity_diagnostics(self, class_locs_lookup, class_len_lookup, master_locs_lookup):
        diagnostics = []
        slot_blocks = list(SCHEDULE_SLOT_BLOCKS)
        master_locs_lookup = set(master_locs_lookup)
        special_capacity_locs = {'Various', 'Manz'}

        for school in self.schools.all():
            available_slot_blocks = slot_blocks[DAY_OFFSETS[school.arrive]:DAY_OFFSETS[school.depart]]
            available_slot_keys = {slot_key for slot_key, _slot_kind in available_slot_blocks}
            night_slots = [
                slot_key
                for slot_key, _slot_kind in available_slot_blocks
                if 'night' in slot_key
            ]
            daytime_slots = [
                slot_key
                for slot_key, _slot_kind in available_slot_blocks
                if 'night' not in slot_key
            ]
            paired_daytime_footprints = [
                (slot_key, slot_key[:-1] + '2')
                for slot_key in daytime_slots
                if '1' in slot_key and slot_key[:-1] + '2' in available_slot_keys
            ]

            for activity in school.subject.all():
                activity_name = activity.course_name
                activity_len = class_len_lookup[activity_name]
                eligible_locs = [
                    loc
                    for loc in class_locs_lookup[activity_name]
                    if loc in master_locs_lookup or loc in special_capacity_locs
                ]
                location_capacity = sum(
                    location_capacity_for_generation(loc, class_locs_lookup)
                    for loc in eligible_locs
                )
                if activity_len == 0:
                    slot_capacity = len(night_slots)
                elif activity_len == 2:
                    slot_capacity = len(paired_daytime_footprints)
                else:
                    slot_capacity = len(daytime_slots)

                capacity = location_capacity * slot_capacity
                demand = school.ag_num
                if capacity >= demand:
                    continue

                placement_word = "placement" if demand == 1 else "placements"
                availability_word = "is" if capacity == 1 else "are"
                diagnostics.append({
                    "type": "activity_capacity_insufficient",
                    "severity": "warning",
                    "school": school.school_name,
                    "activity": activity_name,
                    "demand": demand,
                    "capacity": capacity,
                    "reason": (
                        f"{school.school_name} — {activity_name} needs {demand} {placement_word}, "
                        f"but only {capacity} {availability_word} available in this trip window."
                    ),
                })
        return diagnostics

    @property
    def create_sched(self): #save(self, *args, **kwargs):
        initialize_scheduling_data(force=True)
        local_class_locs = {
            activity_name: list(locations)
            for activity_name, locations in class_locs.items()
        }
        local_class_len = dict(class_len)
        local_master_locs = list(master_locs)
        self.generation_runtime_diagnostics = []
        self.generation_diagnostics = self.get_scheduling_diagnostics(local_class_locs)
        if self.generation_diagnostics:
            self.generation_complete = False
            return {}

        count=0
        for school in self.schools.all():
            count+= school.ag_num
        global sched
        sched = {slot_key: [UNAVAILABLE_SLOT_VALUE] * count for slot_key in SCHEDULE_SLOT_KEYS}
        sched['ags'] = []
        sched['classes_needed'] = []
          
        group_count=0
        day_offset = {'Mon':0, "Tue":5, "Wed":10, "Thur":15, "Fri":19}
        #  day_end_offsett = {'Mon':, 'Tues':, 'Wed':,'Fri':}
        for school in self.schools.all():
            school.update_sorted_subject_lst(local_class_len, local_class_locs)
            sorted_subjects = [subject for subject in school.sorted_subject_lst.split(',') if subject]
            for i in range(school.ag_num):
                sched['ags'].append(school.school_name + ' ' + str(i))
                # ------------------
                sched['classes_needed'].append(sorted_subjects[::-1])
            for key in list(sched.keys())[day_offset[school.arrive]:day_offset[school.depart]]:    
                for i in range(group_count,group_count+school.ag_num):
                            sched[key][i] = UNASSIGNED_SLOT_VALUE
                            # gray box means school is gone
            group_count += school.ag_num
        
        self.sched = sched
        capacity_diagnostics = self.get_activity_capacity_diagnostics(
            local_class_locs,
            local_class_len,
            local_master_locs,
        )
        if capacity_diagnostics:
            self.generation_complete = False
            self.generation_runtime_diagnostics.extend(capacity_diagnostics)
            self.sched.pop('classes_needed', None)
            return self.sched

        time_slots = list(SCHEDULE_SLOT_KEYS)
        locs_open = {
            loc:{slot: 3 if loc in local_class_locs['Ropes'] else 1 for slot in time_slots} for loc in local_master_locs
        }  
        locs_open['Various'] = {slot:100 for slot in time_slots}
        locs_open['Manz'] = {slot:10 for slot in time_slots}
        search_attempts = {"count": 0}
        
        def search_open_slot(locs_open, n=0, slots=time_slots,schedule=self.sched,):
            search_attempts["count"] += 1
            if search_attempts["count"] > GENERATION_SEARCH_MAX_ATTEMPTS:
                raise GenerationSearchLimitExceeded

            if n >= len(schedule['classes_needed']):
                return True
            
            classes_needed = schedule['classes_needed'][n]
            
            if len(classes_needed) == 0:
                return search_open_slot(locs_open, n+1, schedule=self.sched,)
            
            current_class = classes_needed.pop()
            for current_loc in local_class_locs[current_class]:
                current_len = local_class_len[current_class]

                if current_len == 0: 
                    for slot in slots:
                        if 'night'not in slot:
                            continue
                            
                        if locs_open[current_loc][slot]>0 and schedule[slot][n]=='empty':
                            locs_open[current_loc][slot]-=1
                            schedule[slot][n]= current_class
                            if search_open_slot(locs_open, n, schedule=self.sched,):
                                return True
                            schedule[slot][n]='empty'
                            locs_open[current_loc][slot]+=1

                if current_len == 2:
                    # print('length is 2')
                    for slot in slots:
                        if '1' not in slot:
                            continue
                        slot2 = slot[:-1]+'2'
                        if locs_open[current_loc][slot]>0 and schedule[slot][n]=='empty': 
                            if locs_open[current_loc][slot2]>0 and schedule[slot2][n]=='empty':
                                locs_open[current_loc][slot]-=1
                                schedule[slot][n]= current_class
                                locs_open[current_loc][slot2]-=1
                                schedule[slot2][n]= current_class
                                if search_open_slot(locs_open, n,schedule=self.sched,):
                                    return True
                                schedule[slot][n]='empty'
                                locs_open[current_loc][slot]+=1
                                schedule[slot2][n]='empty'
                                locs_open[current_loc][slot2]+=1

                if current_len == 1:
                    # print('current len == 1') Here is where it is getting "stuck...."
                    for slot in slots:
                        if "night" in slot:
                            continue
                        if locs_open[current_loc][slot]>0 and schedule[slot][n]=='empty':
                            locs_open[current_loc][slot]-=1
                            schedule[slot][n]= current_class
                            if search_open_slot(locs_open, n,schedule=self.sched,):
                                return True
                            schedule[slot][n]='empty'
                            locs_open[current_loc][slot]+=1
                
            classes_needed.append(current_class)
            return False
        
        try:
            self.generation_complete = search_open_slot(locs_open,schedule=self.sched,)
        except GenerationSearchLimitExceeded:
            self.generation_complete = False
            self.generation_runtime_diagnostics.append({
                "type": "search_limit_exceeded",
                "severity": "warning",
                "reason": (
                    "Schedule generation stopped because the recursive assignment "
                    f"search reached the safety limit of {GENERATION_SEARCH_MAX_ATTEMPTS} attempts."
                ),
            })
        self.sched.pop('classes_needed', None)
        # self.sched_data = self.sched.copy()

        # super().save(*args, **kwargs)
        return self.sched 


class Instructor(models.Model):
    fname = CharField(max_length=450)
    lname = CharField(max_length=450)
    ropes_lead = BooleanField()
    school_lead = BooleanField()
    # days_incabin = SmallIntegerField()
    # this will have to be a calculated field.
    cpr = BooleanField(choices=(("fr", "fresh"), ('hr', 'house'),('boolean field','boolean field')))
    firstaid = CharField(choices= (('yes','yes'),('jack','jack'), ('charfield','charfield')),max_length=100)
    
    def __str__(self):
        return self.fname + ' ' + self.lname
