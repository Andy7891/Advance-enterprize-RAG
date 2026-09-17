# Blue/green indexing with a Qdrant ALIAS (the option D5-C we did not choose), on the LIVE server.
# Readers always query the alias; the switch from old to new collection is one atomic call.
from qdrant_client.models import (CreateAlias, CreateAliasOperation, DeleteAlias, DeleteAliasOperation,
                                  Distance, PointStruct, VectorParams)

from src.vectorstore.setup import build_qdrant_client

client = build_qdrant_client("http://localhost:6333")
ALIAS, BLUE, GREEN = "dd15_live_index", "dd15_blue", "dd15_green"
for name in (BLUE, GREEN):
    if client.collection_exists(name):
        client.delete_collection(name)

def build(name, version, count):
    client.create_collection(name, vectors_config=VectorParams(size=2, distance=Distance.COSINE))
    client.upsert(name, wait=True, points=[PointStruct(id=i, vector=[1.0, 0.0], payload={"version": version})
                                           for i in range(count)])

def reader():
    points, _ = client.scroll(ALIAS, limit=10)          # the "application" only knows the alias
    return f"{len(points)} points, versions {sorted({p.payload['version'] for p in points})}"

build(BLUE, "v1", 3)
client.update_collection_aliases(change_aliases_operations=[
    CreateAliasOperation(create_alias=CreateAlias(collection_name=BLUE, alias_name=ALIAS))])
print("1. alias -> blue               :", reader())

build(GREEN, "v2", 2)                                   # build the new index while readers use blue
green_half_built = client.count(GREEN).count
print("2. green built, alias unchanged:", reader(), f"(green has {green_half_built} points, invisible to readers)")

client.update_collection_aliases(change_aliases_operations=[   # atomic: delete + create in ONE request
    DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=ALIAS)),
    CreateAliasOperation(create_alias=CreateAlias(collection_name=GREEN, alias_name=ALIAS)),
])
print("3. alias switched -> green     :", reader())
print("4. aliases on server           :", [(a.alias_name, a.collection_name) for a in client.get_aliases().aliases
                                           if a.alias_name == ALIAS])

client.delete_collection(BLUE)                          # old index kept until switch succeeded, then removed
client.delete_collection(GREEN)                         # (demo cleanup; also drops the alias)
print("5. cleanup: dd15 collections left:", [c.name for c in client.get_collections().collections if c.name.startswith("dd15")])
