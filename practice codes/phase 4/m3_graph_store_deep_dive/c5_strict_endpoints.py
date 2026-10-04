# C5: referential integrity -- an edge may only join nodes that exist, or a typo becomes a ghost node.
from src.graph.records import GraphEdge, GraphNode, NodeRef                  # the records built in P4-M3
from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)
from src.retrieval.graph_store import GraphStore, MissingEndpoint            # the store and its error

contract = NodeRef("Contract", {"doc_id": "__demo_k1"})                      # a contract that will exist (the prefix marks demo data)
typo = NodeRef("Product", {"name": "__demo_Carlllm"})                        # a product name with a typo, which will not exist


def products(session) -> int:                                                # how many demo products exist
    return session.run("MATCH (p:Product) WHERE p.name STARTS WITH '__demo_' RETURN count(p) AS c").single()["c"]  # count them


with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        store = GraphStore(driver)                                           # refuses to start unless the schema is in force
        store.upsert_node(GraphNode("Contract", {"doc_id": "__demo_k1"}))    # only the contract exists
        try:                                                                 # writing an edge to the typo'd product...
            store.upsert_edge(GraphEdge("FOR_PRODUCT", contract, typo))      # ...must be refused
        except MissingEndpoint as error:                                     # the store's own error
            print("strict store:", error)                                    # it names the missing node
        print("products after the refusal:", products(session))              # 0: no ghost was created
        session.run("MATCH (c:Contract {doc_id: '__demo_k1'}) MERGE (c)-[:FOR_PRODUCT]->(:Product {name: '__demo_Carlllm'})").consume()  # the alternative: MERGE the endpoint
        print("products after a MERGEd endpoint:", products(session))        # 1: the typo is now a real product node
    finally:                                                                 # always clean up
        session.run("MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) STARTS WITH '__demo_') DETACH DELETE n").consume()  # remove demo nodes by prefix
