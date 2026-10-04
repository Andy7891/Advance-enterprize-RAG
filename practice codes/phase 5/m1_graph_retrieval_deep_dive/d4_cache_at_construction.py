# Minimal demo of C5: doing expensive, unchanging setup ONCE when an object is built,
# instead of redoing it on every call, using the SAME shape GraphRetriever's __init__ uses.
import time


def slow_lookup():
    # stands in for store.all_entity_names() -- one real network round trip to Neo4j
    time.sleep(0.05)
    return {"Product": ["Carllm"], "Company": ["DriveSmart Insurance"]}


class RecomputesEveryCall:
    def retrieve(self, query):
        names = slow_lookup()  # pays the cost EVERY single call
        return [name for names_ in names.values() for name in names_ if name in query]


class CachesAtConstruction:
    def __init__(self):
        self._names = slow_lookup()  # pays the cost exactly ONCE, when the object is built

    def retrieve(self, query):
        return [name for names_ in self._names.values() for name in names_ if name in query]


QUERIES = ["Carllm", "DriveSmart Insurance", "Carllm", "Carllm"]

started = time.perf_counter()
slow = RecomputesEveryCall()
for q in QUERIES:
    slow.retrieve(q)
print(f"RecomputesEveryCall : {time.perf_counter() - started:.3f}s for {len(QUERIES)} calls")

started = time.perf_counter()
fast = CachesAtConstruction()  # the one-time cost happens here, during construction
for q in QUERIES:
    fast.retrieve(q)
print(f"CachesAtConstruction: {time.perf_counter() - started:.3f}s for {len(QUERIES)} calls")
# the second number includes the SAME one-time setup cost, paid once instead of 4 times
