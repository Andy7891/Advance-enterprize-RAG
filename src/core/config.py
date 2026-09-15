"""Centralized app configuration, loaded from environment variables (.env in dev)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Service hostnames default to Docker Compose service names.
    # Override with real values in .env for local (non-Docker) dev.
    qdrant_url: str = "http://qdrant:6333"
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme"
    ollama_url: str = "http://ollama:11434"

    cohere_api_key: str = ""


settings = Settings()
