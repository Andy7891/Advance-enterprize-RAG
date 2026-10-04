# Minimal demo of C3: at c=60 two middling ranks beat one great rank, and weights shift that.
def rrf(vector_rank, graph_rank, c=60, wv=1.0, wg=1.0):
    total = 0.0  # starts the running score at zero
    if vector_rank is not None:  # only counts the vector list if it ranked the chunk
        total += wv / (c + vector_rank)  # adds the weighted reciprocal of the vector rank
    if graph_rank is not None:  # only counts the graph list if it ranked the chunk
        total += wg / (c + graph_rank)  # adds the weighted reciprocal of the graph rank
    return total  # hands back the fused score


print("TechDrive contract (vector rank 9, graph rank 3) vs a chunk ranked #1 by one list only:")
for c in (1, 60, 1000):  # a top-heavy constant, the real one, and a huge one
    both = rrf(9, 3, c=c)  # the chunk both lists rank mediocrely
    one = rrf(1, None, c=c)  # the chunk only one list ranks, but ranks first
    print(f"  c={c:<5} both-lists={both:.5f}  one-list-#1={one:.5f}  agreement wins={both > one}")

print("\nWeights: vector-only #1 versus graph-only #1 (only the RATIO matters):")
for wv, wg in ((1, 1), (2, 1), (1, 2), (0.5, 1)):  # four weight settings; (1,2) equals (0.5,1)
    v = rrf(1, None, wv=wv, wg=wg)  # the vector-only chunk score
    g = rrf(None, 1, wv=wv, wg=wg)  # the graph-only chunk score
    winner = "vector" if v > g else "graph" if g > v else "tie"  # who is ahead
    print(f"  weights=({wv},{wg})  vector-only={v:.5f}  graph-only={g:.5f}  ahead: {winner}")
