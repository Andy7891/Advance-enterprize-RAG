# Minimal demo of C4: joining two lists by a shared id. A naive nested loop is O(n*m); a
# dict index turns it into two O(n) passes. N is big enough to actually SEE the time gap.
import time  # measures how long each approach takes

N = 4000  # how many items in each list

hybrid_pool = [{"id": f"chunk_{i}", "hybrid_score": i / 1000} for i in range(N)]  # stage 1's output
reranked = [{"id": f"chunk_{i}", "relevance_score": 1.0 - i / N} for i in reversed(range(N))]  # stage 2's output, in a DIFFERENT order


def join_naive(reranked, pool):
    joined = []  # will hold one merged record per reranked item
    for r in reranked:  # for each reranked item...
        for p in pool:  # ...scan the ENTIRE pool looking for a matching id
            if p["id"] == r["id"]:  # found the matching hybrid record
                joined.append({**r, "hybrid_score": p["hybrid_score"]})  # merges the two dicts
                break  # stops scanning once a match is found
    return joined  # hands back every merged record


def join_by_index(reranked, pool):
    by_id = {p["id"]: p for p in pool}  # ONE pass to build an id -> record lookup table
    return [{**r, "hybrid_score": by_id[r["id"]]["hybrid_score"]} for r in reranked]  # ONE pass using the table


started = time.perf_counter()  # starts the naive timer
naive_result = join_naive(reranked, hybrid_pool)  # runs the naive nested-loop join
naive_seconds = time.perf_counter() - started  # stops the naive timer

started = time.perf_counter()  # starts the indexed timer
indexed_result = join_by_index(reranked, hybrid_pool)  # runs the dict-indexed join
indexed_seconds = time.perf_counter() - started  # stops the indexed timer

print(f"naive nested-loop join:  {naive_seconds * 1000:8.2f} ms  (up to {N * N:,} comparisons)")  # the slow way
print(f"dict-indexed join:       {indexed_seconds * 1000:8.2f} ms  ({2 * N:,} steps total)")  # the pipeline.py way
print(f"results identical:       {naive_result == indexed_result}")  # proves both give the same answer
print(f"indexed join was {naive_seconds / indexed_seconds:.0f}x faster")  # the measured speed-up
