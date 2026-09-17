# Why a check against CONFIG cannot catch a CONFIG change, and a check against STORED data can.
from dataclasses import dataclass


@dataclass
class Settings:
    prefix: str


stored_points = [{"chunk": f"about_{i}", "prefix": "search_document: "} for i in range(3)]   # built last week


def check_against_config(settings, new_embedding_prefix):
    # what QdrantVectorStore.upsert does: new embeddings vs current settings
    return new_embedding_prefix == settings.prefix


def check_against_stored(settings):
    # what the ingestion preflight does: stored points vs current settings
    return sum(1 for p in stored_points if p["prefix"] != settings.prefix)


for label, settings in [("settings unchanged", Settings("search_document: ")),
                        ("someone set prefix to ''", Settings(""))]:
    new_prefix = settings.prefix                    # the embedder reads the SAME settings
    ok_config = check_against_config(settings, new_prefix)
    incompatible = check_against_stored(settings)
    print(f"{label:<26} config check passes: {ok_config!s:<5} | stored points in another space: {incompatible}"
          f" -> {'REFUSE' if incompatible else 'proceed'}")
