# Orphan removal and the failure guard on a LIVE scratch collection, through the real job.
# Uses 3 small documents so embedding takes seconds. The scratch collection is deleted at the end.
import json
import shutil
import tempfile
from pathlib import Path

from src.embedding.embedder import Embedder, build_ollama_client
from src.vectorstore.ingest import run_ingestion
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

URL, OLLAMA = "http://localhost:6333", "http://localhost:11434"
COLLECTION = "dd15_orphans_scratch"
work = Path(tempfile.mkdtemp())
chunks_dir, log = work / "chunks", work / "log.json"
chunks_dir.mkdir()
for name in ("company__about.md.json", "company__careers.md.json", "products__Carllm.md.json"):
    shutil.copy(Path("data/chunks") / name, chunks_dir / name)


def set_failed(n):
    (chunks_dir / "report.json").write_text(json.dumps({"documents_failed": n}), encoding="utf-8")


client = build_qdrant_client(URL)
if client.collection_exists(COLLECTION):
    client.delete_collection(COLLECTION)
make_embedder = lambda: Embedder(build_ollama_client(OLLAMA), ollama_url=OLLAMA)


def ingest(label):
    r = run_ingestion(client, make_embedder, collection=COLLECTION, chunks_dir=chunks_dir, log_path=log)
    docs = sorted(QdrantVectorStore(client, None, collection=COLLECTION).stored_chunk_ids_by_document())
    print(f"{label:<44} points {r.points_before}->{r.points_after} | removed {r.orphan_documents_removed}"
          f" kept {r.orphan_documents_kept} | verified={r.verified} | stored docs={len(docs)}")
    for w in r.warnings:
        print("   warning:", w)


set_failed(0)
ingest("1. first run, 3 documents")
(chunks_dir / "products__Carllm.md.json").unlink()
set_failed(1)
ingest("2. Carllm file gone, report says 1 failure")
set_failed(0)
ingest("3. Carllm file gone, clean report")

client.delete_collection(COLLECTION)
shutil.rmtree(work)
print("scratch collection deleted:", not client.collection_exists(COLLECTION))
