# Minimal demo of C5: injected retrievers make the class testable with fakes, and bad arguments
# are rejected BEFORE either retriever is called.
import sys  # gives access to the module search path

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.retrieval.hybrid_retriever import HybridRetriever  # the real class

calls = []  # records every call the fakes receive, in order


class RecordingVector:
    def search_with_scores(self, query_text, k):
        calls.append(("vector", k))  # notes that vector search was called, and with which k
        return []  # returns no hits


class RecordingGraph:
    def retrieve(self, query, k):
        calls.append(("graph", k))  # notes that graph retrieval was called, and with which k
        return []  # returns no hits


hybrid = HybridRetriever(RecordingVector(), RecordingGraph())  # any objects with these methods work
hybrid.retrieve("q", 4)  # one valid call
print("valid call order and k:", calls)  # expect vector first, then graph, both asked for 12

calls.clear()  # forgets the earlier calls
bad_calls = (  # three unusable calls
    ("k=0", {"k": 0}),  # k must be at least 1
    ("weight=-1", {"k": 3, "vector_weight": -1}),  # negative weight
    ("both weights 0", {"k": 3, "vector_weight": 0, "graph_weight": 0}),  # every score would be 0
)
for label, kwargs in bad_calls:  # tries each bad call in turn
    try:
        hybrid.retrieve("q", **kwargs)  # a call with an unusable argument
    except ValueError as error:
        print(f"{label:<15} -> ValueError: {error}")  # shows the message the caller sees
print("retrievers called during the bad calls:", calls)  # expect an empty list
