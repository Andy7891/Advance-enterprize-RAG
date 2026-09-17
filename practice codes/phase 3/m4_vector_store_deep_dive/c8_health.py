# Liveness vs readiness: four situations, what the server endpoints say, what health_check says.
import logging
import subprocess
import sys
import urllib.error
import urllib.request

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

logging.basicConfig(level=logging.WARNING, format="   log: %(message)s", stream=sys.stdout)


class NoQueries:
    def embed_query(self, text):
        raise AssertionError("never called")


def endpoint(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return f"{r.status} {r.read().decode()[:25]}"
    except (urllib.error.URLError, OSError) as e:
        return f"unreachable ({type(e).__name__})"


print("A. server endpoints on :6333 ->", "/livez:", endpoint("http://localhost:6333/livez"),
      "| /readyz:", endpoint("http://localhost:6333/readyz"))
print("   server endpoints on :6399 ->", "/livez:", endpoint("http://localhost:6399/livez"))

live = build_qdrant_client("http://localhost:6333")
cases = [
    ("real collection 'chunks'", QdrantVectorStore(live, NoQueries())),
    ("collection that does not exist", QdrantVectorStore(live, NoQueries(), collection="no_such_collection")),
    ("wrong size (384) collection", None),
    ("nothing listening on :6399", QdrantVectorStore(build_qdrant_client("http://localhost:6399"), NoQueries())),
]
for label, store in cases:
    if store is None:
        memory = QdrantClient(":memory:")
        memory.create_collection("chunks", vectors_config=VectorParams(size=384, distance=Distance.COSINE))
        store = QdrantVectorStore(memory, NoQueries())
    print(f"B. health_check, {label:<32} ->", store.health_check())

exit_code = subprocess.run([sys.executable, "-m", "src.vectorstore.store", "health"], capture_output=True, text=True)
print("C. CLI `store health` exit code:", exit_code.returncode, "| stdout:", exit_code.stdout.strip())
