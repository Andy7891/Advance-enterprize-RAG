# HTTP content negotiation: the client lists encodings it can decode, the server picks one.
import httpx

URL = "http://localhost:6333/collections/chunks/points/scroll"
body = {"limit": 50, "with_payload": True, "with_vector": False}

print("httpx default Accept-Encoding here:", httpx.Client().headers["accept-encoding"])
for accept in ("identity", "gzip", "zstd", "gzip, deflate, zstd"):
    with httpx.Client() as client:
        with client.stream("POST", URL, json=body, headers={"Accept-Encoding": accept}) as response:
            wire = b"".join(response.iter_raw())            # bytes as sent over the network
        decoded = client.post(URL, json=body, headers={"Accept-Encoding": accept}).content   # after decoding
    print(f"Accept-Encoding: {accept:<20} -> server chose {response.headers.get('content-encoding', 'none'):<5}"
          f" | on the wire {len(wire):>6} bytes | decoded {len(decoded):>6} bytes")

from src.vectorstore.setup import build_qdrant_client

seen = []
qdrant = build_qdrant_client("http://localhost:6333")
qdrant._client.openapi_client.client._client.event_hooks["response"].append(
    lambda r: seen.append((r.request.headers["accept-encoding"], r.headers.get("content-encoding"))))
qdrant.count("chunks")
print("build_qdrant_client request/response encodings:", seen)
