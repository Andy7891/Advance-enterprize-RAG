# Provenance at the storage boundary: refuse stale embeddings, and query stored provenance.
from collections import Counter

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchExcept, MatchValue

from src.core.config import settings
from src.ingestion.schemas import DocumentChunk, Embedding
from src.vectorstore.setup import build_qdrant_client, ensure_collection
from src.vectorstore.store import ProvenanceMismatch, QdrantVectorStore


def emb(digest, prefix):
    chunk = DocumentChunk(chunk_id="company/about.md_0", doc_id="doc-about", source_path="company/about.md",
                          category="company", title="About", chunk_index=0, text="x", char_count=1,
                          approx_token_count=1, metadata={})
    return Embedding(chunk, tuple([1.0] + [0.0] * 767), settings.embed_model, digest, prefix)


class NoQueries:
    def embed_query(self, text):
        raise AssertionError


store = QdrantVectorStore(QdrantClient(":memory:"), NoQueries(), collection="demo")
ensure_collection(store._client, name="demo")
for label, e in [("current model and prefix", emb(settings.embed_model_digest, settings.embed_document_prefix)),
                 ("an OLD model digest", emb("9" * 64, settings.embed_document_prefix)),
                 ("prefix switched off", emb(settings.embed_model_digest, ""))]:
    try:
        store.upsert([e])
        print(f"1. upsert with {label:<26} -> stored (count {store.count()})")
    except ProvenanceMismatch as err:
        print(f"1. upsert with {label:<26} -> refused: {err}")

# On the live collection, provenance is queryable like any other payload field
live = build_qdrant_client("http://localhost:6333")
points, _ = live.scroll("chunks", limit=500, with_payload=["model_digest", "document_prefix"])
print("2. live provenance groups:", dict(Counter((p.payload["model_digest"][:12], p.payload["document_prefix"]) for p in points)))
stale = Filter(must_not=[FieldCondition(key="model_digest", match=MatchValue(value=settings.embed_model_digest))])
print("3. live points NOT made by the pinned digest:", live.count("chunks", count_filter=stale, exact=True).count)
other_prefix = Filter(must=[FieldCondition(key="document_prefix", match=MatchExcept(**{"except": [settings.embed_document_prefix]}))])
print("4. live points with any other document prefix:", live.count("chunks", count_filter=other_prefix, exact=True).count)
