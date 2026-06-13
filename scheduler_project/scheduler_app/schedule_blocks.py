"""Canonical schedule-block definitions and derived display/accounting views."""

SCHEDULE_BLOCKS = [
    {"key": "mon_pm1", "day": "Monday", "label": "PM1", "kind": "daytime"},
    {"key": "mon_pm2", "day": "Monday", "label": "PM2", "kind": "daytime"},
    {"key": "mon_night", "day": "Monday", "label": "Night", "kind": "night"},
    {"key": "tue_am1", "day": "Tuesday", "label": "AM1", "kind": "daytime"},
    {"key": "tue_am2", "day": "Tuesday", "label": "AM2", "kind": "daytime"},
    {"key": "tue_pm1", "day": "Tuesday", "label": "PM1", "kind": "daytime"},
    {"key": "tue_pm2", "day": "Tuesday", "label": "PM2", "kind": "daytime"},
    {"key": "tue_night", "day": "Tuesday", "label": "Night", "kind": "night"},
    {"key": "wed_am1", "day": "Wednesday", "label": "AM1", "kind": "daytime"},
    {"key": "wed_am2", "day": "Wednesday", "label": "AM2", "kind": "daytime"},
    {"key": "wed_pm1", "day": "Wednesday", "label": "PM1", "kind": "daytime"},
    {"key": "wed_pm2", "day": "Wednesday", "label": "PM2", "kind": "daytime"},
    {"key": "wed_night", "day": "Wednesday", "label": "Night", "kind": "night"},
    {"key": "thur_am1", "day": "Thursday", "label": "AM1", "kind": "daytime"},
    {"key": "thur_am2", "day": "Thursday", "label": "AM2", "kind": "daytime"},
    {"key": "thur_pm1", "day": "Thursday", "label": "PM1", "kind": "daytime"},
    {"key": "thur_pm2", "day": "Thursday", "label": "PM2", "kind": "daytime"},
    {"key": "thur_night", "day": "Thursday", "label": "Night", "kind": "night"},
    {"key": "fri_am1", "day": "Friday", "label": "AM1", "kind": "daytime"},
    {"key": "fri_am2", "day": "Friday", "label": "AM2", "kind": "daytime"},
]

SCHEDULE_BLOCK_KEYS = [block["key"] for block in SCHEDULE_BLOCKS]
SCHEDULE_SLOT_BLOCKS = [(block["key"], block["kind"]) for block in SCHEDULE_BLOCKS]

# Preserve the legacy slice offsets used by generation and School accounting.
SCHEDULE_DAY_OFFSETS = {
    "Mon": 0,
    "Tue": 5,
    "Tues": 5,
    "Wed": 10,
    "Thur": 15,
    "Thurs": 15,
    "Fri": 19,
}

# Rendering and CSV export consume this display-oriented view.
SCHEDULE_DAYS = []
for block in SCHEDULE_BLOCKS:
    if not SCHEDULE_DAYS or SCHEDULE_DAYS[-1]["name"] != block["day"]:
        SCHEDULE_DAYS.append({"name": block["day"], "slots": []})
    SCHEDULE_DAYS[-1]["slots"].append({"label": block["label"], "key": block["key"]})
