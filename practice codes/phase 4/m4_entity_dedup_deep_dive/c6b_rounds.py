# C6b: rounds until nothing changes (a fixpoint), watched from outside. No database needed.
import src.graph.dedup as dd                                                 # the module, so we can wrap one of its functions
from src.graph.records import GraphNode                                      # the record type it works on

real_merge, calls = dd._merge_groups, []                                     # keep the real function; remember each round


def spy(groups, merges):                                                     # a stand-in that reports, then calls the real one
    forms = [" ".join(dd._fullest(g)) for g in groups]                       # what each group looks like this round
    calls.append(merges)                                                     # count the round
    print(f"  round {len(calls)}: groups={forms}  merges(i->j)={merges}")   # show the decisions made this round
    return real_merge(groups, merges)                                        # do the real merge


dd._merge_groups = spy                                                       # plug the spy in (the loop looks the function up by name)
names = ["Alice", "Alice Johnson", "Alice Johnson Smith"]                    # a short name, a middle name, a full name
nodes = [GraphNode("Employee", {"name": n}) for n in names]                  # as Employee nodes
print("first run:")                                                          # heading
first = dd.deduplicate_entities(nodes)                                       # run once: watch the rounds
print("  result :", [(n.key["name"], n.properties["aliases"]) for n in first])  # one node, two aliases
calls.clear()                                                                # forget the first run's rounds
print("second run, on the first run's own output:")                          # heading
second = dd.deduplicate_entities(first)                                      # run again on what came out
print("  rounds that merged anything:", len(calls), "| same result:", second == first)  # 0 rounds, identical: idempotent
dd._merge_groups = real_merge                                                # put the real function back
