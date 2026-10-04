# Minimal demo of C1: a FIXED, hand-picked budget number vs. a DYNAMIC one computed fresh from
# real inputs every call -- the exact upgrade P6-M5 made to P6-M4's own apply_budget.
CONTEXT_WINDOW = 20  # a tiny toy "context window" so the numbers stay easy to trace by hand


def count_tokens(text):  # a toy stand-in for count_tokens_approx (the real project function)
    return max(1, len(text) // 4)  # same chars/4 rule this project actually uses


def fits_fixed(chunk_size, FIXED_BUDGET=10):  # the OLD way: one number, decided ahead of time
    return chunk_size <= FIXED_BUDGET  # never looks at the real prompt or max_tokens at all


def fits_dynamic(chunk_size, prompt, max_tokens):  # the NEW way: computed fresh, every call
    budget = CONTEXT_WINDOW - count_tokens(prompt) - max_tokens  # real room left, right now
    return chunk_size <= budget  # compares against THIS call's real, measured budget


print("--- a short prompt ---")
short_prompt = "hi"
print("fixed says:  ", fits_fixed(8))
print("dynamic says:", fits_dynamic(8, short_prompt, max_tokens=5))

print("\n--- a much longer prompt, the SAME chunk size ---")
long_prompt = "x" * 40
print("fixed says:  ", fits_fixed(8))  # never changes -- it doesn't know the prompt got longer
print("dynamic says:", fits_dynamic(8, long_prompt, max_tokens=5))  # correctly notices less room

print("\nThe fixed rule gives the SAME answer both times -- it can't see the real prompt at all.")
print("The dynamic rule's answer changes, because it actually measures what's really left.")
