# A Qdrant Filter is boolean logic over payload fields: must (AND), should (OR), must_not (NOT).
from qdrant_client import QdrantClient
from qdrant_client.models import (Distance, FieldCondition, Filter, MatchAny, MatchValue, PointStruct,
                                  Range, VectorParams)

client = QdrantClient(":memory:")
client.create_collection("demo", vectors_config=VectorParams(size=2, distance=Distance.COSINE))
rows = [
    (1, "company", "doc-about", 0), (2, "company", "doc-about", 1), (3, "products", "doc-carllm", 0),
    (4, "products", "doc-carllm", 1), (5, "contracts", "doc-techdrive", 0),
]
client.upsert("demo", wait=True, points=[
    PointStruct(id=i, vector=[1.0, 0.0], payload={"category": c, "doc_id": d, "chunk_index": k})
    for i, c, d, k in rows
])


def ids(f):
    points, _ = client.scroll("demo", scroll_filter=f, limit=100)
    return sorted(p.id for p in points)


eq = lambda key, value: FieldCondition(key=key, match=MatchValue(value=value))
print("must  doc_id == doc-about                  ->", ids(Filter(must=[eq("doc_id", "doc-about")])))
print("must  category == products AND index == 0  ->", ids(Filter(must=[eq("category", "products"), eq("chunk_index", 0)])))
print("should category company OR contracts       ->", ids(Filter(should=[eq("category", "company"), eq("category", "contracts")])))
print("match any of [company, contracts]          ->", ids(Filter(must=[FieldCondition(key="category", match=MatchAny(any=["company", "contracts"]))])))
print("must_not category == products              ->", ids(Filter(must_not=[eq("category", "products")])))
print("range chunk_index >= 1                     ->", ids(Filter(must=[FieldCondition(key="chunk_index", range=Range(gte=1))])))
print("typo'd key 'docid' (no such field)         ->", ids(Filter(must=[eq("docid", "doc-about")])), "<- matches nothing, no error")
