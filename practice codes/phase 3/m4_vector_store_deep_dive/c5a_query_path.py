# The full query path, one step at a time, against the live model and collection.
import time

from src.embedding.embedder import Embedder, build_ollama_client
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore, chunk_from_payload

question = "Who founded Insurellm?"
embedder = Embedder(build_ollama_client("http://localhost:11434"), ollama_url="http://localhost:11434")
client = build_qdrant_client("http://localhost:6333")

# Step 1: question -> vector (the embedder adds "search_query: " itself)
t = time.perf_counter()
vector = embedder.embed_query(question)
t_embed = time.perf_counter() - t
print(f"1. embed_query     -> {len(vector)} numbers in {t_embed * 1000:.0f} ms, first 3 {[round(x, 4) for x in vector[:3]]}")

# Step 2: vector -> ranked points (what search_with_scores sends)
t = time.perf_counter()
response = client.query_points("chunks", query=list(vector), limit=3, with_payload=True)
t_query = time.perf_counter() - t
print(f"2. query_points    -> {type(response).__name__} with {len(response.points)} {type(response.points[0]).__name__}s in {t_query * 1000:.0f} ms")
top = response.points[0]
print(f"   ScoredPoint fields: id={top.id[:8]}..., version={top.version}, score={top.score:.3f}, payload keys={len(top.payload)}, vector={top.vector}")

# Step 3: payload -> DocumentChunk (what the caller receives)
for rank, point in enumerate(response.points, start=1):
    chunk = chunk_from_payload(point.payload)
    print(f"3. rank {rank}: score {point.score:.3f}  {chunk.chunk_id:<32} {chunk.text[:55]!r}")

# Step 4: the same through the store in one call
store = QdrantVectorStore(client, embedder)
print("4. store.search    ->", [c.chunk_id for c in store.search(question, k=3)])
