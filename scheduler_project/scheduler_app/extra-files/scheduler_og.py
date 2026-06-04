# from pandas import DataFrame as DF
# there is missing code (not copied over) in the notebook


# a list of all the classes
master_locs = [
    "Over",
    "C21",
    "Slab",
    "Eagle A",
    "Eagle B",
    "Lodge",
    "Manz",
    "Riv",
    "Sher",
    "ACCT",
    "HV",
    "Pond",
    "Oak A",
    "Oak B",
    "SG",
    "SJ",
    "Chal",
    "SZ",
    "Denali",
    "Whit",
    "Amp",
    "Rosa",
    "GLO9",
    "Hawk",
    "KC",
    "Oak B",
    "Cdr",
    "LV",
    "PP",
    "HT",
    "Sher",
    "WM",
    "LCR",
    "SLIDE",
]

# Dict of class abbriviations to the length of the class
class_len = {
    "AERO": 2,
    "AN": 1,
    "ARCH": 1,
    "AS": 2,
    "ASTRO": "N",
    "ATB": 1,
    "BR": 1,
    "BS": 1,
    "CP": 1,
    "CS": 1,
    "CSI": 1,
    "D": "N",
    "DH": 1,
    "ED": 1,
    "FE": 2,
    "FWB": 1,
    "GE": 2,
    "G3": "N",
    "K": "N",
    "MN": "N",
    "NH": "N",
    "ORIENT": 1,
    "OS": 2,
    "PJ": "N",
    "QUAD": 1,
    "SQ": 1,
    "STAND": 1,
    "TB": 1,
    "UP": 1,
    "Ropes": 2,
    "SLIDE": 2,
    "LCR": 2,
    "WM": 2,
}
# class_locs and class_len in schools and sched == global variable
class_locs = {
    "AERO": ["Over", "C21", "Slab", "Eagle A", "Eagle B", "Lodge", "Manz"],
    "AN": ["Various"],
    "ARCH": ["Riv", "Sher", "Hawk", "KC"],
    "AS": ["HV", "HT", "FOX"],
    "ASTRO": ["Over", "ACCT", "C21", "Slab", "Pond"],
    "ATB": ["Various"],
    "BR": ["Oak A", "SG", "Manz", "Denali", "SJ"],
    "BS": ["SJ", "Whit", "Oak A", "Oak B"],
    "CP": ["Over", "Manz"],
    "CS": ["Oak", "Chal", "Manz", "Patio,", "SZ"],
    "CSI": ["SJ", "Oak A", "Eagle A", "Whit"],
    "D": ["Manz"],
    "DH": ["Various"],
    "ED": ["Oak A", "Oak B", "SJ", "Whit"],
    "FE": ["Various"],
    "FWB": ["Pond"],
    "GE": ["Manz", "Chal", "Oak A", "Eagle B"],
    "G3": ["Field"],
    "K": ["Oak A"],
    "MN": ["Oak A"],
    "NH": ["Various"],
    "ORIENT": ["GLO9", "LV", "PP"],
    "OS": ["A", "B", "C"],
    "PJ": ["Manz"],
    "QUAD": ["Quad"],
    "SQ": ["Cdr", "Chal"],
    "STAND": ["Patio", "Over", "SG"],
    "TB": ["Various"],
    "UP": ["Amp", "Rosa"],
    "Ropes": ["LCR", "SLIDE", "WM"],
    "LCR": ["LCR"],
    "SLIDE": ["SLIDE"],
    "WM": ["WM"],
}


"""
class School_2(forms.form):
    def __init__(self, q_set):
        # these are selected
        self.name = q_set.name
        
        # already in the DB
        self.subjects  = list(q_set.subject)
        self.arrive = q_set.arrive
        self.depart = q_set.depart
        total_students = q_set.total_students
        # make sure these are in the correct types
"""


