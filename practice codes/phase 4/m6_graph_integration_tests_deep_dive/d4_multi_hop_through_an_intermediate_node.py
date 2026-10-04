# Minimal demo of C5: two entities can be CONNECTED with no DIRECT edge between them --
# only a path through something in between. A tiny hand-rolled graph + breadth-first
# search stands in for GraphStore.multi_hop_search, so the idea is visible with no
# database at all.
from collections import deque

# The SAME shape this project's real graph has: Client -> Contract -> Product. No edge
# ever goes straight from Client to Product.
edges = [
    ("Client", "Contract"),  # WITH_CLIENT, direction doesn't matter for this demo
    ("Contract", "Product"),  # FOR_PRODUCT
]

neighbours = {}  # build an undirected adjacency list: each node's list of directly-linked nodes
for a, b in edges:
    neighbours.setdefault(a, []).append(b)  # a can reach b directly
    neighbours.setdefault(b, []).append(a)  # and b can reach a directly (undirected)


def hops_away(start, target):
    # A minimal breadth-first search: explore one hop at a time, remembering how many
    # hops it took to reach each node, and stop as soon as `target` is found.
    visited = {start: 0}  # every node found so far, with its distance from `start`
    queue = deque([start])  # nodes waiting to have their own neighbours explored
    while queue:
        node = queue.popleft()  # take the next node to explore
        if node == target:
            return visited[node]  # found it -- return how many hops it took
        for neighbour in neighbours.get(node, []):  # look at everything directly linked to `node`
            if neighbour not in visited:  # only visit each node once
                visited[neighbour] = visited[node] + 1  # one hop further than `node` was
                queue.append(neighbour)  # explore ITS neighbours later
    return None  # never reached


print("Is there a DIRECT edge from Client to Product?", ("Client", "Product") in edges)
print("Hops from Client to Contract:", hops_away("Client", "Contract"))  # 1: a direct edge exists
print("Hops from Client to Product: ", hops_away("Client", "Product"))  # 2: only through Contract
