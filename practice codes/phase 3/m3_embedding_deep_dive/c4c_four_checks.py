# Feed hand-made bad vectors to the real _validate and see each check fire.
import math
from src.embedding.embedder import Embedder, EmbeddingError


class NoClient:
    def embed(self, **kwargs):
        raise AssertionError("not used")


embedder = Embedder(NoClient(), verify=lambda url, model: "digest")   # skip the live model check
good = [1.0] + [0.0] * 767                                             # length exactly 1
cases = {
    "valid batch        ": (["c0", "c1"], [good, good]),
    "one vector missing ": (["c0", "c1"], [good]),
    "wrong dimension    ": (["c0"], [[1.0] + [0.0] * 383]),
    "NaN inside         ": (["c0"], [[math.nan] + [0.0] * 767]),
    "not unit length    ": (["c0"], [[0.5] + [0.0] * 767]),
    "tiny rounding error": (["c0"], [[1.0004] + [0.0] * 767]),
}
for label, (ids, vectors) in cases.items():
    try:
        embedder._validate(ids, vectors)
        print(f"{label} -> passes")
    except EmbeddingError as e:
        print(f"{label} -> EmbeddingError: {e}")
