"""Repository-owned realistic prepared-demo scenario.

This module is runtime data.  It deliberately contains natural identifiers
(names and instructor name pairs), never source database primary keys.
"""

from datetime import date


WORKING_DEMO_SCENARIO_VERSION = 'working-v1'

LOCATIONS = (
    ('Acct', 'ACCT', True), ('Ampatheater', 'Amp', True),
    ('Cabin 21', 'C21', False), ('Cedar', 'Cdr', True),
    ('Chalet', 'Chal', True), ('Denali', 'Denal', True),
    ('Eagle A', 'E-A', True), ('Eagle B', 'Eag B', False),
    ('Field', 'Field', True), ('Fox', 'Fox', True),
    ('Glow 9', 'GLO9', True), ('Hawkeye', 'Hawk', True),
    ('Hidden Trail', 'HT', True), ('Hidden Valley', 'HV', True),
    ('Kings Court', 'KC', True), ('Location Not available', 'noloc', False),
    ('Lodge', 'Lodge', False), ('Lower Coyote Ridge', 'LCR', True),
    ('Lower Valley', 'LV', True), ('Manzana', 'Manz', True),
    ('OS A', 'A', True), ('OS B', 'OS B', True), ('OS C', 'OS C', True),
    ('Oak A', 'OakA', True), ('Oak B', 'OakB', True),
    ('Overlook', None, True), ('Patio', 'Patio', True),
    ('Peach Pit', 'PP', True), ('Pond', 'Pond', True),
    ('Quad Zip', 'Quad', True), ('River', 'Riv', True),
    ('Rosa', 'Rosa', True), ('SZ', 'SZ', True),
    ('San Grandito', 'SG', True), ('San Josinto', 'SJ', True),
    ('Savage Retreat Center', 'SRC', True), ('Sher', 'Sher', True),
    ('Sheraden', 'Sher', True), ('Slab', 'Slab', False),
    ('Slide', 'SLIDE', True), ('Various', 'Vari', True),
    ('Warrior Mountian', 'WM', True), ('Whitney', 'Whit', True),
)

ACTIVITIES = (
    ('Advanced Team Building', 'ATB', 1, ('Various',)),
    ('Aero Dynamics', 'Aero', 2, ('Cabin 21', 'Eagle A', 'Eagle B', 'Lodge', 'Manzana', 'Overlook', 'Slab')),
    ('Animal Survivor', 'AS', 1, ('Hidden Trail', 'Hidden Valley')),
    ('Archery', 'Arch', 1, ('Hawkeye', 'Kings Court', 'River', 'Sher')),
    ('Art in Nature', 'AN', 1, ('Various',)),
    ('Arts and Crafts', 'A&C', 0, ('Savage Retreat Center',)),
    ('Astronomy', 'Astro', 0, ('Acct', 'Cabin 21', 'Overlook', 'Pond', 'Slab')),
    ('Balloon Rescue', 'BR', 1, ('Denali', 'Manzana', 'Oak A', 'Oak B', 'San Grandito', 'San Josinto')),
    ('Building Support', 'BS', 1, ('Oak A', 'Oak B', 'San Josinto', 'Whitney')),
    ('CS', 'CS', 1, ('Chalet', 'Manzana', 'Oak A', 'Oak B')),
    ('Community Puzzle', 'CP', 1, ('Manzana', 'Overlook')),
    ('Crime Scene Investigation', 'CSI', 1, ('Oak A', 'Oak B', 'San Josinto', 'Whitney')),
    ('DH', 'DH', 1, ('Various',)), ('Dance', 'D', 0, ('Manzana',)),
    ('Energy Dilema', 'ED', 1, ('Oak A', 'Oak B', 'San Josinto', 'Whitney')),
    ('Forest Ecology', 'FE', 2, ('Various',)), ('Fresh Water Bio', 'FWB', 1, ('Pond',)),
    ('Games Games Games', 'G3', 0, ('Field',)),
    ('Geology and Engineering', 'GE', 2, ('Chalet', 'Eagle A', 'Eagle B', 'Manzana', 'Oak A', 'Oak B')),
    ('Karioke', 'K', 0, ('Eagle A', 'San Josinto', 'Sheraden')),
    ('LCR', 'LCR', 2, ('Lower Coyote Ridge',)), ('Movie Night', 'MN', 0, ('Oak A',)),
    ('Night Hike', 'NH', 0, ('Various',)),
    ('No loc activity', '-loca', 1, ('Location Not available',)),
    ('Orienteering', 'Orien', 1, ('Glow 9', 'Lower Valley', 'Peach Pit')),
    ('Outdoor Skills', 'OS', 2, ('OS A', 'OS B', 'OS C')),
    ('Pali Jepordy', 'PJ', 0, ('Eagle A', 'Manzana')),
    ('Quad Zip', 'Quad', 1, ('Quad Zip',)),
    ('Ropes', 'Ropes', 2, ('Lower Coyote Ridge', 'Slide', 'Warrior Mountian')),
    ('Slide', 'Slide', 2, ('Slide',)), ('Squid', 'SQ', 1, ('Cedar', 'Chalet')),
    ('TeamBuilding', 'TB', 1, ('Various',)), ('WM', 'WM', 2, ('Warrior Mountian',)),
    ('Where do you Stand', 'STAND', 1, ('Overlook', 'Patio', 'San Grandito')),
)

