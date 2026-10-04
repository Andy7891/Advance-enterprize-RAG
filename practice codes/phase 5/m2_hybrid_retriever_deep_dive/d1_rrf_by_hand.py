# Minimal demo of C2: Reciprocal Rank Fusion by hand, then checked against the REAL fuse_rankings.
import sys  # gives access to the module search path
from types import SimpleNamespace  # builds tiny stand-in objects with named attributes

sys.path.insert(0, ".")  # lets Python import src.* when run from the project root
from src.retrieval.hybrid_retriever import fuse_rankings  # the real production function


def chunk(name):
    return SimpleNamespace(chunk_id=name)  # a stand-in chunk: fuse_rankings only reads chunk_id


def scored(name, score):
    return SimpleNamespace(chunk=chunk(name), score=score)  # a stand-in vector hit: chunk + cosine


vector_hits = [scored("A", 0.90), scored("B", 0.80), scored("C", 0.70)]  # vector ranks 1, 2, 3
graph_hits = [chunk("C"), chunk("D")]  # graph ranks 1, 2 (no scores at all)
c = 60  # the smoothing constant

by_hand = {  # each chunk score worked out with weight / (c + rank), both weights 1
    "A": 1 / (c + 1),  # only in the vector list, at rank 1
    "B": 1 / (c + 2),  # only in the vector list, at rank 2
    "C": 1 / (c + 3) + 1 / (c + 1),  # vector rank 3 PLUS graph rank 1: two contributions
    "D": 1 / (c + 2),  # only in the graph list, at rank 2
}
print("by hand:")  # heading for the hand-computed scores
for name, score in sorted(by_hand.items(), key=lambda pair: -pair[1]):  # best first
    print(f"  {name}  {score:.5f}")  # one line per chunk

print("real fuse_rankings:")  # heading for the production function answer
for item in fuse_rankings(vector_hits, graph_hits, vector_weight=1.0, graph_weight=1.0):
    print(f"  {item.chunk.chunk_id}  {item.score:.5f}  vec_rank={item.vector_rank}  graph_rank={item.graph_rank}")
# C wins although neither list ranked it first; B and D tie, and the vector rank breaks the tie
