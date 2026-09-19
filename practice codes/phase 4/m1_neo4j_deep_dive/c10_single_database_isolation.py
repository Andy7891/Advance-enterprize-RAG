# C10: Community Edition has ONE user database -- so test isolation must use labels, not scratch databases.
import uuid                                                      # unique run id

from neo4j.exceptions import ClientError                         # raised for unsupported commands

from src.graph.setup import build_driver                         # the project's driver factory

with build_driver() as driver:
    # 1. The databases that exist, and what Community Edition says about creating another
    with driver.session(database="system") as system:           # admin commands run against "system"
        print("1. databases:", [r["name"] for r in system.run("SHOW DATABASES YIELD name")])
        try:
            system.run("CREATE DATABASE scratch_test").consume()
        except ClientError as e:
            print("   CREATE DATABASE ->", e.code, "|", e.message[:80])

    # 2. The isolation pattern the project will use instead: a unique label per test run
    run_label = f"TestRun_{uuid.uuid4().hex[:8]}"
    with driver.session() as s:
        try:
            s.run(f"UNWIND range(1, 3) AS i CREATE (:Chunk:{run_label} {{i: i}})").consume()
            mine = s.run(f"MATCH (n:{run_label}) RETURN count(n) AS n").single()["n"]
            everyone = s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
            print(f"2. nodes with my label: {mine} | all nodes in the database: {everyone}")
        finally:
            s.run(f"MATCH (n:{run_label}) DETACH DELETE n").consume()
            left = s.run("MATCH (n) RETURN count(n) AS n").single()["n"]
            print("3. after cleanup, nodes in the database:", left)
