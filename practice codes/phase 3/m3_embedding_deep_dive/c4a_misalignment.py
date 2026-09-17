# What a misaligned batch does to retrieval, with REAL vectors: every chunk gets a neighbour's meaning.
import numpy as np
import ollama
from src.embedding.lock import as_document, as_query

client = ollama.Client(host="http://localhost:11434")
texts = [
    "Employees receive 20 days of paid annual leave per year.",
    "The Acme widget ships in blue and red.",
    "Contracts renew automatically every twelve months.",
]
vectors = np.array(client.embed(model="nomic-embed-text", input=[as_document(t) for t in texts])["embeddings"])
query = np.array(client.embed(model="nomic-embed-text", input=[as_query("How much holiday do staff get?")])["embeddings"][0])


def search(stored_vectors, label):
    scores = stored_vectors @ query                  # unit vectors: dot product == cosine
    best = int(np.argmax(scores))
    print(f"{label}: top hit = {texts[best]!r} (score {scores[best]:.3f})")


search(vectors, "correct pairing   ")
search(np.roll(vectors, 1, axis=0), "shifted by one    ")   # vector i stored next to text i+1
