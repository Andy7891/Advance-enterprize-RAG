# Minimal demo of C5: a GOOD health check reports enough to diagnose a problem, not just "ok" --
# exactly why src/api/main.py's /health returns qdrant_url/neo4j_uri/ollama_url, not just status.
from fastapi import FastAPI
from fastapi.testclient import TestClient


def build_app(qdrant_url, ollama_url):  # mirrors src/api/main.py's own settings-driven health check
    app = FastAPI()

    @app.get("/health_minimal")
    def health_minimal():
        return {"status": "ok"}  # tells you NOTHING about what this instance is even pointed at

    @app.get("/health_informative")
    def health_informative():
        return {"status": "ok", "qdrant_url": qdrant_url, "ollama_url": ollama_url}
        # tells a human debugging a problem EXACTLY what this running instance is configured for

    return app


print("--- a correctly-configured instance ---")
client = TestClient(build_app("http://qdrant:6333", "http://ollama:11434"))
print("minimal:    ", client.get("/health_minimal").json())
print("informative:", client.get("/health_informative").json())

print("\n--- a MISCONFIGURED instance (still reports 'ok'!) ---")
client2 = TestClient(build_app("http://wrong-host:6333", "http://ollama:11434"))
print("minimal:    ", client2.get("/health_minimal").json())
print("informative:", client2.get("/health_informative").json())
print("\nBoth say 'status: ok' -- the app itself is running fine either way. Only the")
print("INFORMATIVE version lets a human actually SEE the misconfiguration without guessing.")
