# Throughput for different batch sizes on the SAME 32 chunks (model already warm).
import time
import ollama
from src.embedding.lock import as_document
from src.ingestion.pipeline import read_chunks

client = ollama.Client(host="http://localhost:11434")
texts = [as_document(c.text) for c in read_chunks()[:32]]
client.embed(model="nomic-embed-text", input=["warm-up"])

for batch_size in (1, 4, 8, 16, 32):
    t = time.perf_counter()
    for start in range(0, len(texts), batch_size):
        client.embed(model="nomic-embed-text", input=texts[start:start + batch_size], truncate=False)
    elapsed = time.perf_counter() - t
    print(f"batch_size={batch_size:>2}: {len(texts) // batch_size + (len(texts) % batch_size > 0):>2} requests, "
          f"{elapsed:5.1f}s, {len(texts) / elapsed:.2f} chunks/s")
