# C8: the alias map and edge remapping -- keeping relationships alive after a merge. No database needed.
from src.graph.dedup import remap_edges, resolve_entities                    # the alias map's user and the merge itself
from src.graph.records import GraphEdge, GraphNode, NodeRef                  # records for nodes, references and edges

nodes = [GraphNode("Employee", {"name": "Alice"}), GraphNode("Employee", {"name": "Alice Johnson"}),  # two spellings of one person
         GraphNode("Product", {"name": "Carllm"})]                           # and a product they work on
result = resolve_entities(nodes)                                             # merge them
for old, new in result.alias_map.pairs():                                    # every spelling that disappeared
    print("alias map :", old.key["name"], "->", new.key["name"])             # where it now points

carllm = NodeRef("Product", {"name": "Carllm"})                              # a reference to the product node
edges = [                                                                    # the extractor wrote one edge per spelling it saw
    GraphEdge("WORKS_ON", NodeRef("Employee", {"name": "Alice"}), carllm),   # written against the short spelling
    GraphEdge("WORKS_ON", NodeRef("Employee", {"name": "Alice Johnson"}), carllm),  # written against the full spelling
]
print("edges before:", len(edges), [e.source.key["name"] for e in edges])    # two edges, two different sources
fixed = remap_edges(edges, result.alias_map)                                 # point both at the canonical node
print("edges after :", len(fixed), [e.source.key["name"] for e in fixed])    # they became identical, so they collapsed into one
print("unknown ref :", result.alias_map.resolve(NodeRef("Employee", {"name": "Zed"})).key["name"])  # a reference nobody merged resolves to itself
