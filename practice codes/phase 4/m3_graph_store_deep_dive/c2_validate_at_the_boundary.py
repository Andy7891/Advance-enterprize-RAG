# C2: validate at the boundary -- a record that exists is a record the store can write. No database needed.
from src.graph.records import GraphEdge, GraphNode, InvalidRecord, NodeRef   # the records built in P4-M3

good = GraphNode("Company", {"name": "Acme"}, {"legal_name": "Acme Inc."})   # valid: a known label, its full key, storable properties
print("valid:", good.label, dict(good.key), dict(good.properties))           # show what it holds

mistakes = {                                                                 # five ways to get it wrong, each wrapped so it runs later
    "unknown label": lambda: GraphNode("Person", {"name": "Avery"}),         # the model has no Person label
    "missing key part": lambda: GraphNode("Contact", {"name": "Bob"}),       # a Contact needs name AND organization
    "padded key": lambda: GraphNode("Product", {"name": "Carllm "}),         # a trailing space would create a ghost node
    "None property": lambda: GraphNode("Product", {"name": "Carllm"}, {"line": None}),  # None would delete the property on write
    "wrong endpoint": lambda: GraphEdge(                                     # a client must be a Company, not a Product
        "WITH_CLIENT", NodeRef("Contract", {"doc_id": "k1"}), NodeRef("Product", {"name": "Carllm"})  # source and target labels
    ),
}
for reason, build in mistakes.items():                                       # try each mistake in turn
    try:                                                                     # each one must be refused
        build()                                                              # construct the bad record
    except InvalidRecord as error:                                           # the record types' own error
        print(f"{reason:<16} ->", error)                                     # the message names what is wrong
