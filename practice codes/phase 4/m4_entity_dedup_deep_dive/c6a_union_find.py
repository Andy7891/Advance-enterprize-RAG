# C6a: union-find -- joining groups and asking "which group is this?". No database needed.
def make(n):                                                                 # a fresh structure for n items
    return list(range(n))                                                    # parent[i] = i means "item i is its own group's leader"


def find(parent, i):                                                         # follow parents up to the group's leader
    while parent[i] != i:                                                    # a leader is its own parent
        parent[i] = parent[parent[i]]                                        # shortcut: point at the grandparent to make later finds faster
        i = parent[i]                                                        # climb one step
    return i                                                                 # the leader identifies the group


def union(parent, a, b):                                                     # join the group of a with the group of b
    parent[find(parent, a)] = find(parent, b)                                # make one leader point at the other


def groups(parent):                                                          # collect items by leader
    out = {}                                                                 # leader -> members
    for i in range(len(parent)):                                             # every item
        out.setdefault(find(parent, i), []).append(i)                        # file it under its leader
    return sorted(out.values())                                              # a canonical, comparable view


joins = [(0, 1), (1, 2), (3, 4)]                                             # "0 goes with 1", "1 goes with 2", "3 goes with 4"
forward, backward = make(5), make(5)                                         # two fresh structures
for a, b in joins:                                                           # apply the joins in the given order
    union(forward, a, b)                                                     # forward order
for a, b in reversed(joins):                                                 # apply the SAME joins in the opposite order
    union(backward, a, b)                                                    # backward order
print("forward groups :", groups(forward))                                   # [[0, 1, 2], [3, 4]]
print("backward groups:", groups(backward))                                  # identical: the order of joins does not matter
print("parent list    :", forward)                                           # the raw structure: leaders point at themselves
