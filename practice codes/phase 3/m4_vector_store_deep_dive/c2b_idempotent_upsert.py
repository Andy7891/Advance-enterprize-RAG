# Upsert = insert or replace BY ID. Same ID -> overwrite. New ID -> another point.
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from qdrant_client.http.exceptions import UnexpectedResponse

client = QdrantClient(":memory:")
client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
NS = uuid.UUID("f46fd59c-cfad-4523-9397-d36d7de5de4e")


def run(label, make_id, runs=3):
    client.delete_collection("demo")
    client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    for run_number in range(1, runs + 1):
        client.upsert("demo", wait=True, points=[
            PointStruct(id=make_id(chunk_id), vector=[1.0, 0.0], payload={"chunk_id": chunk_id, "run": run_number})
            for chunk_id in ("about.md_0", "about.md_1")
        ])
    points, _ = client.scroll("demo", limit=100)
    print(f"{label:<22} after {runs} runs of 2 chunks -> {len(points)} points; runs stored: {sorted(p.payload['run'] for p in points)}")


run("uuid5(chunk_id)", lambda cid: str(uuid.uuid5(NS, cid)))
run("uuid4() random", lambda cid: str(uuid.uuid4()))

# What Qdrant accepts as an ID -- asked of BOTH the in-memory client and the real server
from src.vectorstore.setup import build_qdrant_client

server = build_qdrant_client("http://localhost:6333")
if server.collection_exists("dd14_id_scratch"):
    server.delete_collection("dd14_id_scratch")
server.create_collection("dd14_id_scratch", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
for candidate in (7, str(uuid.uuid5(NS, "x")), "about.md_0", -1):
    verdicts = []
    for label, target, name in (("in-memory", client, "demo"), ("server", server, "dd14_id_scratch")):
        try:
            target.upsert(name, wait=True, points=[PointStruct(id=candidate, vector=[1.0, 0.0])])
            verdicts.append(f"{label}: accepted")
        except (ValueError, UnexpectedResponse) as e:
            verdicts.append(f"{label}: rejected ({type(e).__name__})")
    print(f"id {candidate!r:<40} -> " + " | ".join(verdicts))
server.delete_collection("dd14_id_scratch")
