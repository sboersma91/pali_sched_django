"""Canonical operational Schedule day and time-block definitions.

Generation, detail rendering, CSV export, and School slot accounting all depend on
this order. Keep stored slot keys and their order stable unless a deliberately
scoped scheduling-behavior change updates every consumer and its regression tests.
"""

DAYTIME_BLOCK = "daytime"
NIGHT_BLOCK = "night"

UNAVAILABLE_SLOT_VALUE = "g_box"
UNASSIGNED_SLOT_VALUE = "empty"

WEEKDAY_CHOICES = (
    ("Mon", "Monday"),
    ("Tue", "Tuesday"),
    ("Wed", "Wednesday"),
    ("Thur", "Thursday"),
    ("Fri", "Friday"),
)

SCHEDULE_DAYS = (
    {
        "key": "Mon",
        "name": "Monday",
        "slots": (
            {"label": "PM1", "key": "mon_pm1", "kind": DAYTIME_BLOCK},
            {"label": "PM2", "key": "mon_pm2", "kind": DAYTIME_BLOCK},
            {"label": "Night", "key": "mon_night", "kind": NIGHT_BLOCK},
        ),
    },
    {
        "key": "Tue",
        "name": "Tuesday",
        "slots": (
            {"label": "AM1", "key": "tue_am1", "kind": DAYTIME_BLOCK},
            {"label": "AM2", "key": "tue_am2", "kind": DAYTIME_BLOCK},
            {"label": "PM1", "key": "tue_pm1", "kind": DAYTIME_BLOCK},
            {"label": "PM2", "key": "tue_pm2", "kind": DAYTIME_BLOCK},
            {"label": "Night", "key": "tue_night", "kind": NIGHT_BLOCK},
        ),
    },
    {
        "key": "Wed",
        "name": "Wednesday",
        "slots": (
            {"label": "AM1", "key": "wed_am1", "kind": DAYTIME_BLOCK},
            {"label": "AM2", "key": "wed_am2", "kind": DAYTIME_BLOCK},
            {"label": "PM1", "key": "wed_pm1", "kind": DAYTIME_BLOCK},
            {"label": "PM2", "key": "wed_pm2", "kind": DAYTIME_BLOCK},
            {"label": "Night", "key": "wed_night", "kind": NIGHT_BLOCK},
        ),
    },
    {
        "key": "Thur",
        "name": "Thursday",
        "slots": (
            {"label": "AM1", "key": "thur_am1", "kind": DAYTIME_BLOCK},
            {"label": "AM2", "key": "thur_am2", "kind": DAYTIME_BLOCK},
            {"label": "PM1", "key": "thur_pm1", "kind": DAYTIME_BLOCK},
            {"label": "PM2", "key": "thur_pm2", "kind": DAYTIME_BLOCK},
            {"label": "Night", "key": "thur_night", "kind": NIGHT_BLOCK},
        ),
    },
    {
        "key": "Fri",
        "name": "Friday",
        "slots": (
            {"label": "AM1", "key": "fri_am1", "kind": DAYTIME_BLOCK},
            {"label": "AM2", "key": "fri_am2", "kind": DAYTIME_BLOCK},
        ),
    },
)

SCHEDULE_SLOTS = tuple(slot for day in SCHEDULE_DAYS for slot in day["slots"])
SCHEDULE_SLOT_KEYS = tuple(slot["key"] for slot in SCHEDULE_SLOTS)
SCHEDULE_SLOT_BLOCKS = tuple((slot["key"], slot["kind"]) for slot in SCHEDULE_SLOTS)

# These are operational trip-window boundaries, not simple day-start indexes.
# Their established values intentionally skip some arrival/departure-day blocks.
# Legacy aliases remain accepted for pre-standardization data compatibility.
DAY_OFFSETS = {"Mon": 0, "Tue": 5, "Wed": 10, "Thur": 15, "Fri": 19}
DAY_OFFSETS.update({"Tues": DAY_OFFSETS["Tue"], "Thurs": DAY_OFFSETS["Thur"]})

SCHEDULE_DISPLAY_VALUES = {
    UNAVAILABLE_SLOT_VALUE: "/////",
    UNASSIGNED_SLOT_VALUE: "****",
}
CSV_ACTIVITY_VALUES = {
    UNAVAILABLE_SLOT_VALUE: "Unavailable / Not present",
    UNASSIGNED_SLOT_VALUE: "Unassigned",
}

SCHEDULE_LEGEND = (
    {
        "value": SCHEDULE_DISPLAY_VALUES[UNAVAILABLE_SLOT_VALUE],
        "label": "unavailable or not present",
    },
    {
        "value": SCHEDULE_DISPLAY_VALUES[UNASSIGNED_SLOT_VALUE],
        "label": "unassigned available block",
    },
)
