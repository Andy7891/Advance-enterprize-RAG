# C1: a graph answers a question that needs facts from different documents, by following relationships.
import uuid                                                                  # to make a label nothing else uses

from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)

label = f"Demo{uuid.uuid4().hex[:8]}"                                        # a throwaway label, so cleanup is exact
with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        session.run(                                                         # write three facts as one chain
            f"CREATE (:{label} {{name: 'Rachel'}})-[:WORKS_ON]->(:{label} {{name: 'Carllm'}})"  # fact from an HR record: Rachel works on Carllm
            f"<-[:FOR_PRODUCT]-(:{label} {{name: 'Contract 7'}})-[:WITH_CLIENT]->(:{label} {{name: 'DriveSmart'}})"  # facts from a contract: for Carllm, with DriveSmart
        ).consume()                                                          # wait for the write to finish
        names = session.run(                                                 # ask: what is connected to Rachel?
            f"MATCH (:{label} {{name: 'Rachel'}})-[*1..3]-(t:{label}) RETURN DISTINCT t.name AS name"  # anything within 3 relationships, either direction
        ).value("name")                                                      # a plain list of names
        print(sorted(names))                                                 # DriveSmart appears, though no single document names both it and Rachel
    finally:                                                                 # always clean up
        session.run(f"MATCH (n:{label}) DETACH DELETE n").consume()          # remove the demo's nodes
