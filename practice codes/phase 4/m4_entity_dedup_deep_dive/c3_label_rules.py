# C3: each kind of name has its own rules. No database needed.
from src.graph.dedup import _company_words, _location_display, _location_words, _person_words  # the four rule functions

checks = [                                                                   # (kind, rule, first spelling, second spelling)
    ("person: honorific", _person_words, "Dr. Karen Phillips", "Karen Phillips"),        # a title is not part of the name
    ("person: initial", _person_words, "Jordan K. Bishop", "Jordan Bishop"),             # an initial IS a word, so these differ
    ("company: suffix", _company_words, "GreenValley Insurance, LLC", "GreenValley Insurance"),  # a legal suffix is not the name
    ("company: 'The'", _company_words, "The Acme", "Acme"),                              # a leading "The" is not the name
    ("place: state code", _location_words, "San Francisco, CA", "San Francisco, California"),    # CA and California are one state
    ("place: two states", _location_words, "Portland, OR", "Portland, ME"),              # same city name, different states
]
for kind, rule, first, second in checks:                                     # run every rule on its pair
    a, b = rule(first), rule(second)                                         # the word tuples the matcher compares
    print(f"{kind:<18} same={a == b!s:<5} {a} vs {b}")                       # True only when the rule treats them as one name

print("\nplace display:", _location_display("san francisco, ca"))            # the constructed name a place is keyed on
