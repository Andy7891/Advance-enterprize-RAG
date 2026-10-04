# C5: the subset rule -- a short name joins a longer one only if EXACTLY ONE longer name holds all its words. No database needed.
from src.graph.dedup import resolve_entities                                 # the real function, to compare with the idea
from src.graph.records import GraphNode                                      # the record type it works on


def supersets(short, names):                                                 # the idea in three lines, for teaching
    words = set(short.lower().split())                                       # the short name as a set of words
    return [n for n in names if words < set(n.lower().split())]              # every name whose words strictly contain them


for names in (["Alice", "Alice Johnson"], ["Alice", "Alice Johnson", "Alice Smith"]):  # one candidate, then two
    found = supersets("Alice", names)                                        # who could "Alice" be?
    verdict = "merge" if len(found) == 1 else "refuse (report it)" if found else "nothing to join"  # the rule's three outcomes
    print(f"{names!s:<50} candidates={found} -> {verdict}")                  # show the rule's decision

result = resolve_entities([GraphNode("Employee", {"name": n}) for n in ["Alice", "Alice Johnson", "Alice Smith"]])  # the real function, three names
print("real nodes  :", [n.key["name"] for n in result.nodes])               # all three survive
print("real report :", result.ambiguous)                                     # and the doubt is written down as an Ambiguity
