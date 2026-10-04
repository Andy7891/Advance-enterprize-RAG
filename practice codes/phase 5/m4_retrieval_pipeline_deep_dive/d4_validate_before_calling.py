# Minimal demo of C5: validating BEFORE calling either stage means a bad argument costs
# ZERO calls to the (possibly expensive/paid) stages, instead of one wasted call.
def check_k_first(k):  # the exact rule pipeline.py's _check_k uses
    if not isinstance(k, int) or isinstance(k, bool) or k < 1:  # not a plain positive integer
        raise ValueError(f"k must be a positive integer, got {k!r}")  # refuses immediately


class ExpensiveStage:  # stands in for HybridRetriever/Reranker -- imagine a real network call
    def __init__(self, name):
        self.name = name  # remembers which stage this is, for the printed trace
        self.calls = 0  # counts how many times it was actually invoked

    def run(self, k):
        self.calls += 1  # counts this call
        print(f"  ...calling the REAL {self.name} service (call #{self.calls})")  # simulates real cost
        return f"{self.name} result for k={k}"  # a fake result


def validate_first(k):  # the pattern pipeline.py actually uses
    check_k_first(k)  # fails HERE if k is bad, before anything else runs
    stage = ExpensiveStage("hybrid+rerank")  # only ever reached if k was valid
    return stage.run(k), stage.calls  # runs the (now trusted) call


def validate_last(k):  # the REJECTED pattern -- call first, check after
    stage = ExpensiveStage("hybrid+rerank")  # runs UNCONDITIONALLY
    result = stage.run(k)  # the expensive call already happened by this point
    check_k_first(k)  # only NOW does the bad k get noticed
    return result, stage.calls


print("validate_first(-1):")  # tries the good pattern with a bad k
try:
    validate_first(-1)  # should fail before any call is made
except ValueError as error:
    print(f"  refused before any call was made: {error}")

print("\nvalidate_last(-1):")  # tries the rejected pattern with the same bad k
try:
    validate_last(-1)  # the expensive call above already ran once by the time this raises
except ValueError as error:
    print(f"  refused, but only AFTER the call above already ran: {error}")
