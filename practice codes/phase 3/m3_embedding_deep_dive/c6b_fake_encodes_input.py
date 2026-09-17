# A useful fake returns vectors that ENCODE their input, so tests can see which text made which vector.
from src.embedding.embedder import Embedder
from src.ingestion.pipeline import read_chunks


def one_hot(position: int) -> list[float]:
    v = [0.0] * 768
    v[position % 768] = 1.0            # unit length, so it passes validation
    return v


class HonestFake:
    def embed(self, model="", input=(), truncate=None):
        return {"embeddings": [one_hot(len(t)) for t in input]}


class ShufflingFake(HonestFake):
    def embed(self, model="", input=(), truncate=None):
        return {"embeddings": list(reversed(super().embed(model, input, truncate)["embeddings"]))}


class ConstantFake:                    # the lazy fake: same vector for everything
    def embed(self, model="", input=(), truncate=None):
        return {"embeddings": [one_hot(0) for _ in input]}


chunks = read_chunks()[:3]
prefix = len("search_document: ")
expected = [(prefix + len(c.text)) % 768 for c in chunks]   # same wrap-around as one_hot
print("expected hot positions:", expected)
for fake in (HonestFake(), ShufflingFake(), ConstantFake()):
    result = Embedder(fake, batch_size=3, verify=lambda url, model: "d").embed_batch(chunks)
    got = [e.vector.index(1.0) for e in result]
    print(f"{type(fake).__name__:<13} hot positions {got} -> order check {'PASS' if got == expected else 'FAIL'}")
