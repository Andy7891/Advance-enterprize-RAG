# Minimal demo of C4: generalizing a fixed, deliberately-conservative constant into a live
# formula can silently drop the SAFETY MARGIN that constant used to carry -- P6-M5's own real,
# disclosed finding (decision.md D-2026-09-30-06), reproduced here in miniature.
REAL_CONTEXT_WINDOW = 100  # a toy "real" limit, standing in for the real 4096
OLD_FIXED_BUDGET = 25  # picked by hand, ONCE, deliberately conservative (below the ~40 theoretical)


def old_fits(chunk_size):
    return chunk_size <= OLD_FIXED_BUDGET  # a fixed number -- includes a hidden safety cushion


def new_dynamic_fits(chunk_size, prompt_tokens, max_tokens):
    budget = REAL_CONTEXT_WINDOW - prompt_tokens - max_tokens  # the FULL theoretical number
    return chunk_size <= budget  # no cushion at all -- trusts the estimate completely


chunk = 36  # a chunk the OLD conservative rule would reject, but the new rule accepts
print("chunk size (ESTIMATED, chars/4-style):", chunk)
print("OLD fixed rule (budget=25):", old_fits(chunk))
# real budget = 100 - 30 - 30 = 40
print("NEW dynamic rule (real budget=40):", new_dynamic_fits(chunk, prompt_tokens=30, max_tokens=30))

print("\nbut if the real token count ever runs 15% higher than the chars/4 ESTIMATE says:")
real_chunk_tokens = int(chunk * 1.15)  # simulates the estimate being a bit too low, for real text
print("estimated:", chunk, "vs. what the model actually sees:", real_chunk_tokens)
print("would the REAL usage still fit the REAL window (40)?", real_chunk_tokens <= 40)
print("the OLD rule's built-in cushion (25, well below 40) would have caught this.")
print("the NEW dynamic rule has no such cushion -- it trusts the estimate completely.")
