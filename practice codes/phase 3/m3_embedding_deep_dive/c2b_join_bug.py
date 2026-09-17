# Why Embedding carries the chunk instead of two parallel lists joined later.
chunks = ["refund policy: 30 days", "CEO is Avery Lancaster", "office in Paris"]
vectors = ["<vec refund>", "<vec ceo>", "<vec paris>"]          # vectors[i] belongs to chunks[i]

# Design C (parallel lists): someone "tidies" the chunks by sorting them, and forgets the vectors.
chunks_sorted = sorted(chunks)
print("Parallel lists after sorting only one of them:")
for text, vec in zip(chunks_sorted, vectors):
    print(f"   {text!r:<28} stored with {vec}")

# Design B (one object per pair): sorting moves text and vector together.
pairs = [{"chunk": c, "vector": v} for c, v in zip(chunks, vectors)]
pairs_sorted = sorted(pairs, key=lambda p: p["chunk"])
print("Pairs after sorting:")
for p in pairs_sorted:
    print(f"   {p['chunk']!r:<28} stored with {p['vector']}")
