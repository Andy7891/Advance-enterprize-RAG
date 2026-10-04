# Minimal demo of C1: two retrievers with DIFFERENT blind spots, then the simplest possible merge.
# The lists are the real ones the project produced for "Who is the CEO of Insurellm?" (shortened).
vector_top = [  # what vector search (P3) returned, best first
    "employees/Jordan K. Bishop.md_2",  # a chunk that only sounds similar to the question
    "contracts/Greenstone for Homellm.md_0",  # another similar-sounding chunk
    "contracts/Apex Reinsurance for Rellm.md_0",  # a third similar-sounding chunk
]
graph_top = [  # what graph retrieval (P5-M1) returned, best first
    "company/about.md_0",  # the page the project test accepts (it names the founder)
    "company/careers.md_0",  # another chunk connected to the entity Insurellm
    "company/culture.md_0",  # another chunk connected to the entity Insurellm
]
answer = "company/about.md_0"  # the chunk the project's test labels as an acceptable answer

print("vector alone finds the answer in its top 3:", answer in vector_top)  # False expected
print("graph  alone finds the answer in its top 3:", answer in graph_top)  # True expected
print("graph list for a question naming no entity:", [])  # graph is blind without an entity

merged = []  # the simplest merge: take one chunk from each list in turn
for vector_chunk, graph_chunk in zip(vector_top, graph_top):  # walks both lists side by side
    for chunk_id in (vector_chunk, graph_chunk):  # takes the vector pick, then the graph pick
        if chunk_id not in merged:  # skips a chunk already taken from the other list
            merged.append(chunk_id)  # adds the chunk to the merged list
print("interleaved merge, first 3:", merged[:3])  # the answer now surfaces at position 2
print("answer in merged top 3:", answer in merged[:3])  # True: the merge recovered it
# interleaving ignores agreement and weights, which is what RRF (C2, C3) adds
