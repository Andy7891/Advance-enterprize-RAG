# WATCH IT WORK: messy names -> rounds -> result -> a picture -> Neo4j -> checks. Needs: docker compose up -d neo4j
#   python "practice codes/phase 4/m4_entity_dedup_deep_dive/watch_it_work.py"          load, verify, clean up
#   python "practice codes/phase 4/m4_entity_dedup_deep_dive/watch_it_work.py" --keep   leave the __demo_ nodes to look at in Neo4j Browser
#   python "practice codes/phase 4/m4_entity_dedup_deep_dive/watch_it_work.py" --clean  remove them
import sys                                                                   # to read the command-line flag
import src.graph.dedup as dd                                                 # the module under test (also patched below to show the rounds)
from src.graph.records import GraphEdge, GraphNode, NodeRef                  # the record types
from src.graph.setup import build_driver                                     # the project's driver factory
from src.retrieval.graph_store import GraphStore                             # the P4-M3 store

PREFIX = "__demo_"                                                           # every name written to Neo4j starts with this, so cleanup is exact


def sweep(session):                                                          # delete everything this script ever wrote
    session.run("MATCH (n) WHERE n.name STARTS WITH $p DETACH DELETE n", p=PREFIX).consume()  # only prefixed nodes, with their edges


def pnode(n):                                                                # the same node with a prefixed key (applied AFTER merging)
    return GraphNode(n.label, {k: PREFIX + v for k, v in n.key.items()}, dict(n.properties))  # key changes, properties stay


def pref(r):                                                                 # the same reference with a prefixed key
    return NodeRef(r.label, {k: PREFIX + v for k, v in r.key.items()})      # used for edge endpoints


emp = lambda name: GraphNode("Employee", {"name": name})                     # shorthand for the Employee nodes below
nodes = [emp("Avery Lancaster"), emp("Avery"), emp("Ms. Lancaster"),         # one person, three spellings
         emp("Alex"), emp("Alex Chen"), emp("Alex Harper"),                  # a short name that fits two people
         GraphNode("Company", {"name": "Pinnacle Insurance Co."}), GraphNode("Company", {"name": "Pinnacle Insurance"}),  # a legal suffix
         GraphNode("Location", {"name": "San Francisco, CA"}), GraphNode("Location", {"name": "San Francisco, California"}),  # a state code
         GraphNode("Product", {"name": "Carllm"})]                           # a product the edges point at
ref = lambda label, name: NodeRef(label, {"name": name})                     # shorthand for a reference
edges = [GraphEdge("WORKS_ON", ref("Employee", "Avery"), ref("Product", "Carllm")),                 # written against the short name
         GraphEdge("WORKS_ON", ref("Employee", "Avery Lancaster"), ref("Product", "Carllm")),       # written against the full name
         GraphEdge("BASED_IN", ref("Employee", "Avery"), ref("Location", "San Francisco, CA"), {"remote": False})]  # to the abbreviated place

if "--clean" in sys.argv:                                                    # cleanup-only mode
    with build_driver() as driver, driver.session() as session:              # connect
        sweep(session)                                                       # remove the demo nodes
    sys.exit("cleaned")                                                      # stop here

real, rounds = dd._merge_groups, []                                          # STEP 1: watch the rounds by wrapping one function


def spy(groups, merges):                                                     # a stand-in for the merge step: report, then delegate
    rounds.append(merges)                                                    # count the round
    print(f"  round {len(rounds)}: merges (group i -> group j) = {merges}")  # show the decisions made this round
    return real(groups, merges)                                              # do the real merge


dd._merge_groups = spy                                                       # plug the spy in (the loop looks the function up by name)
print("STEP 1  rounds that changed something:")                              # heading
result = dd.resolve_entities(nodes)                                          # STEP 2: run the real merge
dd._merge_groups = real                                                      # restore the real function
print("\nSTEP 2  merged nodes:")                                             # heading
for n in result.nodes:                                                       # each surviving node
    print(f"  {n.label:<9} {n.key['name']:<28} aliases={n.properties.get('aliases', [])}")  # its name and the spellings it absorbed
print("  left apart and reported:", [(a.mention, a.candidates) for a in result.ambiguous])  # the doubt
print("  alias map:", [(o.key["name"], n.key["name"]) for o, n in result.alias_map.pairs()])  # who went where
fixed = dd.remap_edges(edges, result.alias_map)                              # STEP 3: point edges at surviving nodes
print(f"\nSTEP 3  edges: {len(edges)} written -> {len(fixed)} after remapping:")  # the count drops by one
for e in fixed:                                                              # each surviving edge
    print(f"  {e.source.key['name']} -[{e.type}]-> {e.target.key['name']}")  # readable form

print("\nSTEP 4  picture (paste into https://mermaid.live):\ngraph LR")     # heading; Mermaid is a text format for diagrams
for i, (old, new) in enumerate(result.alias_map.pairs()):                    # one arrow per merged-away spelling
    print(f'  a{i}["{old.key["name"]}"] --> b{i}["{new.key["name"]}"]')      # spelling --> canonical node

with build_driver() as driver, driver.session() as session:                  # STEP 5: write to Neo4j
    try:                                                                     # cleanup must run even if a check fails
        sweep(session)                                                       # start from nothing
        store = GraphStore(driver)                                           # the store (checks the constraints exist)
        for n in result.nodes:                                               # nodes first (edges need their endpoints)
            store.upsert_node(pnode(n))                                      # write the prefixed copy
        for e in fixed:                                                      # then edges
            store.upsert_edge(GraphEdge(e.type, pref(e.source), pref(e.target), dict(e.properties)))  # write the prefixed copy
        counts = session.run("MATCH (n) WHERE n.name STARTS WITH $p RETURN labels(n)[0] AS l, count(n) AS c ORDER BY l", p=PREFIX).data()  # STEP 6: look
        print("\nSTEP 6  what Neo4j holds:", {r["l"]: r["c"] for r in counts})  # nodes per label
        print("  edges of the full name :", len(store.search_related(PREFIX + "Avery Lancaster")))  # both edges arrived on ONE node
        print("  search by the alias    :", len(store.search_related(PREFIX + "Avery")), "<- 0: the store matches exact names only")  # aliases are not searchable
        hit = session.run("MATCH (e:Employee) WHERE 'Avery' IN e.aliases RETURN e.name AS n").value("n")  # a scan over lists, not an index seek
        print("  Cypher on the list     :", hit, "<- findable, but only by scanning")  # what alias lookup costs today
        if "--keep" in sys.argv:                                             # optional: leave the nodes for Neo4j Browser
            print("\nkept. In http://localhost:7474 run:  MATCH (n) WHERE n.name STARTS WITH '__demo_' RETURN n")  # the query to run
            sys.exit(0)                                                      # skip cleanup (finally below still runs, so exit after it)
    finally:                                                                 # cleanup
        if "--keep" not in sys.argv:                                         # unless asked to keep them
            sweep(session)                                                   # remove the demo nodes
