# API Usage Examples

Every example on this page was run for real against a live `uvicorn src.api.main:app` instance,
with real Qdrant, Neo4j, and Ollama running (`docker compose up -d qdrant neo4j ollama`) — nothing
here is hand-written or assumed. For the concept-by-concept *why* behind this API's own design
(streaming, validation, error handling, logging), see
[docs/35](<35 chat_streaming_deep_dive.md>), [docs/36](<36 error_handling_deep_dive.md>), and
[docs/37](<37 logging_observability_deep_dive.md>). For the full, machine-readable contract, see
the interactive docs below — this page is a companion to them, not a replacement.

## Prerequisites

```bash
docker compose up -d qdrant neo4j ollama
.venv/Scripts/python -m uvicorn src.api.main:app --port 8000
```

## Interactive documentation

FastAPI generates two interactive explorers automatically from the same schema:

- **Swagger UI**: <http://localhost:8000/docs> — browse every endpoint, expand a request/response
  shape, and use "Try it out" to send a real request from the browser (`ChatRequest.query` comes
  prefilled with a real, reliable example question).
- **ReDoc**: <http://localhost:8000/redoc> — a read-only, single-page view of the same schema,
  often easier to skim for the full shape of every request/response at once.
- **Raw schema**: <http://localhost:8000/openapi.json> — the underlying OpenAPI 3.1 document both
  of the above render.

## `GET /health`

A liveness check that also reports which backing services this running instance is configured
for. Takes no parameters, never fails on its own (no real failure path exists for it). Only
`GET` is accepted: a `HEAD` probe (some load balancers use one) gets a `405` with `Allow: GET`.

```bash
curl -s http://localhost:8000/health
```

Real output:
```json
{"status":"ok","qdrant_url":"http://localhost:6333","neo4j_uri":"bolt://localhost:7687","ollama_url":"http://localhost:11434"}
```

## `POST /chat` — the happy path

Streams a real, grounded answer to `query` as Server-Sent Events, retrieving `k` chunks (default
3) to ground it. The real wire format: one `data: <fragment>\n\n` event per generated fragment,
then a final `data: [DONE]\n\n` marker. `-N` disables curl's own output buffering, so fragments
print as they actually arrive rather than all at once at the end.

```bash
curl -s -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "Who founded Insurellm?", "k": 3}'
```

Real output (captured in full — note real fragments can split mid-word, e.g. `Lanc`/`aster`,
since Ollama streams by raw model token, not by whole word):
```
data:  A

data: very

data:  Lanc

data: aster

data:  founded

data:  Ins

data: ure

data: ll

data: m

data:  in

data:  

data: 2

data: 0

data: 1

data: 5

data:  [1

data: ].

data: 
data: 
data: Sources:
data: [1] About Insurellm (company/about.md)
data: [2] Avery Lancaster (employees/Avery Lancaster.md)
data: [3] HR Record (employees/Alex Harper.md)

data: [DONE]
```
Reassembled, the answer reads: *"Avery Lancaster founded Insurellm in 2015 [1]."*, followed by the
mechanical source list P7-M2 appends regardless of the model's own inline citations.

