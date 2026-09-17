# Why re-chunking leaves stale points, and how delete-by-doc_id removes them.
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (Distance, FieldCondition, Filter, FilterSelector, MatchValue,
                                  PointStruct, VectorParams)

NS = uuid.UUID("f46fd59c-cfad-4523-9397-d36d7de5de4e")


def points_for(texts, doc_id="doc-about"):
    # chunk_id is positional: "<source>_<index>", exactly like compute_chunk_id
    return [PointStruct(id=str(uuid.uuid5(NS, f"about.md_{i}")), vector=[1.0, float(i)],
                        payload={"doc_id": doc_id, "chunk_id": f"about.md_{i}", "text": t})
            for i, t in enumerate(texts)]


def show(client, label):
    points, _ = client.scroll("demo", limit=100, with_payload=True)
    rows = sorted((p.payload["chunk_id"], p.payload["text"]) for p in points)
    print(f"{label:<44} {len(rows)} points: {rows}")


old_run = ["Founded 2015", "Offices in SF", "Restructured 2021", "Products list"]   # 4 chunks
new_run = ["Founded 2015. Offices in SF", "Restructured 2021. Products list"]      # re-chunked to 2

for guard in (False, True):
    client = QdrantClient(":memory:")
    client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    client.upsert("demo", points=points_for(old_run), wait=True)
    print(f"--- guard {'ON ' if guard else 'OFF'} ---")
    show(client, "after first ingest")
    if guard:
        client.delete("demo", wait=True, points_selector=FilterSelector(
            filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value="doc-about"))])))
        show(client, "after delete where doc_id == 'doc-about'")
    client.upsert("demo", points=points_for(new_run), wait=True)
    show(client, "after re-ingest with 2 chunks")
