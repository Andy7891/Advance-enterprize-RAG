# Minimal demo of C3: pool_size = max(k * multiplier, floor). Shows why max() (not + or
# min()), and reuses the REAL measured pool-rank table from P5-M3 to show the floor's limit.
DEFAULT_MULTIPLIER = 3  # the real project's default, from pipeline.py
DEFAULT_FLOOR = 20  # the real project's default, from pipeline.py


def pool_size(k, multiplier=DEFAULT_MULTIPLIER, floor=DEFAULT_FLOOR):
    return max(k * multiplier, floor)  # the exact formula used at pipeline.py line 124


print(f"{'k':<4}{'k*multiplier':<16}{'pool_size':<12}decided by")  # column headings
for k in (1, 2, 5, 7, 10, 20):  # a spread of k values, small to large
    scaled = k * DEFAULT_MULTIPLIER  # what the multiplier alone would give
    size = pool_size(k)  # what the real formula gives
    decided_by = "the FLOOR" if scaled < DEFAULT_FLOOR else "the multiplier"  # which term won
    print(f"{k:<4}{scaled:<16}{size:<12}{decided_by}")

# MEASURED on the real corpus (see notes/step-5.3-reranker.md): the CEO question's accepted
# chunk's rank INSIDE the hybrid pool, at different pool sizes. A floor guarantees CAPACITY
# (pool_size >= k), it does NOT guarantee the answer is easier to find in a bigger pool.
CEO_RANK_BY_POOL = {3: 2, 10: 3, 20: 15, 30: 21, 50: 9}  # real, measured data -- not invented
print("\npool size -> CEO answer's rank inside that pool (lower is better; MEASURED, not simulated):")
for size, rank in CEO_RANK_BY_POOL.items():
    flag = "  <- the DEFAULT pool size (20) is actually one of the WORSE ones here" if size == 20 else ""
    print(f"  {size:<4} -> rank {rank}{flag}")
