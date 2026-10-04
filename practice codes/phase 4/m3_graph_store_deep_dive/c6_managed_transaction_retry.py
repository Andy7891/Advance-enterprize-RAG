# C6: a managed transaction re-runs its function on a transient failure, so the function must be idempotent.
from neo4j.exceptions import TransientError                                  # the kind of error the driver treats as "try again"

from src.graph.setup import build_driver                                     # the project's driver factory (reads .env)

attempts = []                                                                # one entry per time the driver runs our function


def work(tx):                                                                # the transaction function handed to the driver
    attempts.append(1)                                                       # record this attempt
    tx.run("MERGE (n:DemoRetry {k: 1})").consume()                           # an idempotent write: MERGE finds what an earlier attempt made
    if len(attempts) == 1:                                                   # on the first attempt only...
        raise TransientError("simulated deadlock")                           # ...fail as a deadlock would


with build_driver() as driver, driver.session() as session:                  # open a connection, then one session
    try:                                                                     # cleanup below must always run
        session.execute_write(work)                                          # run it as a managed write transaction
        print("times the function ran:", len(attempts))                      # more than once: the driver retried it
        nodes = session.run("MATCH (n:DemoRetry) RETURN count(n) AS c").single()["c"]  # how many nodes exist afterwards
        print("nodes created:", nodes)                                       # 1: the retry did not duplicate the write
    finally:                                                                 # always clean up
        session.run("MATCH (n:DemoRetry) DETACH DELETE n").consume()         # remove the demo's node
