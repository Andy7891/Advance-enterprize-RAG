# Minimal demo of C5: the small rules around the model call, using the real Reranker and a recording client.
import sys  # gives access to the module search path
from types import SimpleNamespace  # builds tiny stand-in objects with named attributes

sys.path.insert(0, ".")  # lets Python import src.* and tests.* when run from the project root
from src.retrieval.reranker import Reranker  # the real production class
from tests.support import make_chunk  # builds a real DocumentChunk

calls = []  # records every request the recording client receives


class RecordingClient:
    def __init__(self, order=None):
        self.order = order  # an optional fixed answer, as document positions

    def rerank(self, *, model, query, documents, top_n):
        calls.append({"documents": list(documents), "top_n": top_n})  # records what was sent
        positions = self.order if self.order is not None else list(range(len(documents)))  # chooses which positions to answer with
        return SimpleNamespace(results=[SimpleNamespace(index=i, relevance_score=1.0 - 0.1 * i) for i in positions][:top_n])  # answers best first, at most top_n


def chunk(name):
    return make_chunk(name, 0, f"text of {name}")  # a tiny chunk whose text is "text of <name>"


def show(label, action):
    calls.clear()  # forgets earlier requests
    try:
        result = action()  # runs the call
        outcome = [getattr(r, "chunk", r).chunk_id for r in result]  # lists the chunk ids returned
    except ValueError as error:  # catches the validation errors
        outcome = f"ValueError: {error}"  # records the message
    print(f"{label:<34} -> {outcome}   requests: {len(calls)}   sent: {calls[0] if calls else '-'}")  # prints result and what was sent


reranker = Reranker(RecordingClient())  # a reranker over the recording client
A, B, C = chunk("A"), chunk("B"), chunk("C")  # three different chunks
show("empty candidate list", lambda: reranker.rerank("q", [], 3))  # nothing to score: no request
show("duplicate A in the input", lambda: reranker.rerank_with_scores("q", [A, B, A, C], 3))  # A must be sent and returned once
show("k larger than the list", lambda: reranker.rerank("q", [A, B], 10))  # top_n is capped at the list length
show("k = 0", lambda: reranker.rerank("q", [A], 0))  # invalid k: refused before any request
show("blank question", lambda: reranker.rerank("  ", [A], 1))  # invalid question: refused before any request
show("impossible index from the API", lambda: Reranker(RecordingClient(order=[-1])).rerank("q", [A, B], 1))  # a negative index must not silently pick a chunk
