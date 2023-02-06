from models import class_len, class_locs

def sort_subjects_presave(sender, instance, *args, **kwargs):
    if instance.sorted_subject_lst == 'fun':
        ropes = []
        various_one = []
        various_two = []
        one_block = []
        two_block = []
        night = []
        sorted_subjects = []

        instance.subjects = [sub.course_name for sub in instance.subject.all()]

        for c in range(len(instance.subjects)):
            if instance.subjects[c] in ["WM",'LCR','SLIDE']:
                ropes.append(instance.subjects[c])
            elif class_len[instance.subjects[c]] == 2 and class_locs[instance.subjects[c]] == 'Various':
                various_two.append(instance.subjects[c])
            elif class_len[instance.subjects[c]] == 2:
                two_block.append(instance.subjects[c])
            elif class_len[instance.subjects[c]] == 1 and class_locs[instance.subjects[c]]== 'Various' :
                various_one.append(instance.subjects[c])
            elif class_len[instance.subjects[c]] == 1:
                one_block.append(instance.subjects[c])
            elif class_len[instance.subjects[c]] == 0:
                # 'N' = 0 now.
                night.append(instance.subjects[c])
            else:
                print(c)
                print(instance.subjects[c])
                raise ValueError("There is no length for said class" )
        
        sorted_subjects.extend(ropes)
        sorted_subjects.extend(two_block)
        sorted_subjects.extend(various_two)
        sorted_subjects.extend(one_block)
        sorted_subjects.extend(various_one)
        sorted_subjects.extend(night)
        print(sorted_subjects)
        instance.sorted_subject_lst = sorted_subjects
        print('sorted list saved')
        print(*args)
        print(**kwargs)
        print(sender)

# pre_save.connect(sort_subjects_presave, sender=Schools)

def sorted_subjects_postsave(*args, **kwargs):
    pass

# post_save.connect(sorted_subjects_postsave, sender=Schools)
