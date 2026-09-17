# Watch exactly what the Embedder sends to the REAL Ollama, using a spy that wraps the real client.
import ollama
from src.embedding.embedder import Embedder
from src.ingestion.pipeline import read_chunks


class SpyClient:
    """Passes every call through to the real client, printing what was sent and received."""

    def __init__(self, real):
        self.real = real

    def embed(self, model="", input=(), truncate=None):
        print(f"  -> embed(model={model!r}, truncate={truncate}, {len(input)} texts)")
        for text in input:
            print(f"       {text[:60]!r}...")
        response = self.real.embed(model=model, input=input, truncate=truncate)
        print(f"  <- {len(response['embeddings'])} vectors, tokens read: {response['prompt_eval_count']}")
        return response


url = "http://localhost:11434"
chunks = read_chunks()[:3]
print("constructing Embedder (verify_model runs now, against the real server):")
embedder = Embedder(SpyClient(ollama.Client(host=url)), ollama_url=url, batch_size=2)
print("  digest verified:", embedder._digest[:12])

print("caller passes raw chunk text:", repr(chunks[0].text[:40]))
result = embedder.embed_batch(chunks)
print("returned", len(result), "Embeddings; first vector starts", [round(x, 4) for x in result[0].vector[:3]])
