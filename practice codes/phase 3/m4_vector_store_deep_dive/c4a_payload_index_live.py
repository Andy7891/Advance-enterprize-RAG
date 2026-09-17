# Payload indexes on the LIVE server vs the in-memory client, and a filtered search with real data.
import json
import urllib.request

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue, PayloadSchemaType

from src.vectorstore.setup import build_qdrant_client

URL = "http://localhost:6333"
info = json.load(urllib.request.urlopen(f"{URL}/collections/chunks"))["result"]
print("1. live payload_schema:")
for field, spec in info["payload_schema"].items():
    print(f"     {field:<9} type={spec['data_type']:<8} points indexed={spec['points']}")

local = QdrantClient(":memory:")
local.create_collection("demo", vectors_config={"size": 2, "distance": "Cosine"})
local.create_payload_index("demo", "doc_id", PayloadSchemaType.KEYWORD)
print("2. in-memory payload_schema after create_payload_index:", local.get_collection("demo").payload_schema,
      "<- local mode accepts the call and ignores it")

# Filtered counting and searching on the real collection
client = build_qdrant_client(URL)
for category in ("company", "contracts", "employees", "products"):
    f = Filter(must=[FieldCondition(key="category", match=MatchValue(value=category))])
    print(f"3. count where category == {category:<10} -> {client.count('chunks', count_filter=f, exact=True).count}")

# Filtered SEARCH with a real question: does restricting to one category change what comes back?
from src.embedding.embedder import Embedder, build_ollama_client

embedder = Embedder(build_ollama_client("http://localhost:11434"), ollama_url="http://localhost:11434")
question_vector = list(embedder.embed_query("What does the Carllm product do?"))
only_products = Filter(must=[FieldCondition(key="category", match=MatchValue(value="products"))])
for label, f in [("no filter", None), ("category == products", only_products)]:
    hits = client.query_points("chunks", query=question_vector, query_filter=f, limit=3).points
    print(f"4. 'What does the Carllm product do?', {label:<21}:",
          [(h.payload["chunk_id"], round(h.score, 3)) for h in hits])
