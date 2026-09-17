# One request per text vs one request for all texts: same vectors? how many round trips?
import time
import numpy as np
import ollama
from src.embedding.lock import as_document
from src.ingestion.pipeline import read_chunks

client = ollama.Client(host="http://localhost:11434")
texts = [as_document(c.text) for c in read_chunks()[:16]]
client.embed(model="nomic-embed-text", input=["warm-up"])   # load the model first so timing is fair

# A: 16 separate HTTP requests
t = time.perf_counter()
single = [client.embed(model="nomic-embed-text", input=[x], truncate=False)["embeddings"][0] for x in texts]
t_single = time.perf_counter() - t

# B: 1 HTTP request carrying all 16 texts
t = time.perf_counter()
response = client.embed(model="nomic-embed-text", input=texts, truncate=False)
batched = response["embeddings"]
t_batch = time.perf_counter() - t

print(f"A: 16 requests -> {len(single)} vectors in {t_single:.1f}s")
print(f"B:  1 request  -> {len(batched)} vectors in {t_batch:.1f}s")
print("response fields besides embeddings:", {k: v for k, v in dict(response).items() if k != "embeddings"})
diff = max(float(np.abs(np.array(a) - np.array(b)).max()) for a, b in zip(single, batched))
print("largest difference between A and B, over all 16x768 numbers:", diff)
