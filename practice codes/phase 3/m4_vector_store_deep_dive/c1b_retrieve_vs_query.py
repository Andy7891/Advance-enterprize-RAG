# Three ways to read points: by ID (retrieve), by listing (scroll), by similarity (query_points).
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

client = QdrantClient(":memory:")
client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
client.upsert("demo", wait=True, points=[
    PointStruct(id=1, vector=[1.0, 0.0], payload={"text": "east"}),
    PointStruct(id=2, vector=[0.0, 1.0], payload={"text": "north"}),
    PointStruct(id=3, vector=[0.7, 0.7], payload={"text": "north-east"}),
])

print("retrieve(ids=[2])      ->", [(p.id, p.payload["text"]) for p in client.retrieve("demo", ids=[2])])

points, next_page = client.scroll("demo", limit=2)          # paging through everything, no ranking
print("scroll(limit=2)        ->", [(p.id, p.payload["text"]) for p in points], "| next page starts at id", next_page)

hits = client.query_points("demo", query=[0.9, 0.1], limit=3).points   # ranked by cosine similarity
print("query_points([0.9,0.1])->", [(p.id, p.payload["text"], round(p.score, 3)) for p in hits])

# with_payload / with_vectors control what travels back over the network
bare = client.query_points("demo", query=[0.9, 0.1], limit=1, with_payload=False).points[0]
print("with_payload=False     ->", "id", bare.id, "| payload", bare.payload, "| vector", bare.vector)
