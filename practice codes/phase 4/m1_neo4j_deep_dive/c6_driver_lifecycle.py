# C6: the Python driver step by step -- lazy driver, connectivity check, session, result, summary.
import time                                                     # to time each step

from neo4j import GraphDatabase                                 # the official driver entry point
from neo4j.exceptions import ServiceUnavailable                 # raised when no server answers

from src.core.config import settings                            # URI / user / password from .env

# 1. Creating a driver does NOT connect: even a port where nothing listens raises no error here
nowhere = GraphDatabase.driver("bolt://localhost:7699", auth=("neo4j", "x"), connection_timeout=2)
print("1. driver to an empty port created without error:", type(nowhere).__name__)
try:
    nowhere.verify_connectivity()                               # the FIRST network contact happens here
except ServiceUnavailable as e:
    print("   verify_connectivity ->", type(e).__name__)
finally:
    nowhere.close()                                             # always release the pool

# 2. The real server: time each step
t = time.perf_counter()
driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
print(f"2. driver created           : {1000 * (time.perf_counter() - t):6.1f} ms (no network yet)")
t = time.perf_counter()
driver.verify_connectivity()                                    # opens a connection: Bolt handshake + login
print(f"   verify_connectivity      : {1000 * (time.perf_counter() - t):6.1f} ms (handshake + authentication)")
info = driver.get_server_info()                                 # what the server told us during the handshake
print("   server agent / protocol  :", info.agent, "/", info.protocol_version)

# 3. Session -> run -> Result -> Record -> summary
with driver.session(database="neo4j") as session:               # a session borrows connections from the pool
    result = session.run("RETURN 1 + $x AS answer", x=41)       # $x is a query PARAMETER, sent separately
    record = result.single()                                    # exactly one row -> one Record
    summary = result.consume()                                  # metadata about the query that just ran
    print("3. record                   :", record, "| record['answer'] =", record["answer"])
    print("   query type / server time :", summary.query_type, "/", summary.result_available_after, "ms")

# 4. Why parameters instead of string formatting: the value is data, never Cypher code
with driver.session() as session:
    tricky = "x' RETURN 'injected"
    echoed = session.run("RETURN $value AS v", value=tricky).single()["v"]
    print("4. parameter kept as data   :", repr(echoed))
driver.close()                                                  # closes every pooled connection
