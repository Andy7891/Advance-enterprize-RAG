# C7: which exception each failure raises, and where it sits in the driver's exception hierarchy.
from neo4j import GraphDatabase                                  # driver factory
from neo4j.exceptions import (AuthError, ClientError, DriverError, Neo4jError,  # the classes we catch
                              ServiceUnavailable)

from src.core.config import settings                             # real credentials from .env

# 1. The family tree (method resolution order) of each exception class
for cls in (AuthError, ClientError, ServiceUnavailable):
    print(f"1. {cls.__name__:<18} ->", " > ".join(c.__name__ for c in cls.__mro__[1:5]))


def attempt(label, uri, password, query="RETURN 1"):
    driver = GraphDatabase.driver(uri, auth=(settings.neo4j_user, password), connection_timeout=3)
    try:
        driver.verify_connectivity()                             # connection + login
        with driver.session() as s:
            s.run(query).consume()                               # the query itself
        print(f"2. {label:<22} -> OK")
    except (Neo4jError, DriverError) as e:                       # every driver/server error ends up here
        code = getattr(e, "code", None)                          # server errors carry a Neo4j status code
        branch = next(c.__name__ for c in (AuthError, ServiceUnavailable, ClientError, Neo4jError, DriverError)
                      if isinstance(e, c))                       # the most specific class we catch
        print(f"2. {label:<22} -> {type(e).__name__:<18} caught as {branch:<18} code={code}")
    finally:
        driver.close()


attempt("everything correct", settings.neo4j_uri, settings.neo4j_password)
attempt("nothing on that port", "bolt://localhost:7699", settings.neo4j_password)
attempt("wrong password (once)", settings.neo4j_uri, "wrong-password")
attempt("unknown function", settings.neo4j_uri, settings.neo4j_password, "RETURN nosuch.fn() AS x")
attempt("Cypher syntax error", settings.neo4j_uri, settings.neo4j_password, "RETURN (")