**Reading the stream correctly.** An SSE client strips exactly one space after `data:` and joins the
`data:` lines of one event with `\n`; that is how the source list above (several lines, one event)
becomes a multi-line block, and why a fragment that starts with a space (` A`) arrives intact. A
line break *inside* a fragment — whether the model produced `\n`, `\r\n` or a lone `\r` — is sent
as separate `data:` lines, so all three arrive as one `\n` and **no `\r` ever appears on the wire**.
(Before P7-M7 a lone `\r` was sent as is, and a spec-compliant client read the text after it as a
new SSE *field*; see [docs/35](<35 chat_streaming_deep_dive.md>)'s correction note.) The complete
rules, each with the check that enforces it, are in [docs/api-contract.md](api-contract.md).

**A real, honest note on latency:** this project's own RAG pipeline runs a local, CPU-only model
(Mistral-7B-Q4), and its real generation latency has been measured repeatedly across this project
at 108-300+ real seconds for a full answer, with a documented ~37.5% real timeout rate on this
specific dev host (`notes/step-6.7-integration-checkpoint.md`). This page's own FIRST real attempt
at the exact example above hit that ceiling and returned `data: [ERROR] timed out` instead of an
answer — reproduced faithfully in the next section rather than quietly discarded. The output shown
above is a second, real attempt that completed successfully; both are genuine, unedited captures
of the same request against the same host. A slow or timed-out response here is expected, disclosed
behavior on this hardware, not a bug in the request itself — see
[docs/35](<35 chat_streaming_deep_dive.md>)/[docs/36](<36 error_handling_deep_dive.md>) for why a
mid-stream failure becomes an in-band SSE event rather than an HTTP error status.

## `POST /chat` — request validation errors

### 400 — the request body isn't valid JSON at all

```bash
curl -s -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{bad json'
```

Real output:
```
HTTP/1.1 400 Bad Request
content-type: application/json

{"error":{"code":"invalid_json","message":"the request body is not valid JSON"}}
```

### 422 — the body is valid JSON, but fails a field's own rule

```bash
curl -s -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"k": 0}'
```

Real output:
```
HTTP/1.1 422 Unprocessable Entity
content-type: application/json

{"error":{"code":"validation_error","message":"body.query: Field required; body.k: Input should be greater than 0"}}
```

Every field-level problem is joined into one message — this example has two (a missing `query`
*and* a `k` that isn't greater than 0), both named.

### 422 — a blank query is rejected exactly like an empty one

Whitespace-only text counts as empty: surrounding whitespace is trimmed *before* the length rule
is checked, so `"   "` fails the same way `""` does. (Before P7-M6 it slipped through as a `200`
stream that opened and then carried `data: [ERROR] question must be a non-empty string, ...`.)

```bash
curl -s -i -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "   "}'
```

Real output:
```
HTTP/1.1 422 Unprocessable Entity
content-type: application/json

{"error":{"code":"validation_error","message":"body.query: String should have at least 1 character"}}
```

## Errors the framework raises: `404` and `405`

A path the app never declared, or a declared path called with the wrong method, is answered by
the web framework's own routing before any of this app's code runs. Both use the same envelope as
every other error, and a `405` keeps its `Allow` header, naming the method that *is* accepted.

```bash
curl -s -i http://localhost:8000/nonexistent
curl -s -i http://localhost:8000/chat          # GET on a POST-only route
```

Real output:
```
HTTP/1.1 404 Not Found
content-type: application/json

{"error":{"code":"not_found","message":"Not Found"}}

HTTP/1.1 405 Method Not Allowed
allow: POST
content-type: application/json

{"error":{"code":"method_not_allowed","message":"Method Not Allowed"}}
```

## `POST /chat` — the mid-stream `[ERROR]` event

Once a `/chat` request's `200` response has started streaming, HTTP headers are already committed
— a real failure partway through (a generation timeout, a backing service going down) can no
longer become a different HTTP status code. Instead, it's reported as the stream's own final
event, `data: [ERROR] <message>\n\n`, still inside the same `200` response. Captured for real on
this page's own first attempt at the happy-path example above, on this exact host:

```
data: [ERROR] timed out
```

This is a real, working example of the design [docs/35](<35 chat_streaming_deep_dive.md>) C5 and
[docs/36](<36 error_handling_deep_dive.md>) C2/C4 explain in full — the failure reaches the client
as readable text instead of a silently reset connection, but it is **not** an HTTP 500 or 504; the
client still receives a normal `200` status.

## `POST /chat` — 500 and 504 (documented, not reachable from `/chat` itself)

This API's global error handlers (`src/api/main.py`) *do* produce a structured `500` (any
unexpected, unhandled error) and `504` (a backing-service call that times out *before* any
response has started) — real, tested code, covered by
[`tests/test_errors.py`](../tests/test_errors.py). But `/chat` itself can never actually trigger
either one: every real failure in `/chat`'s own work (query embedding, retrieval, generation)
happens *after* its `200` response has already started (the previous section's own `[ERROR]`
event is what `/chat` produces instead). The two examples below were captured against a
temporary, debug-only route added *solely* to demonstrate these two real, documented — but from
`/chat`, unreachable — response shapes:

```python
@app.get("/_debug/raises-500")
def _raises_500():
    raise RuntimeError("a genuinely unexpected bug")

@app.get("/_debug/raises-504")
def _raises_504():
    raise TimeoutError("a backing service call took too long")
```

Real output:
```
--- 500 ---
HTTP 500
{"error":{"code":"internal_error","message":"an unexpected error occurred"}}

--- 504 ---
HTTP 504
{"error":{"code":"upstream_timeout","message":"a backing service call took too long"}}
```

The `500` response deliberately hides the real exception's own message from the caller (logged
server-side instead, via `logger.exception` — see [docs/37](<37 logging_observability_deep_dive.md>))
so an internal bug never leaks implementation details over the wire.

