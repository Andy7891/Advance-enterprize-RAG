# Minimal demo of C2: the EXACT algorithm from docs/prompts.md section 4, run against real
# measured chunk-size percentiles from this corpus (max, p95, p90, median approx-tokens).
BUDGET = 2000  # the exact number chosen in docs/prompts.md section 4, with its own worked arithmetic


def apply_budget(chunks_best_to_worst):
    kept = []  # will hold the chunks that survive the budget, in their original order
    total = 0  # the running total of approx-tokens committed so far
    for size in chunks_best_to_worst:  # walks the ranked list from best to worst
        if total + size <= BUDGET or not kept:  # fits, OR this is the very first chunk
            kept.append(size)  # keeps this chunk
            total += size  # adds its size to the running total
        # else: this one chunk does not fit right now -- it is skipped, but the loop
        # keeps going, because a SMALLER, lower-ranked chunk further down might still fit
    return kept, total  # hands back which sizes survived, and their real total


REAL_PERCENTILES = {"max": 1265, "p95": 909, "p90": 613, "median": 204}  # measured, not invented
ranked_sizes = [REAL_PERCENTILES["max"], REAL_PERCENTILES["p95"], REAL_PERCENTILES["p90"], REAL_PERCENTILES["median"]]
# treats the four percentiles as if they were four real chunks, best (biggest/most-relevant-here) first

survivors, total = apply_budget(ranked_sizes)  # runs the real algorithm against real numbers
print(f"budget: {BUDGET} approx-tokens")
print(f"ranked chunk sizes (best to worst): {ranked_sizes}")
print(f"survivors: {survivors}")
print(f"final total: {total} / {BUDGET}")

print("\ncompare to the REJECTED 'stop at first miss' rule:")
naive_kept = []  # what a simpler, worse rule would keep
naive_total = 0  # its own running total
for size in ranked_sizes:  # the SAME ranked list
    if naive_total + size > BUDGET and naive_kept:  # the first chunk that doesn't fit STOPS everything
        break  # gives up on the rest of the list entirely, even if a smaller one would fit
    naive_kept.append(size)
    naive_total += size
print(f"naive survivors: {naive_kept}  (total {naive_total}) -- discards the useful {REAL_PERCENTILES['p90']}-token chunk")
