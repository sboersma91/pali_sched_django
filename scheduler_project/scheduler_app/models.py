from django.db.models import CASCADE
from django.db import models
from django.db.models.fields import BooleanField, CharField, IntegerField, SmallIntegerField, TextField, DateField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models.fields.related import ManyToManyField, ForeignKey
from django.db import connection
from django.db.models import JSONField

# from django.db.models.signals import pre_save, post_save
# from .custom_fields import CommaSepField


wk_days = (('Mon','Monday'),('Tue', 'Tuesday'), ('Wed','Wednesday'), ('Thur', 'Thursday'),('Fri','Friday'))
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

def initialize_scheduling_data():
    global scheduling_data_initialized
    if scheduling_data_initialized:
        return

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
    attending_year = DateField(auto_created=True) # this is to distinguish between the same school coming back yr after yr.Need to add blank=true and auto_now of year

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

    def update_sorted_subject_lst(self):
        initialize_scheduling_data()
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
            elif class_len[self.subjects[c]] == 2 and class_locs[self.subjects[c]] == 'Various':
                various_two.append(self.subjects[c])
            elif class_len[self.subjects[c]] == 2:
                two_block.append(self.subjects[c])
            elif class_len[self.subjects[c]] == 1 and class_locs[self.subjects[c]]== 'Various' :
                various_one.append(self.subjects[c])
            elif class_len[self.subjects[c]] == 1:
                one_block.append(self.subjects[c])
            elif class_len[self.subjects[c]] == 0:
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
    lst_of_school_names = Schools.schools_list.all()
    sched_data = JSONField()
    timestamp_og = DateField(auto_now_add=True)

 

    def __str__(self):
        return self.sched_name

    @property
    def create_sched(self): #save(self, *args, **kwargs):
        initialize_scheduling_data()
        count=0
        for school in self.lst_of_school_names: #because of the foreign key this will only reference 1 school object
            count+= school.ag_num
        global sched
        sched = {
            "mon_pm1": ['g_box']* count,
            "mon_pm2": ['g_box']* count,
            'mon_night':['g_box'] *count,

            "tue_am1": ['g_box']* count,
            "tue_am2": ['g_box']* count,
            "tue_pm1": ['g_box']* count,
            "tue_pm2": ['g_box']* count,
            'tue_night':['g_box'] *count,
            
            "wed_am1": ['g_box']* count,
            "wed_am2": ['g_box']* count,
            "wed_pm1": ['g_box']* count,
            "wed_pm2": ['g_box']* count,
            'wed_night':['g_box'] *count,
            
            "thur_am1":['g_box']* count,
            "thur_am2":['g_box']* count,
            "thur_pm1":['g_box']* count,
            "thur_pm2":['g_box']* count,
            "thur_night":['g_box'] *count,
            
            "fri_am1": ['g_box']* count,
            "fri_am2": ['g_box']* count,
            'ags':[],
            'classes_needed':[],
            # WARNING: you may feel the urge to move ags to the start... this will SCREW up the day offsets :0
        }
          
        group_count=0
        day_offset = {'Mon':0, "Tue":5, "Wed":10, "Thur":15, "Fri":19}
        #  day_end_offsett = {'Mon':, 'Tues':, 'Wed':,'Fri':}
        for school in self.lst_of_school_names:
            for i in range(school.ag_num):
                sched['ags'].append(school.school_name + ' ' + str(i))
                # ------------------
                sched['classes_needed'].append(school.sorted_subject_lst.split(',')[::-1])
            for key in list(sched.keys())[day_offset[school.arrive]:day_offset[school.depart]]:    
                for i in range(group_count,group_count+school.ag_num):
                            sched[key][i]='empty'
                            # gray box means school is gone
            group_count += school.ag_num
        
        self.sched = sched
        time_slots = list(self.sched.keys())[:20]
        locs_open = {
            loc:{slot: 3 if loc in class_locs['Ropes'] else 1 for slot in time_slots} for loc in master_locs
        }  
        locs_open['Various'] = {slot:100 for slot in time_slots}
        locs_open['Manz'] = {slot:10 for slot in time_slots}
        
        def search_open_slot(locs_open, n=0, slots=time_slots,schedule=self.sched,):
            if n >= len(schedule['classes_needed']):
                return True
            
            classes_needed = schedule['classes_needed'][n]
            
            if len(classes_needed) == 0:
                return search_open_slot(locs_open, n+1, schedule=self.sched,)
            
            current_class = classes_needed.pop()
            for current_loc in class_locs[current_class]: 
                current_len = class_len[current_class]

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
        
        search_open_slot(locs_open,schedule=self.sched,)
        self.sched.popitem()
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