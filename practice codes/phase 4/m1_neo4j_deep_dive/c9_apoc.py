# C9: APOC -- what the plugin adds, and a schema read that GraphRAG tooling relies on (self-cleaning).
import uuid                                                      # unique label for isolation

from src.graph.setup import build_driver                         # the project's driver factory

label = f"Demo_{uuid.uuid4().hex[:8]}"                           # isolates this demo's nodes

with build_driver() as driver, driver.session() as session:
    # 1. How many procedures and functions the plugin registered
    procs = session.run("SHOW PROCEDURES YIELD name WHERE name STARTS WITH 'apoc.' RETURN count(*) AS n").single()["n"]
    funcs = session.run("SHOW FUNCTIONS YIELD name WHERE name STARTS WITH 'apoc.' RETURN count(*) AS n").single()["n"]
    print(f"1. APOC {session.run('RETURN apoc.version() AS v').single()['v']}: {procs} procedures, {funcs} functions")

    # 2. A few everyday APOC functions (plain Cypher cannot do these directly)
    print("2. apoc.text.clean   :", session.run("RETURN apoc.text.clean('Car-LLM  Product!') AS v").single()["v"])
    print("   apoc.coll.frequencies:", session.run(
        "RETURN apoc.coll.frequencies(['car','life','car']) AS v").single()["v"])

    try:
        # 3. Schema inspection: what labels, relationship types and properties exist (LangChain uses this)
        session.run(
            f"CREATE (:Person:{label} {{name:'Avery'}})-[:CEO_OF]->(:Company:{label} {{name:'Insurellm'}})"
        ).consume()
        meta = session.run(
            f"CALL apoc.meta.data({{includeLabels: ['Person', 'Company', '{label}']}}) "
            "YIELD label, property, type, elementType "
            "WHERE label IN ['Person', 'Company'] RETURN label, property, type, elementType ORDER BY label, property"
        ).data()
        print("3. apoc.meta.data (Person/Company only):")
        for row in meta:
            print("     ", row)
    finally:
        n = session.run(f"MATCH (n:{label}) DETACH DELETE n RETURN count(n) AS n").single()["n"]
        print("4. cleanup:", n, "nodes deleted")
