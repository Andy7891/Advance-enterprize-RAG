# C8: looking an entity up by name -- namesakes, and why domain edges and provenance edges are kept apart.
from src.graph.records import GraphEdge, GraphNode, NodeRef                  # the records built in P4-M3
from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)
from src.retrieval.graph_store import GraphStore                             # the store under study

N = "__demo_Robert Chen"                                                     # one name, shared by two different people (the prefix marks demo data)
with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        store = GraphStore(driver)                                           # refuses to start unless the schema is in force
        product = NodeRef("Product", {"name": "__demo_Homellm"})             # a product
        employee = NodeRef("Employee", {"name": N})                          # an Insurellm engineer called Robert Chen
        contact = NodeRef("Contact", {"name": N, "organization": "Fortress"})  # a client's officer with the same name
        contract = NodeRef("Contract", {"doc_id": "__demo_k1"})              # a contract the client's officer signed
        chunk = NodeRef("Chunk", {"chunk_id": "__demo_about_0"})             # a chunk of text that mentions the product
        for ref in (product, employee, contact, contract, chunk):            # write every node first: edges need both ends to exist
            store.upsert_node(GraphNode(ref.label, dict(ref.key)))           # one upsert per node
        store.upsert_edge(GraphEdge("WORKS_ON", employee, product))          # the engineer works on the product
        store.upsert_edge(GraphEdge("SIGNED_BY", contract, contact, {"title": "Chief Underwriting Officer"}))  # the officer signed the contract
        store.upsert_edge(GraphEdge("MENTIONS", chunk, product))             # a provenance edge: where the product was read

        both = store.search_related(N)                                       # by name alone: every node with that name is searched
        print("namesakes, no label :", [e.type for e in both])               # both people's edges come back
        only = store.search_related(N, label="Employee")                     # a label narrows it to one person
        print("label='Employee'    :", [e.type for e in only])               # just the engineer's edge

        plain = store.search_related("__demo_Homellm")                       # a product's edges, default
        print("product, default    :", [e.type for e in plain])              # domain edges only: no MENTIONS
        full = store.search_related("__demo_Homellm", include_provenance=True)  # a product's edges, provenance included
        print("include_provenance  :", [e.type for e in full])               # MENTIONS appears
    finally:                                                                 # always clean up
        session.run("MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) STARTS WITH '__demo_') DETACH DELETE n").consume()  # remove demo nodes by prefix
