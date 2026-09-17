# Run the job's verification logic by hand against the LIVE 'chunks' collection.
from src.ingestion.pipeline import read_chunks
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

store = QdrantVectorStore(build_qdrant_client("http://localhost:6333"), None)

disk = {}
for c in read_chunks():
    disk.setdefault(c.doc_id, set()).add(c.chunk_id)
stored = store.stored_chunk_ids_by_document()           # paged scroll of only doc_id + chunk_id

expected = set().union(*disk.values())
actual = set().union(*stored.values())
print("documents on disk / stored :", len(disk), "/", len(stored))
print("chunk_ids on disk / stored :", len(expected), "/", len(actual))
print("missing from index         :", sorted(expected - actual)[:3], len(expected - actual))
print("unexpected in index        :", sorted(actual - expected)[:3], len(actual - expected))
print("incompatible points        :", store.count_incompatible())
print("VERIFIED" if expected == actual and store.count_incompatible() == 0 else "NOT VERIFIED")
