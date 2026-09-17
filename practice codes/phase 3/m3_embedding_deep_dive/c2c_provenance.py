# Provenance: with digest + prefix on every Embedding, a mixed collection is detectable.
from collections import Counter
from src.core.config import settings
from src.embedding.embedder import Embedder
from src.ingestion.pipeline import read_chunks


class FakeClient:
    def embed(self, model="", input=(), truncate=None):
        return {"embeddings": [[1.0] + [0.0] * 767 for _ in input]}


chunks = read_chunks()[:6]
old = Embedder(FakeClient(), verify=lambda url, model: settings.embed_model_digest).embed_batch(chunks[:4])
new = Embedder(FakeClient(), verify=lambda url, model: "ffffffffffffbbbb").embed_batch(chunks[4:])
collection = old + new             # imagine these were upserted on two different days

e = collection[0]
print("one Embedding:", e.chunk.chunk_id, "| model", e.model, "| digest", e.model_digest[:12],
      "| prefix", repr(e.document_prefix), "| vector len", len(e.vector))

by_version = Counter((x.model_digest[:12], x.document_prefix) for x in collection)
print("vectors per (digest, prefix):", dict(by_version))
if len(by_version) > 1:
    print("MIXED COLLECTION: vectors from", len(by_version), "different model versions/prefixes")
print("pinned digest is", settings.embed_model_digest[:12], "-> stale vectors:",
      [x.chunk.chunk_id for x in collection if x.model_digest != settings.embed_model_digest][:2], "...")
