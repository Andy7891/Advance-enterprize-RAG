# C9: variable-length paths -- routes vs entities, shortest distance, the start excluded, and a hop cap.
import uuid                                                                  # to make a label nothing else uses

from src.graph.records import GraphEdge, GraphNode, NodeRef                  # the records built in P4-M3
from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)
from src.retrieval.graph_store import GraphStore                             # the store under study

label = f"Demo{uuid.uuid4().hex[:8]}"                                        # a throwaway label for the raw-Cypher part
with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        session.run(f"CREATE (a:{label} {{n:'A'}})-[:R]->(:{label} {{n:'B'}})-[:R]->(d:{label} {{n:'D'}}), (a)-[:R]->(:{label} {{n:'C'}})-[:R]->(d)").consume()  # a diamond: two routes from A to D
        counts = session.run(                                                # count routes and entities for the same question
            f"MATCH p = (:{label} {{n:'A'}})-[*1..2]-(t:{label} {{n:'D'}}) RETURN count(p) AS routes, count(DISTINCT t) AS entities"  # A to D within 2 hops
        ).single()                                                           # one row
        print("A to D:", counts.data())                                      # 2 routes, but 1 entity: paths are not nodes

        store = GraphStore(driver)                                           # the store under study
        founder = NodeRef("Employee", {"name": "__demo_Avery"})              # a founder
        company = NodeRef("Company", {"name": "__demo_Insurellm"})           # the company they founded
        city = NodeRef("Location", {"name": "__demo_San Francisco"})        # its head-office city, where the founder also lives
        for ref in (founder, company, city):                                 # write the nodes first
            store.upsert_node(GraphNode(ref.label, dict(ref.key)))           # one upsert per node
        store.upsert_edge(GraphEdge("FOUNDED", founder, company))            # founder -> company
        store.upsert_edge(GraphEdge("HAS_OFFICE", company, city, {"headquarters": True}))  # company -> city
        store.upsert_edge(GraphEdge("BASED_IN", founder, city, {"remote": False}))  # founder -> city: a triangle
        print("3 hops from the founder:", store.multi_hop_search("__demo_Avery", 3))  # both neighbours once, at distance 1, and never the founder
        try:                                                                 # more hops than the cap...
            store.multi_hop_search("__demo_Avery", 4)                        # ...is refused
        except ValueError as error:                                          # the store's guard against path explosion
            print("4 hops:", error)                                          # it states the cap
    finally:                                                                 # always clean up
        session.run(f"MATCH (n:{label}) DETACH DELETE n").consume()          # remove the raw-Cypher nodes
        session.run("MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) STARTS WITH '__demo_') DETACH DELETE n").consume()  # remove demo nodes by prefix
