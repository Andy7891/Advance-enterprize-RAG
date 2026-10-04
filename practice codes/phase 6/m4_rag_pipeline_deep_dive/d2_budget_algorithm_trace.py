# Minimal demo of C3: apply_budget's exact control flow, traced step by step -- answers the
# question directly: given chunks of 1800, 400, 100 tokens, what survives a 2000-token budget?
def apply_budget(chunks_with_sizes, budget):  # chunks_with_sizes: a list of (name, size) pairs
    kept = []  # will hold the surviving (name, size) pairs, in their original order
    total = 0  # the running total of tokens committed so far
    for name, size in chunks_with_sizes:  # walks the list best-to-worst, exactly like the real code
        fits = total + size <= budget  # would adding this chunk stay within budget?
        is_first = not kept  # is this the very first chunk seen so far (nothing kept yet)?
        print(f"considering {name} ({size} tok): running total so far = {total}")
        if fits or is_first:  # the real condition: fits, OR this is the first chunk regardless
            kept.append((name, size))  # keeps this chunk
            total += size  # updates the running total
            print(f"  -> KEPT. new running total = {total}")
        else:
            print(f"  -> SKIPPED (would make {total + size} > {budget}), but still checking the rest")
    return kept, total  # hands back the survivors and their final combined total


print("--- three chunks: 1800, 400, 100 tokens; budget = 2000 ---")
chunks = [("A", 1800), ("B", 400), ("C", 100)]  # best-ranked first, exactly as RankedChunk arrives
survivors, total = apply_budget(chunks, 2000)  # runs the real algorithm against this exact case
print(f"\nfinal survivors: {survivors}")
print(f"final total: {total} / 2000")

print("\n--- what if the FIRST chunk alone is bigger than the whole budget? ---")
huge = [("HUGE", 5000), ("small", 100)]  # the top-ranked chunk alone already exceeds the budget
survivors2, total2 = apply_budget(huge, 2000)
print(f"final survivors: {survivors2}  (HUGE is kept anyway -- 'or is_first' never allows zero)")
