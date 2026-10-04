# Minimal demo of C4: each retriever is asked for depth = 3*k candidates, so k changes the answer.
# Uses the REAL HybridRetriever with fake retrievers shaped like the real Carllm situation.
import sys  # gives access to the module search path
from types import SimpleNamespace  # builds tiny stand-in objects

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.retrieval.hybrid_retriever import HybridRetriever  # the real class


def chunk(name):
    return SimpleNamespace(chunk_id=name)  # a stand-in chunk with just a chunk_id


VECTOR_ORDER = [f"v{n}" for n in range(1, 22)] + ["PAGE"] + [f"v{n}" for n in range(23, 41)]  # PAGE at 22
VECTOR_ORDER[8] = "CONTRACT"  # the contract chunk sits at vector rank 9
GRAPH_ORDER = ["g1", "g2", "CONTRACT", "g4", "g5", "g6", "PAGE"] + [f"g{n}" for n in range(8, 41)]  # PAGE at 7


class FakeVector:
    def search_with_scores(self, query_text, k):
        return [SimpleNamespace(chunk=chunk(c), score=0.5) for c in VECTOR_ORDER[:k]]  # top k only


class FakeGraph:
    def retrieve(self, query, k):
        return [chunk(c) for c in GRAPH_ORDER[:k]]  # top k only


hybrid = HybridRetriever(FakeVector(), FakeGraph())  # depth multiplier defaults to 3
for k in (3, 10):  # a small k and a larger one
    ids = [c.chunk_id for c in hybrid.retrieve("question", k)]  # the fused top k
    print(f"k={k:<2} depth={3 * k:<2} -> {ids[:5]}  PAGE in result: {'PAGE' in ids}")
first_three_of_10 = [c.chunk_id for c in hybrid.retrieve("q", 10)][:3]  # top 3 of the k=10 call
result_for_3 = [c.chunk_id for c in hybrid.retrieve("q", 3)]  # the k=3 call
print("first three of k=10 equal the k=3 result:", first_three_of_10 == result_for_3)  # prefix check
