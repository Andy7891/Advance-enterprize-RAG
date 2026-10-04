# C10: test what the store SENDS with a fake driver, and see how a test notices a wrong edit. No database needed.
from src.graph.records import GraphNode                                      # the record to write
from src.retrieval.graph_store import GraphStore                             # the store under study

sent = []                                                                    # every (query, parameters) the store sends


class Result:                                                                # what a query returns
    def consume(self):                                                       # upsert_node's only call on a result
        return None                                                          # nothing to report


class Tx:                                                                    # stands in for a transaction
    def run(self, query, params):                                            # the store calls this with its Cypher and parameters
        sent.append((query, params))                                         # record them instead of talking to a server
        return Result()                                                      # hand back the stand-in result


class Session:                                                               # stands in for a session
    def __enter__(self):                                                     # the store uses `with driver.session() as session`
        return self                                                          # the session is itself
    def __exit__(self, *exc):                                                # nothing to close
        return None                                                          # do not swallow exceptions
    def execute_write(self, work):                                           # the managed-transaction entry point
        return work(Tx())                                                    # run the store's function against the stand-in


class Driver:                                                                # stands in for the driver
    def session(self, *, database=None):                                     # the store asks for a session
        return Session()                                                     # hand back the stand-in


store = GraphStore(Driver(), verify_schema=False)                            # the injected fake; skip the schema check, which needs a server
store.upsert_node(GraphNode("Company", {"name": "Acme"}, {"legal_name": "Acme Inc."}))  # write one node
query, params = sent[0]                                                      # what the store actually sent
print("query :", query)                                                      # the Cypher text
print("params:", params)                                                     # the parameters: values travel separately from the text
print("test passes on the real query :", "SET n += $props" in query)         # the assertion a test makes
mutant = query.replace("+=", "=")                                            # a mutant: the query after a wrong edit (SET = would wipe the key)
print("test passes on the mutant     :", "SET n += $props" in mutant)        # False: the test fails, so the mutant is caught
