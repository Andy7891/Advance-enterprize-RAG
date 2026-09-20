# C6: constraint DDL cannot take labels or property names as parameters, so the code splices names and validates them.
# Live Neo4j for the syntax checks (only harmless failing statements plus one throwaway constraint), self-cleaning.
import uuid                                                              # to make a label no other data can have

from neo4j.exceptions import Neo4jError                                  # base class of every error the server reports

from src.graph.schema import NODE_TYPES, NodeType, constraint_statement  # the model class, the model and the DDL generator
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

label = f"Demo{uuid.uuid4().hex[:8]}"                                    # a unique label; nothing is ever created under it


def attempt(session, title: str, query: str, **parameters) -> None:      # run one statement and report success or the failure class
    try:                                                                 # the server may refuse
        session.run(query, **parameters).consume()                       # run it and wait for the result
        print(f"   OK    {title}")                                       # accepted
    except Neo4jError as exc:                                            # refused
        print(f"   FAIL  {title} -> {type(exc).__name__}")               # show which class of error


with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    print("1. what the server accepts as a parameter (Neo4j 5.26):")     # observed, not assumed
    attempt(session, "MATCH (n:$label)                      label as parameter", "MATCH (n:$label) RETURN n", label=label)  # not allowed
    attempt(session, "FOR (n:$label) ... constraint          label as parameter", "CREATE CONSTRAINT d1 IF NOT EXISTS FOR (n:$label) REQUIRE n.name IS UNIQUE", label=label)  # not allowed
    attempt(session, "REQUIRE n.$p IS UNIQUE                 property as parameter", f"CREATE CONSTRAINT d2 IF NOT EXISTS FOR (n:{label}) REQUIRE n.$p IS UNIQUE", p="name")  # not allowed
    try:                                                                 # a constraint NAME, however, may be a parameter
        attempt(session, "CREATE CONSTRAINT $cname ...           constraint name as parameter", f"CREATE CONSTRAINT $cname IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE", cname=f"demo_{label}")  # allowed
    finally:                                                             # clean up the one constraint that was created
        session.run(f"DROP CONSTRAINT demo_{label} IF EXISTS").consume()  # drop it

print("2. so the code splices names into the text, after validating them:")  # the project's answer
evil = NodeType("Company) detach delete n //", ("name",), "domain", "")  # a label crafted to break out of the pattern
naive = f"CREATE CONSTRAINT x IF NOT EXISTS FOR (n:{evil.label}) REQUIRE n.name IS UNIQUE"  # what plain string formatting would produce
print("   naive f-string would send   :", naive)                         # the label closed the pattern and added a clause
try:                                                                     # the project's generator validates every name first
    constraint_statement(evil)                                           # this must refuse
except ValueError as exc:                                                # plain identifiers only
    print("   constraint_statement says   :", exc)                      # rejected before anything reaches the database

print("3. the eight statements the model generates:")                    # the real DDL for the real model
for node in NODE_TYPES:                                                  # each node type in the registry
    print("  ", constraint_statement(node))                             # the exact text sent to Neo4j
