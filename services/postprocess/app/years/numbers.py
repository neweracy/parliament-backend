"""
Number word lookup tables for the Year_Corrector.

Ported unchanged from lib/location-correction/year-correction.js.
"""

# Spoken ones words → numeric value (0–19)
ONES: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

# Spoken tens words → numeric value (20–90)
TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

# Spoken ordinal words → (day number, suffix)
ORDINALS: dict[str, tuple[int, str]] = {
    "first": (1, "st"),
    "second": (2, "nd"),
    "third": (3, "rd"),
    "fourth": (4, "th"),
    "fifth": (5, "th"),
    "sixth": (6, "th"),
    "seventh": (7, "th"),
    "eighth": (8, "th"),
    "ninth": (9, "th"),
    "tenth": (10, "th"),
    "eleventh": (11, "th"),
    "twelfth": (12, "th"),
    "thirteenth": (13, "th"),
    "fourteenth": (14, "th"),
    "fifteenth": (15, "th"),
    "sixteenth": (16, "th"),
    "seventeenth": (17, "th"),
    "eighteenth": (18, "th"),
    "nineteenth": (19, "th"),
    "twentieth": (20, "th"),
    "twenty-first": (21, "st"),
    "twenty-second": (22, "nd"),
    "twenty-third": (23, "rd"),
    "twenty-fourth": (24, "th"),
    "twenty-fifth": (25, "th"),
    "twenty-sixth": (26, "th"),
    "twenty-seventh": (27, "th"),
    "twenty-eighth": (28, "th"),
    "twenty-ninth": (29, "th"),
    "thirtieth": (30, "th"),
    "thirty-first": (31, "st"),
}

# Spoken month names → numeric month (1–12)
MONTHS: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# Numeric month → display name
MONTH_NAMES: dict[int, str] = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}
