# From Embeddings to a first search: brute-force nearest neighbours with a matrix product.
import numpy as np
import ollama
from src.embedding.embedder import Embedder
from src.embedding.lock import as_query
from src.ingestion.pipeline import read_chunks

url = "http://localhost:11434"
client = ollama.Client(host=url)
subset = [c for c in read_chunks() if c.category == "company"]        # 7 chunks, fast
embeddings = Embedder(client, ollama_url=url).embed_batch(subset)

matrix = np.array([e.vector for e in embeddings])                     # shape (7, 768)
print("matrix shape:", matrix.shape)

for question in ["Who founded the company?", "Where are the offices?", "What are the company values?"]:
    q = np.array(client.embed(model="nomic-embed-text", input=[as_query(question)])["embeddings"][0])
    scores = matrix @ q                                                  # 7 dot products in one step
    order = np.argsort(-scores)                                          # indices, best first
    print(question)
    for i in order[:2]:
        chunk = embeddings[i].chunk
        print(f"   {scores[i]:.3f}  {chunk.chunk_id:<22} {chunk.text[:70]!r}")

about = [e.chunk for e in embeddings if e.chunk.chunk_id == "company/about.md_0"][0]
print("company/about.md_0 is", about.char_count, "chars; it covers:", about.text[:200].replace("\n", " "), "...")