SCHOOLS = (
    ('National Park', 'Wed', 'Fri', 33, 3, date(2022, 12, 15),
     ('Geology and Engineering', 'LCR', 'Night Hike', 'Pali Jepordy', 'Squid', 'TeamBuilding', 'Where do you Stand')),
    ('Pickles', 'Mon', 'Wed', 88, 6, date(2022, 11, 11),
     ('Advanced Team Building', 'Animal Survivor', 'Art in Nature', 'Astronomy', 'Forest Ecology', 'Games Games Games', 'LCR')),
    ('River', 'Mon', 'Wed', 15, 1, date(2022, 12, 15),
     ('Aero Dynamics', 'Archery', 'Crime Scene Investigation', 'Dance', 'Night Hike', 'Quad Zip', 'Slide')),
    ('Seattle', 'Mon', 'Fri', 25, 2, date(2026, 6, 21),
     ('Aero Dynamics', 'Archery', 'Art in Nature', 'Astronomy', 'Crime Scene Investigation', 'Energy Dilema', 'Forest Ecology', 'Karioke', 'Movie Night', 'Night Hike', 'Outdoor Skills', 'Quad Zip', 'Slide', 'Squid', 'TeamBuilding')),
    ('Sparta', 'Mon', 'Wed', 50, 4, date(2022, 12, 15),
     ('Archery', 'Astronomy', 'Balloon Rescue', 'Forest Ecology', 'Night Hike', 'Squid', 'WM')),
    ('Yellowstone', 'Mon', 'Wed', 150, 10, date(2026, 6, 19),
     ('Archery', 'Art in Nature', 'Astronomy', 'LCR', 'Night Hike', 'Slide', 'Squid')),
)

SCHEDULES = (
    ('Halfweek- Lakes, Oak Park, River ONLY', ('National Park', 'River', 'Sparta'), 'complete'),
    ('Hallz and Test school outragousness', ('Pickles', 'Yellowstone'), 'infeasible'),
    ('Lakes, Oak Park, River -- Presents.', ('National Park', 'River', 'Sparta'), 'complete'),
    ('Missing Loc and Oak Park', ('National Park', 'Yellowstone'), 'infeasible'),
    ('Small Test 2 schools, 3 total ags.', ('River', 'Seattle'), 'complete'),
    ('Test 2', ('National Park', 'Pickles', 'River', 'Sparta'), 'infeasible'),
    ('testing3', ('National Park', 'Pickles', 'River', 'Sparta'), 'infeasible'),
)

INSTRUCTORS = (
    ('Angie', 'Dixton'), ('Bobby', 'Goldfish'), ('Dixie', 'Travers'),
    ('Gerold', 'Shaw'), ('MIchael', 'Brownstone'), ('Michael', 'Easton'),
    ('Michael', 'Jordan'), ('Phillip', 'Glenn'), ('Pizza', 'Pie'),
    ('Shawn', 'Smith'), ('Tommy', 'Garland'), ('Zoey', 'Shaw'),
)

PARTICIPATION = (
    (('Pizza', 'Pie'), 'Halfweek- Lakes, Oak Park, River ONLY', 'not_participating'),
)
AVAILABILITY = (
    (('Angie', 'Dixton'), 'Halfweek- Lakes, Oak Park, River ONLY', 'tue_am2', 'available'),
    (('Angie', 'Dixton'), 'Halfweek- Lakes, Oak Park, River ONLY', 'tue_pm1', 'unavailable'),
    (('Dixie', 'Travers'), 'Halfweek- Lakes, Oak Park, River ONLY', 'tue_pm1', 'available'),
    (('Dixie', 'Travers'), 'Halfweek- Lakes, Oak Park, River ONLY', 'tue_pm2', 'unavailable'),
)

WORKING_DEMO_SCENARIO = {
    'version': WORKING_DEMO_SCENARIO_VERSION,
    'locations': LOCATIONS,
    'activities': ACTIVITIES,
    'schools': SCHOOLS,
    'schedules': SCHEDULES,
    'instructors': INSTRUCTORS,
    'participation': PARTICIPATION,
    'availability': AVAILABILITY,
}

EXPECTED_COUNTS = {
    'locations': 43, 'activities': 34, 'schools': 6, 'schedules': 7,
    'instructors': 12, 'participation': 1, 'availability': 4,
}
