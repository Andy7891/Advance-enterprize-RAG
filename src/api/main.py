"""FastAPI entrypoint. RAG endpoints (ingest/query) are added in later steps."""

from fastapi import FastAPI

from src.core.config import settings

app = FastAPI(title="Advance RAG", version="0.1.0")


@app.get("/health")
def health() -> dict:
    """Liveness check + confirms which backing services this instance is configured for."""
    return {
        "status": "ok",
        "qdrant_url": settings.qdrant_url,
        "neo4j_uri": settings.neo4j_uri,
        "ollama_url": settings.ollama_url,
    }