class School:
    def __init__(
        self,
        name,
        subjects,
        arrive,
        depart,
        total_students,
    ):
        self.name = name
        self.subjects = subjects
        self.arrive = arrive
        self.depart = depart
        self.total_students = total_students

        self.sort_subjects()

        if self.total_students % 16 > 0:
            self.ag_num = (self.total_students // 16) + 1
        else:
            self.ag_num = self.total_students // 16

        self.create_ags()

    def sort_subjects(self):
        # Sorted order = Two Block w/ loc, two block no loc, one block w/ loc, one block no loc, night
        ropes = []
        various_one = []
        various_two = []
        one_block = []
        two_block = []
        night = []
        self.sorted_subjects = []

        for c in range(len(self.subjects)):
            if self.subjects[c] in ["WM", "LCR", "SLIDE"]:
                ropes.append(self.subjects[c])
            elif (
                class_len[self.subjects[c]] == 2
                and class_locs[self.subjects[c]] == "Various"
            ):
                various_two.append(self.subjects[c])
            elif class_len[self.subjects[c]] == 2:
                two_block.append(self.subjects[c])
            elif (
                class_len[self.subjects[c]] == 1
                and class_locs[self.subjects[c]] == "Various"
            ):
                various_one.append(self.subjects[c])
            elif class_len[self.subjects[c]] == 1:
                one_block.append(self.subjects[c])
            elif class_len[self.subjects[c]] == "N":
                night.append(self.subjects[c])
            else:
                raise ValueError

        self.sorted_subjects.extend(ropes)
        self.sorted_subjects.extend(two_block)
        self.sorted_subjects.extend(various_two)
        self.sorted_subjects.extend(one_block)
        self.sorted_subjects.extend(various_one)
        self.sorted_subjects.extend(night)
        return self.sorted_subjects

    def create_ags(self):
        self.ag_subjects = {}
        for ind in range(self.ag_num):
            self.ag_subjects[self.name + " " + str(ind)] = self.sorted_subjects
        return self.ag_subjects

    def __str__(self):
        return self.name


class TheSched:
    def __init__(self, *args):
        self.schools = args

    def create_sched(self):
        count = 0
        for school in self.schools:
            count += school.ag_num

        global sched
        sched = {
            "mon_pm1": ["g_box"] * count,
            "mon_pm2": ["g_box"] * count,
            "mon_night": ["g_box"] * count,
            "tue_am1": ["g_box"] * count,
            "tue_am2": ["g_box"] * count,
            "tue_pm1": ["g_box"] * count,
            "tue_pm2": ["g_box"] * count,
            "tue_night": ["g_box"] * count,
            "wed_am1": ["g_box"] * count,
            "wed_am2": ["g_box"] * count,
            "wed_pm1": ["g_box"] * count,
            "wed_pm2": ["g_box"] * count,
            "wed_night": ["g_box"] * count,
            "thur_am1": ["g_box"] * count,
            "thur_am2": ["g_box"] * count,
            "thur_pm1": ["g_box"] * count,
            "thur_pm2": ["g_box"] * count,
            "thur_night": ["g_box"] * count,
            "fri_am1": ["g_box"] * count,
            "fri_am2": ["g_box"] * count,
            "ags": [],
            "classes_needed": [],
            # WARNING: you may feel the urge to move ags to the start... this will SCREW up the day offsets :0
        }

        group_count = 0

        day_offset = {"Mon": 0, "Tue": 5, "Wed": 10, "Thur": 15, "Fri": 19}
        #  day_end_offsett = {'Mon':, 'Tues':, 'Wed':,'Fri':}

        for school in self.schools:
            for i in range(school.ag_num):
                sched["ags"].append(school.name + " " + str(i))
                sched["classes_needed"].append(school.sorted_subjects[::-1])
            for key in list(sched.keys())[
                day_offset[school.arrive] : day_offset[school.depart]
            ]:
                for i in range(group_count, group_count + school.ag_num):
                    sched[key][i] = "empty"
                    # gray box means school is gone
            group_count += school.ag_num

        self.sched = sched
        # return self
        #  return pd.DataFrame.from_dict(sched)

        time_slots = list(self.sched.keys())[:20]
        locs_open = {
            loc: {slot: 3 if loc in class_locs["Ropes"] else 1 for slot in time_slots}
            for loc in master_locs
        }
        locs_open["Various"] = {slot: 100 for slot in time_slots}
        locs_open["Manz"] = {slot: 10 for slot in time_slots}

        def search_open_slot(
            locs_open,
            n=0,
            slots=time_slots,
            schedule=self.sched,
        ):
            if n >= len(schedule["classes_needed"]):
                return True
            classes_needed = schedule["classes_needed"][n]

            if len(classes_needed) == 0:
                return search_open_slot(
                    locs_open,
                    n + 1,
                    schedule=self.sched,
                )
            current_class = classes_needed.pop()

            for current_loc in class_locs[current_class]:
                current_len = class_len[current_class]

                if current_len == "N":
                    for slot in slots:
                        if "night" not in slot:
                            continue

                        if (
                            locs_open[current_loc][slot] > 0
                            and schedule[slot][n] == "empty"
                        ):
                            locs_open[current_loc][slot] -= 1
                            schedule[slot][n] = current_class
                            if search_open_slot(
                                locs_open,
                                n,
                                schedule=self.sched,
                            ):
                                return True
                            schedule[slot][n] = "empty"
                            locs_open[current_loc][slot] += 1

                if current_len == 2:
                    for slot in slots:
                        if "1" not in slot:
                            continue
                        slot2 = slot[:-1] + "2"

                        if (
                            locs_open[current_loc][slot] > 0
                            and schedule[slot][n] == "empty"
                        ):
                            if (
                                locs_open[current_loc][slot2] > 0
                                and schedule[slot2][n] == "empty"
                            ):
                                locs_open[current_loc][slot] -= 1
                                schedule[slot][n] = current_class
                                locs_open[current_loc][slot2] -= 1
                                schedule[slot2][n] = current_class
                                if search_open_slot(
                                    locs_open,
                                    n,
                                    schedule=self.sched,
                                ):
                                    return True
                                schedule[slot][n] = "empty"
                                locs_open[current_loc][slot] += 1
                                schedule[slot2][n] = "empty"
                                locs_open[current_loc][slot2] += 1

                if current_len == 1:
                    for slot in slots:
                        if "night" in slot:
                            continue
                        if (
                            locs_open[current_loc][slot] > 0
                            and schedule[slot][n] == "empty"
                        ):
                            locs_open[current_loc][slot] -= 1
                            schedule[slot][n] = current_class
                            if search_open_slot(
                                locs_open,
                                n,
                                schedule=self.sched,
                            ):
                                return True
                            schedule[slot][n] = "empty"
                            locs_open[current_loc][slot] += 1

            classes_needed.append(current_class)
            return False

        search_open_slot(
            locs_open,
            schedule=self.sched,
        )
        return self.sched


if __name__ == "__main__":
    lakes = School(
        name="Lakes",
        subjects=["WM", "FE", "NH", "ASTRO", "UP", "SQ", "TB"],
        arrive="Mon",
        depart="Wed",
        total_students=70,
    )
    oak_park = School(
        "Oak_Park",
        ["LCR", "GE", "NH", "PJ", "SQ", "TB", "ATB"],
        arrive="Wed",
        depart="Fri",
        total_students=85,
    )
    river = School(
        "River",
        ["AERO", "NH", "D", "CSI", "SLIDE", "ARCH", "AN"],
        arrive="Mon",
        depart="Wed",
        total_students=30,
    )

    smooth = TheSched(lakes, oak_park, river)
    s = smooth.create_sched()
    print(s)
