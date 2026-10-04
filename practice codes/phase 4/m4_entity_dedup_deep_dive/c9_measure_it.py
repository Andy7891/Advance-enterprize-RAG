# C9: measuring a deduplicator -- pairs, precision, recall, and two properties. No database needed.
import itertools                                                             # to list every pair inside a group
import random                                                                # to shuffle the input
from src.graph.dedup import deduplicate_entities, resolve_entities          # the function under test
from src.graph.records import GraphNode                                      # the record type it works on

names = ["Avery", "Avery Lancaster", "Rachel", "Rachel Martinez", "Alex", "Alex Chen", "Alex Harper"]  # a tiny labelled set
truth = [{"Avery", "Avery Lancaster"}, {"Rachel", "Rachel Martinez"}, {"Alex Chen"}, {"Alex Harper"}, {"Alex"}]  # who really is who
nodes = [GraphNode("Employee", {"name": n}) for n in names]                  # the input


def pairs(groups):                                                           # every two names that share a group
    return {frozenset(p) for g in groups for p in itertools.combinations(sorted(g), 2)}  # unordered pairs


amap = resolve_entities(nodes).alias_map                                     # which spelling went where
by_node = {}                                                                 # canonical name -> the names merged into it
for n in names:                                                              # place every name under where it landed
    by_node.setdefault(amap.resolve(nodes[names.index(n)].ref).key["name"], set()).add(n)  # follow the alias map
predicted, actual = pairs(by_node.values()), pairs(truth)                    # pairs the code merged vs pairs that are truly one
hit = predicted & actual                                                     # merged AND truly one
print("precision:", len(hit) / len(predicted), "| recall:", len(hit) / len(actual))  # both 1.0 here

order_ok = all(                                                              # property 1: the input order must not matter
    deduplicate_entities(random.Random(s).sample(nodes, len(nodes))) == deduplicate_entities(nodes) for s in range(200))  # 200 shuffles
once = deduplicate_entities(nodes)                                           # property 2: running it again must change nothing
print("order-independent:", order_ok, "| idempotent:", deduplicate_entities(once) == once)  # both True
