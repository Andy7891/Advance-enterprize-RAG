# Anatomy of a point: fetch ONE real stored point and look at its three parts.
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import chunk_from_payload, point_id

client = build_qdrant_client("http://localhost:6333")
wanted = point_id("company/about.md_0")          # we can compute the ID without asking Qdrant

# retrieve = "give me these IDs" (no similarity involved)
(point,) = client.retrieve("chunks", ids=[wanted], with_payload=True, with_vectors=True)

print("PART 1  id      :", point.id, "| type:", type(point.id).__name__)
print("PART 2  vector  :", len(point.vector), "numbers, first 4 =", [round(x, 4) for x in point.vector[:4]])
print("        norm    :", round(sum(x * x for x in point.vector) ** 0.5, 4))
print("PART 3  payload : keys =", list(point.payload))
for key in ("chunk_id", "doc_id", "category", "chunk_index", "char_count", "model", "model_digest", "document_prefix"):
    value = point.payload[key]
    print(f"        {key:<16} = {value!r:.60}   ({type(value).__name__})")
print("        metadata         =", point.payload["metadata"])
print("        text[:70]        =", repr(point.payload["text"][:70]))

chunk = chunk_from_payload(point.payload)
print("rebuilt DocumentChunk:", type(chunk).__name__, chunk.chunk_id, "| provenance keys dropped:",
      not hasattr(chunk, "model_digest"))
