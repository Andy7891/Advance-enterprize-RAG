# C7: send VALUES as parameters (they stay data); only names from the model may be spliced into query text.
from neo4j.exceptions import Neo4jError                                      # base class of every error the server reports

from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)

hostile = "x'}) DETACH DELETE n //"                                          # a "name" that tries to close the pattern and add a clause
unsafe = f"MATCH (n:Product {{name: '{hostile}'}}) RETURN count(n) AS c"     # what plain string formatting would build (printed, never run)
print("formatted into the text:", unsafe)                                    # the hostile value has become part of the query

with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    safe = session.run(                                                      # the same lookup with the value as a parameter
        "MATCH (n:Product {name: $name}) RETURN count(n) AS c", name=hostile  # $name is bound after parsing, so it can only be data
    ).single()["c"]                                                          # the count of products with that exact name
    print("passed as a parameter:", safe, "matches, nothing executed")      # 0: it was just a string that matched nothing
    try:                                                                     # a LABEL, however, cannot be a parameter...
        session.run("MATCH (n:$label) RETURN n", label="Product").consume()  # ...so this is refused
    except Neo4jError as error:                                              # the server rejects it while parsing
        print("label as a parameter:", type(error).__name__)                 # a syntax error: labels must be spliced, so they must come from the model
    try:                                                                     # a query written by an LLM should run read-only
        session.execute_read(lambda tx: tx.run("CREATE (:DemoReadOnly)").consume())  # a write attempted inside a read transaction
    except Neo4jError as error:                                              # the server refuses it
        print("write in a read transaction:", error.code)                    # AccessMode: a second line of defence
