# Minimal demo of C3: `x or default` silently replaces ANY falsy value (not just missing
# ones) with the default -- the exact bug LocalLLM's first draft had for its model name.
FALSY_BUT_REAL_VALUES = ["", 0, False, [], "   "]  # values a caller might deliberately pass
DEFAULT = "fallback-value"  # stands in for settings.llm_model


def resolve_with_or(value):
    return value or DEFAULT  # the REJECTED, buggy pattern LocalLLM's first draft used


def resolve_with_is_none(value):
    return DEFAULT if value is None else value  # the FIXED pattern LocalLLM actually uses


print(f"{'value passed':<14}{'or-based result':<20}{'is None-based result':<22}should differ from DEFAULT?")
for value in [*FALSY_BUT_REAL_VALUES, None]:  # tries every falsy value, plus the real "not given" case
    via_or = resolve_with_or(value)  # what the buggy pattern produces
    via_is_none = resolve_with_is_none(value)  # what the correct pattern produces
    should_differ = value is not None  # only a REAL, explicitly-passed value should differ from DEFAULT
    print(f"{value!r:<14}{via_or!r:<20}{via_is_none!r:<22}{should_differ}")

print()
print("The row for None is the ONLY row where both columns should say the same thing --")
print("every OTHER row is a real, deliberately-passed value that `or` wrongly replaced anyway.")
