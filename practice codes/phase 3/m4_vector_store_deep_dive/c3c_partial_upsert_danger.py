# The delete guard's own danger: a PARTIAL document list deletes the chunks you left out.
from qdrant_client import QdrantClient

from src.core.config import settings
from src.ingestion.schemas import DocumentChunk, Embedding
from src.vectorstore.setup import ensure_collection
from src.vectorstore.store import IncompleteDocument, QdrantVectorStore


def emb(i):
    text = f"about chunk {i}"
    chunk = DocumentChunk(chunk_id=f"company/about.md_{i}", doc_id="doc-about", source_path="company/about.md",
                          category="company", title="About", chunk_index=i, text=text, char_count=len(text),
                          approx_token_count=3, metadata={})
    vector = [0.0] * 768
    vector[i] = 1.0
    return Embedding(chunk, tuple(vector), settings.embed_model, settings.embed_model_digest,
                     settings.embed_document_prefix)


class NoQueries:
    def embed_query(self, text):
        raise AssertionError


client = QdrantClient(":memory:")
ensure_collection(client, name="demo")
store = QdrantVectorStore(client, NoQueries(), collection="demo")

full = [emb(i) for i in range(3)]
store.upsert(full)
print("stored document with chunks 0,1,2 ->", store.count(), "points")

# Someone "fixes" just chunk 1 and upserts only that one.
# Chunk index 1 alone is not 0..N-1, so it is refused BEFORE the delete runs.
try:
    store.upsert([full[1]])
except IncompleteDocument as e:
    print("upsert([chunk 1]) ->", type(e).__name__ + ":", str(e)[:95], "...")
print("points still stored ->", store.count())

# The known gap: chunks 0 and 1 without chunk 2 LOOK complete (0..N-1), so they are accepted.
store.upsert(full[:2])
print("upsert([chunk 0, chunk 1]) accepted -> points now", store.count(), "<- chunk 2 was deleted")
