# Mutation testing as design feedback: a check that can never fail after an earlier guarantee.
class Mismatch(Exception):
    pass


def ensure_ok(collection):
    # like ensure_collection: creates if missing, RAISES if misconfigured
    if collection.get("size") is None:
        collection["size"] = 768
    if collection["size"] != 768:
        raise Mismatch("wrong size")


def health(collection):
    # like health_check: returns False if missing or misconfigured
    return collection.get("size") == 768


def preflight(collection, use_health=True):
    ensure_ok(collection)
    if use_health and not health(collection):
        return "refused by health check"
    return "passed"


for label, collection in [("missing", {}), ("correct", {"size": 768}), ("wrong size", {"size": 384})]:
    results = []
    for use_health in (True, False):
        try:
            results.append(preflight(dict(collection), use_health))
        except Mismatch as e:
            results.append(f"raised {e}")
    print(f"{label:<11} with health: {results[0]:<18} | without health: {results[1]}")
print("-> identical in every case: the health check never changes the outcome after ensure_ok")
