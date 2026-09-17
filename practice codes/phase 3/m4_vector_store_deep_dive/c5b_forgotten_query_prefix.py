# What happens if the query side forgets its prefix (or uses the document prefix)?
import ollama

from src.vectorstore.setup import build_qdrant_client

client = build_qdrant_client("http://localhost:6333")
ollama_client = ollama.Client(host="http://localhost:11434")
question = "Who founded Insurellm?"

for label, text in [("search_query: (correct)", f"search_query: {question}"),
                    ("no prefix", question),
                    ("search_document: (wrong side)", f"search_document: {question}")]:
    vector = ollama_client.embed(model="nomic-embed-text", input=[text], truncate=False)["embeddings"][0]
    hits = client.query_points("chunks", query=vector, limit=3).points
    print(f"{label:<30}", " | ".join(f"{h.payload['chunk_id']} {h.score:.3f}" for h in hits))
