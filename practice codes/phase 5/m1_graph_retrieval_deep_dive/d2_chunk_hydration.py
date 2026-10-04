# Minimal demo of C3: a graph edge is an ID, not content. Turning a graph hit into real
# text needs a SECOND lookup, into whatever store actually holds the text.
# Two separate, tiny "stores" -- exactly the shape of Neo4j (ids only) vs data/chunks (text).
graph_mentions = {
    # entity name -> the chunk_ids that MENTION it (this is all a graph edge ever gives you)
    "Carllm": ["products/Carllm.md_0", "contracts/Contract with DriveSmart.md_0"],
}

chunk_text_store = {
    # chunk_id -> the actual text (this is what data/chunks/*.json holds, not Neo4j)
    "products/Carllm.md_0": "# Carllm\n\nCarllm is Insurellm's auto insurance platform.",
    "contracts/Contract with DriveSmart.md_0": "# Contract with DriveSmart for Carllm\n\n...",
}


def graph_only_answer(entity):
    # what you'd have if you stopped at the graph: a list of IDs, no readable content
    return graph_mentions.get(entity, [])


def hydrated_answer(entity):
    # follows each id from the graph into the SEPARATE text store, to get real content
    return [chunk_text_store[chunk_id] for chunk_id in graph_mentions.get(entity, [])]


print("Graph-only result (what Neo4j alone can give you):")
for chunk_id in graph_only_answer("Carllm"):
    print(" ", chunk_id)

print("\nHydrated result (after the second lookup, into the text store):")
for text in hydrated_answer("Carllm"):
    print(" ", text.splitlines()[0])

print("\nThis is why GraphRetriever caches read_chunks() at construction time --")
print("every chunk_id the graph returns needs exactly this second lookup to become")
print("a real DocumentChunk with actual text in it.")
