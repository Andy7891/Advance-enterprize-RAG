"""Centralized app configuration, loaded from environment variables (.env in dev)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Service hostnames default to Docker Compose service names.
    # Override with real values in .env for local (non-Docker) dev.
    qdrant_url: str = "http://qdrant:6333"
    # The compose service name; scripts on the host override it with
    # NEO4J_URI=bolt://localhost:7687 in .env.
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    # Empty rather than a guessable default: without .env, connecting fails with an
    # authentication error instead of quietly using a well-known password.
    neo4j_password: str = ""
    ollama_url: str = "http://ollama:11434"

    cohere_api_key: str = ""

    # --- Embedding model lock (P3-M2, see docs/architecture.md) ---------------
    # Every value below is coupled to vectors already stored in Qdrant. Changing
    # any of them without dropping the collection and re-embedding leaves old
    # document vectors and new query vectors in different spaces — searches keep
    # returning results, they are just silently wrong.

    embed_model: str = "nomic-embed-text"

    # A model NAME is not a version: `ollama pull` can replace the weights behind
    # the same tag. The digest is what actually identifies the weights, and it is
    # checked at startup by src/embedding/lock.py.
    embed_model_digest: str = "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"

    # Measured from the running model, not taken from documentation.
    embed_dim: int = 768
    embed_context_tokens: int = 2048

    # Deliberately well under the context limit, because it is counted with the
    # char/4 estimate rather than nomic's own tokenizer. Measured: a chunk of
    # 2,788 tiktoken tokens was rejected, 2,119 was accepted.
    embed_max_chunk_tokens: int = 1500

    # nomic-embed-text is trained with asymmetric task prefixes. Stored document
    # vectors include the document prefix, so changing either value means
    # re-embedding the whole collection, not just reconfiguring.
    embed_query_prefix: str = "search_query: "
    embed_document_prefix: str = "search_document: "

    # Throughput only — batching does not change the vectors (verified: identical
    # single vs batched). On this CPU, with the model already loaded, every size
    # from 1 to 32 ran at ~1.16 chunks/s, so 16 is chosen for fewer round trips
    # and a bounded failure (one bad chunk fails 16, not all). An earlier reading
    # that 16 was fastest came from a cold-model first run and was wrong.
    # Retune on a GPU, where larger batches genuinely run in parallel.
    embed_batch_size: int = 16

    qdrant_collection: str = "chunks"

    # --- Local generation model (P6-M1) ----------------------------------------
    # No digest lock here on purpose (contrast embed_model_digest above): a drifted
    # embedding model makes stored vectors SILENTLY wrong with no error at all, so it
    # is hard-pinned. A drifted generation model just answers with different wording
    # or quality -- an observable difference, not a silent, undetectable one -- so a
    # lighter "is it pulled and does it actually answer" check is enough for now.
    llm_model: str = "mistral"


settings = Settings()
