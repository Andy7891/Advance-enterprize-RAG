# Minimal demo of C1: Neo4j's MERGE makes ONE write idempotent, but it is not a re-run
# POLICY for a whole job -- it only ever adds, so it can never notice that something the
# last run wrote should now be gone. Pure Python, no database: a dict stands in for Neo4j.
graph = {}  # stand-in for "what Neo4j holds": key -> properties, exactly like a real MERGE target


def merge(label, key, props):
    # This is exactly what GraphStore.upsert_node's Cypher does: MERGE (n:label {key})
    # SET n += props -- find the node by its key, or create it, then add the properties.
    identity = (label, key)  # a node's real-world identity: its label plus its unique key
    existing = graph.get(identity, {})  # look up whatever is already stored under this identity
    graph[identity] = {**existing, **props}  # keep old fields, let new ones override -- SET n +=


print("Run 1: load two products")  # the FIRST ingestion run, as if from a fresh corpus
merge("Product", "Carllm", {"tagline": "AI-Powered Autos"})  # first product, first time seen
merge("Product", "Rellm", {"tagline": "Real Estate AI"})  # second product, first time seen
print("  graph now:", sorted(graph))  # show every identity currently stored

print("\nRun 2: re-run the SAME two products (nothing changed in the source)")  # idempotency test
merge("Product", "Carllm", {"tagline": "AI-Powered Autos"})  # same key, same properties again
merge("Product", "Rellm", {"tagline": "Real Estate AI"})  # same key, same properties again
print("  graph now:", sorted(graph))  # still exactly two entries: MERGE did not duplicate anything

print("\nRun 3: the corpus changed -- Rellm's product page was DELETED")  # simulate a real edit
merge("Product", "Carllm", {"tagline": "AI-Powered Autos"})  # Carllm is still on disk, so it's re-merged
# Notice: there is no line here for Rellm. A real ingestion job would simply stop calling
# merge() for a product that is no longer on disk -- MERGE never gets a chance to delete it.
print("  graph now:", sorted(graph))  # Rellm is STILL there: MERGE alone can only add, never remove

print("\nThe fix our job uses: an explicit --rebuild that wipes ownership labels FIRST")
managed_labels = {"Product"}  # the job's own record of which labels it is responsible for


def rebuild(graph, managed_labels):
    # This mirrors _wipe_managed_labels: DETACH DELETE every node under labels this job owns,
    # before loading anything, so a deleted source document cannot leave a stale node behind.
    for identity in list(graph):  # iterate over a COPY of the keys, since we're deleting while looping
        if identity[0] in managed_labels:  # only touch labels this job owns, never a stranger's data
            del graph[identity]  # the DETACH DELETE equivalent: remove the stale entry entirely


rebuild(graph, managed_labels)  # wipe every Product node before reloading today's corpus
merge("Product", "Carllm", {"tagline": "AI-Powered Autos"})  # reload only what is ACTUALLY on disk now
print("  graph after --rebuild + reload:", sorted(graph))  # Rellm is finally, correctly, gone
