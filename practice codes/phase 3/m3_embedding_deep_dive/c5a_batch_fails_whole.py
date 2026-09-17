# One bad text inside a batch: does the server return the good ones? (real Ollama)
import ollama
from src.embedding.lock import as_document

client = ollama.Client(host="http://localhost:11434")
good_1 = as_document("Employees receive 20 days of paid annual leave per year.")
good_2 = as_document("The Acme widget ships in blue and red.")
too_long = as_document("policy " * 3000)

print("batch [good, good]           ->", len(client.embed(model="nomic-embed-text", input=[good_1, good_2], truncate=False)["embeddings"]), "vectors")
try:
    client.embed(model="nomic-embed-text", input=[good_1, too_long, good_2], truncate=False)
except ollama.ResponseError as e:
    print("batch [good, TOO LONG, good] ->", type(e).__name__, "status", e.status_code, "|", e.error)
    print("   the error mentions which input? ", "index" in e.error or "1" in e.error)
