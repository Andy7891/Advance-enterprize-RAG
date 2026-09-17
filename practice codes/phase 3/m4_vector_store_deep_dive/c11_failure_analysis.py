# Why did "What does the Carllm product do?" miss products/Carllm.md? Measure instead of guessing.
import re

from src.embedding.embedder import Embedder, build_ollama_client
from src.vectorstore.setup import build_qdrant_client
from src.vectorstore.store import QdrantVectorStore

question = "What does the Carllm product do?"
embedder = Embedder(build_ollama_client("http://localhost:11434"), ollama_url="http://localhost:11434")
client = build_qdrant_client("http://localhost:6333")
store = QdrantVectorStore(client, embedder)

# 1. Where do the Carllm product chunks rank among ALL 223?
ranked = store.search_with_scores(question, k=223)
positions = {r.chunk.chunk_id: (i + 1, r.score) for i, r in enumerate(ranked)}
for cid in ("products/Carllm.md_0", "products/Carllm.md_1", "products/Carllm.md_2"):
    rank, score = positions[cid]
    print(f"1. {cid:<22} rank {rank:>3}/223  score {score:.3f}  (top-1 score {ranked[0].score:.3f})")

# 2. How close together are the scores? A flat score curve means weak separation.
print("2. scores at ranks 1, 5, 10, 25, 50, 100:", [round(ranked[i - 1].score, 3) for i in (1, 5, 10, 25, 50, 100)])

# 3. How much of the corpus mentions "Carllm" at all? (a keyword signal the vectors don't use)
mentions = sorted(((len(re.findall(r"carllm", r.chunk.text, re.I)), r.chunk.chunk_id) for r in ranked), reverse=True)
print("3. chunks mentioning 'Carllm':", sum(1 for n, _ in mentions if n), "| top by count:", mentions[:4])

# 4. What is the Carllm page's first chunk actually about? (dilution check)
page = next(r.chunk for r in ranked if r.chunk.chunk_id == "products/Carllm.md_0")
print(f"4. products/Carllm.md_0 is {page.char_count} chars; headings: {re.findall(r'^#+ .+', page.text, re.M)[:6]}")

# 5. A crude keyword ranking (count of query words) vs the vector ranking
words = [w for w in re.findall(r"\w+", question.lower()) if len(w) > 3]
keyword_top = sorted(ranked, key=lambda r: -sum(r.chunk.text.lower().count(w) for w in words))[:3]
print("5. query words:", words, "| keyword top-3:", [r.chunk.chunk_id for r in keyword_top])
print("   vector top-3:", [r.chunk.chunk_id for r in ranked[:3]])
