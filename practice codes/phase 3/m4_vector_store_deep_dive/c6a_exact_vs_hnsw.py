# Exact vs approximate search on the LIVE server.
# Copies the 223 stored points into a scratch collection whose thresholds force an HNSW index,
# then compares approximate results with exact ones. The scratch collection is deleted at the end.
import json
import time
import urllib.request

from qdrant_client.models import (Distance, HnswConfigDiff, OptimizersConfigDiff, PointStruct, SearchParams,
                                  VectorParams)

from src.vectorstore.setup import build_qdrant_client

URL = "http://localhost:6333"
client = build_qdrant_client(URL)
real = json.load(urllib.request.urlopen(f"{URL}/collections/chunks"))["result"]
print("1. real 'chunks': points", real["points_count"], "| indexed_vectors", real["indexed_vectors_count"],
      "| full_scan_threshold", real["config"]["hnsw_config"]["full_scan_threshold"], "KB",
      "| indexing_threshold", real["config"]["optimizer_config"]["indexing_threshold"], "KB")
print("   vector data size ~", round(real["points_count"] * 768 * 4 / 1024), "KB -> below both thresholds -> exact scan")

points, _ = client.scroll("chunks", limit=500, with_payload=["chunk_id"], with_vectors=True)
scratch = "dd14_hnsw_scratch"
if client.collection_exists(scratch):
    client.delete_collection(scratch)
client.create_collection(
    scratch,
    vectors_config=VectorParams(size=768, distance=Distance.COSINE),
    # m=4 and ef_construct=8 build a deliberately SPARSE graph, so approximation errors are visible
    hnsw_config=HnswConfigDiff(m=4, ef_construct=8, full_scan_threshold=10),   # server minimum is 10 KB; still far below 669 KB
    optimizers_config=OptimizersConfigDiff(indexing_threshold=10),
)
client.upsert(scratch, wait=True, points=[PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points])

for _ in range(60):                                   # the optimizer builds HNSW in the background
    info = client.get_collection(scratch)
    if info.indexed_vectors_count and info.indexed_vectors_count >= len(points) and str(info.status) .endswith("green"):
        break
    time.sleep(1)
print("2. scratch: points", info.points_count, "| indexed_vectors", info.indexed_vectors_count, "| status", info.status)

total_hits = total_possible = 0
for probe in points[::20]:                            # 12 stored vectors used as queries
    exact = client.query_points(scratch, query=probe.vector, limit=10, search_params=SearchParams(exact=True)).points
    approx = client.query_points(scratch, query=probe.vector, limit=10, search_params=SearchParams(hnsw_ef=10)).points
    overlap = len({p.id for p in exact} & {p.id for p in approx})
    total_hits += overlap
    total_possible += 10
print(f"3. recall@10 of HNSW (m=4, ef=10) vs exact over 12 queries: {total_hits}/{total_possible} = {total_hits / total_possible:.2f}")

total_hits = 0
for probe in points[::20]:
    exact = client.query_points(scratch, query=probe.vector, limit=10, search_params=SearchParams(exact=True)).points
    approx = client.query_points(scratch, query=probe.vector, limit=10, search_params=SearchParams(hnsw_ef=200)).points
    total_hits += len({p.id for p in exact} & {p.id for p in approx})
print(f"4. same graph, hnsw_ef=200 (search wider)            : {total_hits}/{total_possible} = {total_hits / total_possible:.2f}")

client.delete_collection(scratch)
print("5. scratch collection deleted:", not client.collection_exists(scratch))
