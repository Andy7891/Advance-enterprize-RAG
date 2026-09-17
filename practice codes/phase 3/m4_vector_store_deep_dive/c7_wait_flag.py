# wait=True vs wait=False on the LIVE server: what the call returns and when points become visible.
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.vectorstore.setup import build_qdrant_client

client = build_qdrant_client("http://localhost:6333")
name = "dd14_wait_scratch"
if client.collection_exists(name):
    client.delete_collection(name)
client.create_collection(name, vectors_config=VectorParams(size=4, distance=Distance.COSINE))

batch = lambda start: [PointStruct(id=i, vector=[1.0, 0.0, 0.0, float(i % 7)]) for i in range(start, start + 20000)]

result = client.upsert(name, points=batch(0), wait=False)
seen_immediately = client.count(name, exact=True).count
print(f"wait=False -> status={result.status.value:<12} count right after the call: {seen_immediately}")

result = client.upsert(name, points=batch(20000), wait=True)
seen_immediately = client.count(name, exact=True).count
print(f"wait=True  -> status={result.status.value:<12} count right after the call: {seen_immediately}")

client.delete_collection(name)