## Calling the API from a browser

Verified in a real browser (the built-in browser of the Claude desktop app, driving
[`web/frontend_sim.html`](<../practice codes/phase 7/m7_api_contract_checkpoint/web/frontend_sim.html>)):

- **`EventSource` cannot be used.** It only makes `GET` requests and `/chat` is `POST`:
  `new EventSource('/chat')` fires `error` at once and ends with `readyState 2` (closed). Use
  `fetch` and read `response.body` as a stream, parsing SSE yourself (the page does, in about 25
  lines) or with a library such as [`@microsoft/fetch-event-source`](https://github.com/Azure/fetch-event-source).
  ```js
  const response = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
    body: JSON.stringify({ query, k: 3 }),
    signal: controller.signal,                    // lets a "Stop generating" button abort the read
  });
  const reader = response.body.getReader(), decoder = new TextDecoder("utf-8");
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    parser(decoder.decode(value, { stream: true })); // an SSE parser: split on \r\n | \r | \n
  }
  ```
- **A page served from another origin is blocked.** The API sends no CORS headers (a deliberate
  decision, [decision.md](../decision.md) D-2026-10-03-15), so the browser refuses to let such a page read
  any response: `Access to fetch at '…/health' from origin '…' has been blocked by CORS policy: No
  'Access-Control-Allow-Origin' header is present`, and a JSON `POST` dies at the preflight
  (`OPTIONS /chat` is a `405`). Serve the page from the same origin (a reverse proxy), call the API from a
  server, or add CORS deliberately — which changes clause X2 of the contract.
- **"Stop generating" works from the client's side:** `controller.abort()` rejects the read with
  `AbortError`, the server stops asking the model for more fragments, and the API stays usable for the
  next question. What it does *not* yet do promptly is release the connection to the model server
  (known gap G1 in the contract).
- **Expect long silences.** A real answer takes minutes on this CPU-only host, and nothing is sent
  before the first fragment (no keep-alive comments), so any proxy or client timeout shorter than that
  will cut the stream.

## Every response's shared error shape

Every error this API returns — `400`, `404`, `405`, `422`, `500`, `504` — shares one envelope:
```json
{"error": {"code": "<short machine-readable slug>", "message": "<human-readable text>"}}
```
`code` is stable and meant to be branched on programmatically (`invalid_json`, `validation_error`,
`not_found`, `method_not_allowed`,
`internal_error`, `upstream_timeout`, and a generic `http_error` for any other HTTP status);
`message` is for a human reading logs or a UI.

`/openapi.json` declares this envelope as the `ErrorResponse` schema (with `error.code` and
`error.message` required) for `/chat`'s `400`, `422`, `500` and `504`, each with a real example, and
a test validates every observed error body against that declared schema. `404` and `405` are raised
by routing for every path, so — as is conventional — they are not declared per route.
