# Minimal demo of C2: a function can compute a number internally and still never GIVE
# it back to you. When you can't change the function, calling it at each depth and
# diffing consecutive results recovers the missing number from the outside.
from collections import deque

edges = [("Alice", "Bob"), ("Bob", "Carol"), ("Carol", "Dave")]  # a straight chain, 3 hops long
neighbours = {}
for a, b in edges:
    neighbours.setdefault(a, []).append(b)
    neighbours.setdefault(b, []).append(a)


def names_within(start, hops):
    # THIS is deliberately shaped like multi_hop_search: it computes a real distance
    # internally (the BFS naturally knows how far each node is) but only RETURNS names.
    visited = {start: 0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if visited[node] >= hops:
            continue
        for neighbour in neighbours.get(node, []):
            if neighbour not in visited:
                visited[neighbour] = visited[node] + 1
                queue.append(neighbour)
    return sorted(n for n in visited if n != start)  # <- distance thrown away here on purpose


print("names_within('Alice', hops=1):", names_within("Alice", 1))
print("names_within('Alice', hops=2):", names_within("Alice", 2))
print("names_within('Alice', hops=3):", names_within("Alice", 3))

print("\nRecovering each name's actual distance by diffing consecutive calls:")
previous = set()
for hop in range(1, 4):
    within_hop = set(names_within("Alice", hop))
    new_at_this_hop = within_hop - previous
    print(f"  hop {hop}: newly reached = {sorted(new_at_this_hop)}")
    previous = within_hop
# this loop is EXACTLY GraphRetriever._entities_by_hop's own technique, on a toy chain
