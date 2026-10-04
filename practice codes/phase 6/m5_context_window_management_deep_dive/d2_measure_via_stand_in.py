# Minimal demo of C3: solving a circular-dependency measurement problem by calling the REAL
# builder with a trivial/empty input, instead of writing and maintaining a second builder.
def build_user_prompt(question, sources):  # a tiny stand-in for the real build_user_prompt
    sources_text = "\n".join(sources)  # joins whatever sources were given, even zero of them
    return f"Sources:\n{sources_text}\n\nQuestion: {question}\n\nAnswer:"


# --- Option A: measure the "shell" by calling the REAL builder with an empty source list ---
shell_via_reuse = build_user_prompt("Who is the CEO?", sources=[])
print("shell (via reuse):", repr(shell_via_reuse))

# --- Option B: a SEPARATE, hand-written "shell" function, maintained on its own ---
def build_shell_by_hand(question):  # a second, independent function -- has to be kept in sync
    return f"Sources:\n\n\nQuestion: {question}\n\nAnswer:"  # a manual guess at the same shape


shell_by_hand = build_shell_by_hand("Who is the CEO?")
print("shell (by hand):  ", repr(shell_by_hand))
print("identical?", shell_via_reuse == shell_by_hand)

# --- now the REAL builder's template changes (a realistic future edit) ---
def build_user_prompt_v2(question, sources):  # imagine a future edit: labels get capitalized
    sources_text = "\n".join(sources)
    return f"SOURCES:\n{sources_text}\n\nQUESTION: {question}\n\nANSWER:"  # <- changed!


shell_via_reuse_v2 = build_user_prompt_v2("Who is the CEO?", sources=[])
print("\nafter a template edit:")
print("shell (via reuse):", repr(shell_via_reuse_v2), "-- updates automatically")
print("shell (by hand):  ", repr(shell_by_hand), "-- now WRONG, nobody remembered to update it")
