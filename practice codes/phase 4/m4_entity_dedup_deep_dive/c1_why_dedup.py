# C1: why deduplicate -- the same names treated three ways. No database needed.
from src.graph.dedup import resolve_entities                                 # the P4-M4 function that decides who is who
from src.graph.records import GraphNode                                      # the record type it works on


def emp(name):                                                               # tiny helper: a name becomes an Employee node
    return GraphNode("Employee", {"name": name})                             # label + key, exactly as the store would hold it


names = ["Alice", "alice", "Alice Johnson"]                                  # the task's example: ONE person written three ways
print("exact match      :", sorted(set(names)))                              # a plain set keeps all three: three "people"
print("lower-case match :", sorted({n.lower() for n in names}))             # folding case helps a little: still two
result = resolve_entities([emp(n) for n in names])                           # the real function
for node in result.nodes:                                                    # one node comes back
    print("real function    :", node.key["name"], "| aliases =", node.properties["aliases"])  # its name and the spellings it absorbed

crowd = ["Alex", "Alex Chen", "Alex Harper"]                                 # a short name that fits TWO different people
first_match = next(m for m in crowd if m != "Alex" and "Alex" in m)          # naive rule: join the first longer name that contains it
print("first-match rule : 'Alex' joins", first_match, "<- silently guessed")  # a false merge: it may be the other Alex
crowd_result = resolve_entities([emp(n) for n in crowd])                     # the real function on the same three names
print("real function    :", [n.key["name"] for n in crowd_result.nodes])     # nothing merged
print("reported doubt   :", crowd_result.ambiguous[0].mention, "fits", crowd_result.ambiguous[0].candidates)  # the doubt is said out loud
