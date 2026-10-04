"""Verifies the API contract the way a frontend would use it, one clause at a time.

Each clause is written down in docs/api-contract.md under the same id and prints PASS, FAIL, SKIP
or (for a documented, not-yet-fixed gap) GAP, with its evidence. Two tiers, because "what does the
model say" is not part of the contract:

  python check_api_contract.py --tier deterministic
      starts the REAL app with a scripted stub model (stub_server.py) on a free port, so every
      result is exact and repeatable, then stops it again
  python check_api_contract.py --tier real --base-url http://127.0.0.1:8000
      checks an already-running REAL stack (Qdrant + Neo4j + Ollama behind it). One real answer
      takes minutes on this CPU-only host, so it asks for ONE and shares it between the clauses
      that need a stream; only the clauses that do not depend on what the model says run, and a
      generation that hits the host's timeout is tolerated (and reported), nothing else is

Exit code 0 when no clause FAILed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jsonschema
from httpx_sse import EventSource
from scenarios import SCENARIOS, marker

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
# .../<repo>/practice codes/phase 7/m7_api_contract_checkpoint -> three levels up is the repo root

REAL_BODY = {"query": "Who founded Insurellm?", "k": 3}
# the project's own documented, reliably-retrieving question (notes/step-6.6), also the one
# /docs pre-fills in "Try it out"
LINE_END = re.compile(r"\r\n|\r|\n")
# the three line terminators the SSE grammar defines, CRLF first so it counts as one


# --- SSE parsing: two independent implementations ---------------------------------------------


def parse_reference(text: str) -> list[tuple[str, str]]:
    """(event type, data) pairs, from the WHATWG "event stream interpretation" algorithm.

    Written from the specification, not from this server's framing. `id` and `retry` are ignored
    (the server only sends `data:` lines; clause S2 asserts that separately).
    """
    events: list[tuple[str, str]] = []
    data_lines: list[str] = []
    event_type = ""
    for line in LINE_END.split(text)[:-1]:
        # [:-1] drops what follows the last line end: an empty string or an unterminated partial
        # line, neither of which can complete an event
        if line == "":
            if data_lines:
                events.append((event_type or "message", "\n".join(data_lines)))
            data_lines, event_type = [], ""
        elif line.startswith(":"):
            continue
        else:
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "data":
                data_lines.append(value)
            elif field == "event":
                event_type = value
    return events


def parse_with_httpx_sse(raw: bytes) -> list[tuple[str, str]]:
    """The same events as a third-party SSE library reads them, from the same captured bytes."""
    response = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw)
    # a response object around bytes already captured, so one (slow) request feeds both parsers
    return [(sse.event, sse.data) for sse in EventSource(response).iter_sse()]


@dataclass
class Capture:
    """One complete (or deliberately abandoned) /chat response, with when each event arrived."""

    status: int
    headers: httpx.Headers
    raw: bytes
    text: str
    events: list[tuple[str, str]]
    event_times: list[float]
    # seconds after the request was sent at which the Nth event became complete
    total: float


def capture_stream(
    client: httpx.Client,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    stop_after: int | None = None,
) -> Capture:
    """POSTs `body` to /chat and reads the response as it arrives, like a frontend would."""
    started = time.perf_counter()
    decoder = codecs.getincrementaldecoder("utf-8")()
    raw = bytearray()
    text = ""
    event_times: list[float] = []
    with client.stream("POST", "/chat", json=body, headers=headers) as response:
        status, response_headers = response.status_code, response.headers
        for chunk in response.iter_raw():
            raw += chunk
            text += decoder.decode(chunk)
            complete = len(parse_reference(text))
            while len(event_times) < complete:
                event_times.append(time.perf_counter() - started)
            if stop_after is not None and complete >= stop_after:
                break
                # leaving the `with` block closes the connection: the client walked away
    total = time.perf_counter() - started
    return Capture(
        status, response_headers, bytes(raw), text, parse_reference(text), event_times, total
    )


def terminal_of(capture: Capture) -> tuple[str, str]:
    """("done", "") or ("error", message) for the stream's last event, else ("none", data)."""
    data = capture.events[-1][1] if capture.events else ""
    if data == "[DONE]":
        return "done", ""
    if data.startswith("[ERROR]"):
        return "error", data.removeprefix("[ERROR]").strip()
    return "none", data


def parse_sources(data: str) -> list[tuple[int, str, str]]:
    """(number, title, path) for every entry of the source list the stream ends with."""
    expect(data.startswith("\n\nSources:\n"), f"not a source list: {ascii(data[:60])}")
    entries = []
    for line in data.removeprefix("\n\nSources:\n").split("\n"):
        match = re.fullmatch(r"\[(\d+)\] (.+) \((.+)\)", line)
        expect(match is not None, f"source line does not look like '[n] title (path)': {line!r}")
        assert match is not None
        entries.append((int(match.group(1)), match.group(2), match.group(3)))
    return entries


# --- the clause framework -----------------------------------------------------------------------


class Fail(Exception):
    """A clause's promise was broken."""


class Skip(Exception):
    """A clause cannot be judged here, and says why."""


def expect(condition: object, message: str) -> None:
    if not condition:
        raise Fail(message)


@dataclass
class Clause:
    cid: str
    title: str
    tiers: tuple[str, ...]
    run: Callable[[Ctx], str]
    known_gap: str | None
    # when set, this clause states the DESIRED behaviour, which the API does not have yet


CLAUSES: list[Clause] = []


def clause(
    cid: str,
    title: str,
    tiers: tuple[str, ...] = ("deterministic", "real"),
    known_gap: str | None = None,
):
    """Registers a function as clause `cid`; it returns its evidence, or raises Fail / Skip.

    A clause with `known_gap` is this checker's strict xfail: while it fails the verdict is GAP
    (documented, tolerated, exit code 0); the day it passes the verdict becomes FAIL, because a
    gap that is gone must be removed from docs/api-contract.md on purpose, not by accident.
    """

    def register(function: Callable[[Ctx], str]) -> Callable[[Ctx], str]:
        CLAUSES.append(Clause(cid, title, tiers, function, known_gap))
        return function

    return register


class Ctx:
    """Everything a clause needs: the target, the OpenAPI document, shared captures."""

    def __init__(self, base_url: str, tier: str, yield_log: Path | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.tier = tier
        self.yield_log = yield_log
        read_timeout = 900.0 if tier == "real" else 30.0
        # a real answer can go minutes between bytes on a CPU-only host; the stub never does
        self.http = httpx.Client(
            base_url=self.base_url, timeout=httpx.Timeout(30.0, read=read_timeout)
        )
        self.observed: list[tuple[str, str, int]] = []
        # (method, path, status) of every response that matched its declared contract
        self.declared_failures: list[str] = []
        self._spec: dict[str, Any] | None = None
        self._streams: dict[str, Capture] = {}
        self._abandon: tuple[list[float], list[list[str]], float] | None = None

    @property
    def spec(self) -> dict[str, Any]:
        if self._spec is None:
            self._spec = self.http.get("/openapi.json").json()
        return self._spec

    def body(self, scenario: str = "plain", k: int = 3) -> dict[str, Any]:
        """The JSON a frontend would POST: the real question, or a stub scenario's marker."""
        if self.tier == "real":
            return dict(REAL_BODY)
        return {"query": f"{marker(scenario)} Who founded Insurellm?", "k": k}

    def stream(self, key: str, body: dict[str, Any]) -> Capture:
        """The /chat response for `body`, fetched once per key and shared between clauses."""
        if key not in self._streams:
            self._streams[key] = capture_stream(
                self.http, body, headers={"Accept": "text/event-stream"}
            )
        return self._streams[key]

    def abandon(self) -> tuple[list[float], list[list[str]], float]:
        """Walks away from a slow stream after 3 events; returns the stub model's yield times,
        its log rows, and when the client left. Done once and shared by S9 and S12."""
        if self.yield_log is None:
            raise Skip("no yield log: start the stub with STUB_YIELD_LOG and pass --yield-log")
        if self._abandon is None:
            self.yield_log.write_text("", encoding="utf-8")
            capture_stream(self.http, self.body("slow"), stop_after=3)
            left = time.time()
            time.sleep(3.0)
            # long enough for any further fragments, or a close, to show up in the log
            rows = [line.split() for line in self.yield_log.read_text(encoding="utf-8").splitlines()]
            yields = [float(row[0]) for row in rows if row[1] == "yielded"]
            self._abandon = (yields, rows, left)
        return self._abandon


def check_declared(
    ctx: Ctx, method: str, path: str, status: int, content_type: str, body: bytes
) -> str:
    """The declared-vs-observed check: /openapi.json promises a status, a media type and a body
    schema for this response; raises Fail unless the observed response keeps every promise."""
    try:
        declared = ctx.spec["paths"][path][method.lower()]["responses"].get(str(status))
        expect(declared is not None, f"{status} is not declared for {method} {path}")
        media = content_type.split(";")[0]
        content = declared.get("content", {})
        expect(media in content, f"{media} is not a declared media type (declared: {list(content)})")
        note = f"{status} {media} is declared"
        if media == "application/json":
            schema = content[media].get("schema")
            expect(schema is not None, "declared with an example but no schema")
            instance = json.loads(body)
            try:
                jsonschema.validate(instance, {**schema, "components": ctx.spec["components"]})
            except jsonschema.ValidationError as invalid:
                raise Fail(f"the body breaks the declared schema: {invalid.message}") from invalid
            ref = schema.get("$ref", "")
            resolved = ctx.spec["components"]["schemas"][ref.rsplit("/", 1)[-1]] if ref else schema
            required = resolved.get("required")
            expect(required, "the schema has no required property, so it accepts any object")
            missing = [name for name in required if name not in instance]
            expect(not missing, f"required properties missing from the body: {missing}")
            note += f" and the body matches {ref or 'its inline schema'}"
    except Fail as failure:
        ctx.declared_failures.append(f"{method} {path} {status}: {failure}")
        raise
    ctx.observed.append((method, path, status))
    return note


def check_envelope(response: httpx.Response, code: str) -> str:
    """Requires the one error shape every failure uses; returns the message."""
    body = response.json()
    expect(set(body) == {"error"}, f"top-level keys are {sorted(body)}, expected ['error']")
    expect(set(body["error"]) == {"code", "message"}, f"error keys: {sorted(body['error'])}")
    expect(body["error"]["code"] == code, f"code is {body['error']['code']!r}, expected {code!r}")
    expect(
        isinstance(body["error"]["message"], str) and body["error"]["message"],
        "the message is empty",
    )
    return body["error"]["message"]


# --- A: discovery -------------------------------------------------------------------------------


@clause("A1", "GET /health answers 200 JSON that matches its declared HealthResponse")
def a1(ctx: Ctx) -> str:
    response = ctx.http.get("/health")
    expect(response.status_code == 200, f"status {response.status_code}")
    note = check_declared(
        ctx, "GET", "/health", 200, response.headers["content-type"], response.content
    )
    body = response.json()
    expect(body["status"] == "ok", f"status field is {body['status']!r}")
    return f"{note}; reports qdrant={body['qdrant_url']} ollama={body['ollama_url']}"


@clause("A2", "/openapi.json describes both routes; every declared JSON example fits its schema")
def a2(ctx: Ctx) -> str:
    response = ctx.http.get("/openapi.json")
    expect(response.status_code == 200, f"status {response.status_code}")
    spec = ctx.spec
    expect(str(spec["openapi"]).startswith("3."), f"openapi version {spec['openapi']}")
    expect("get" in spec["paths"].get("/health", {}), "GET /health is not described")
    expect("post" in spec["paths"].get("/chat", {}), "POST /chat is not described")
    responses = spec["paths"]["/chat"]["post"]["responses"]
    expect(set(responses) == {"200", "400", "422", "500", "504"}, f"statuses: {sorted(responses)}")
    expect("text/event-stream" in responses["200"]["content"], "200 is not an event stream")
    expect("application/json" not in responses["200"]["content"], "200 also claims JSON")
    checked = []
    for status, declared in responses.items():
        media = declared.get("content", {}).get("application/json")
        if media is None:
            continue
        expect("schema" in media and "example" in media, f"{status}: needs a schema AND example")
        jsonschema.validate(media["example"], {**media["schema"], "components": spec["components"]})
        checked.append(status)
    return f"{spec['info']['title']} {spec['info']['version']}; examples for {checked} fit their schema"


@clause("A3", "GET /docs serves the interactive documentation")
def a3(ctx: Ctx) -> str:
    response = ctx.http.get("/docs")
    expect(response.status_code == 200, f"status {response.status_code}")
    expect("text/html" in response.headers["content-type"], response.headers["content-type"])
    return f"200 {response.headers['content-type']}, {len(response.content)} bytes"


# --- S: the streaming success path ------------------------------------------------------------


@clause("S1", "POST /chat answers 200 with a chunked, UTF-8 text/event-stream response")
def s1(ctx: Ctx) -> str:
    cap = ctx.stream("main", ctx.body("plain"))
    expect(cap.status == 200, f"status {cap.status}")
    content_type = cap.headers["content-type"]
    expect(
        content_type.lower() == "text/event-stream; charset=utf-8", f"content-type {content_type}"
    )
    expect(cap.headers.get("transfer-encoding") == "chunked", "the body is not chunked")
    expect("content-length" not in cap.headers, "a content-length means the body was buffered")
    note = check_declared(ctx, "POST", "/chat", 200, content_type, b"")
    gaps = [h for h in ("cache-control", "x-accel-buffering") if h not in cap.headers]
    return f"{note}; chunked; known gap, not asserted: no {', '.join(gaps)} header"


@clause("S2", "The stream is plain SSE: every line is `data:`, and two independent parsers agree")
def s2(ctx: Ctx) -> str:
    captures = {"main": ctx.stream("main", ctx.body("plain"))}
    if ctx.tier == "deterministic":
        captures["framing"] = ctx.stream("framing", ctx.body("framing", k=2))
        # the stream full of awkward line endings is held to the same grammar: it is the one
        # where a framing bug shows, since the ordinary stream never contains a CR to mishandle
    notes = []
    for name, cap in captures.items():
        lines = LINE_END.split(cap.text)[:-1]
        stray = [line for line in lines if line and not line.startswith("data:")]
        expect(
            not stray, f"{name}: lines that are not `data:` lines: {[ascii(s) for s in stray[:3]]}"
        )
        expect("\r" not in cap.text, f"{name}: a carriage return reached the wire")
        library = parse_with_httpx_sse(cap.raw)
        expect(
            cap.events == library,
            f"{name}: parsers disagree: reference {len(cap.events)} events, httpx-sse {len(library)}",
        )
        kinds = {kind for kind, _ in cap.events}
        expect(kinds == {"message"}, f"{name}: event types seen: {sorted(kinds)}")
        notes.append(f"{name} {len(cap.events)} events")
    return f"{'; '.join(notes)}; every non-blank line is `data:`; reference parser == httpx-sse"


@clause("S3", "Exactly one terminal event ends the stream: [DONE], or [ERROR] <message>")
def s3(ctx: Ctx) -> str:
    cap = ctx.stream("main", ctx.body("plain"))
    positions = [
        i
        for i, (_, data) in enumerate(cap.events)
        if data == "[DONE]" or data.startswith("[ERROR]")
    ]
    expect(positions == [len(cap.events) - 1], f"terminal events at {positions}")
    kind, detail = terminal_of(cap)
    if ctx.tier == "deterministic":
        expect(kind == "done", f"the scripted answer ended with {kind} {detail!r}")
        return f"{len(cap.events)} events, the last is [DONE]"
    if kind == "done":
        return (
            f"{len(cap.events)} events in {cap.total:.0f} s (the first arrived after "
            f"{cap.event_times[0]:.0f} s of silence), the last is [DONE]"
        )
    expect(
        "timed out" in detail,
        f"a failure other than the host's documented generation ceiling: {detail!r}",
    )
    # tolerates ONLY the documented generation-throughput ceiling (notes/step-6.7), exactly as
    # tests/test_api.py's live test does; anything else (Qdrant down, a bad collection) fails
    return (
        f"TOLERATED: generation hit the host ceiling after {cap.total:.0f} s "
        f"({len(cap.events)} event(s), the first after {cap.event_times[0]:.0f} s) -> [ERROR] {detail}"
    )


@clause("S4", "A completed answer ends with its numbered source list, then [DONE]")
def s4(ctx: Ctx) -> str:
    cap = ctx.stream("main", ctx.body("plain"))
    kind, detail = terminal_of(cap)
    if kind != "done":
        raise Skip(f"the stream ended with [ERROR] {detail!r}; sources follow only a full answer")
    sources = parse_sources(cap.events[-2][1])
    k = 3
    expect(1 <= len(sources) <= k, f"{len(sources)} sources for k={k}")
    expect([n for n, _, _ in sources] == list(range(1, len(sources) + 1)), "not numbered 1..n")
    return f"{len(sources)} sources: " + "; ".join(f"[{n}] {path}" for n, _, path in sources)


@clause("S5", "Events arrive one by one as generated, not buffered into one burst", ("deterministic",))
def s5(ctx: Ctx) -> str:
    cap = ctx.stream("main", ctx.body("plain"))
    scripted = SCENARIOS["plain"].delay * len(SCENARIOS["plain"].fragments)
    spread = cap.event_times[len(SCENARIOS["plain"].fragments) - 1] - cap.event_times[0]
    expect(
        spread >= 0.5 * scripted,
        f"first-to-last content event spread {spread:.2f} s, scripted generation {scripted:.2f} s",
    )
    return (
        f"first content event at {cap.event_times[0]:.2f} s, "
        f"last at {cap.event_times[len(SCENARIOS['plain'].fragments) - 1]:.2f} s "
        f"(scripted generation {scripted:.2f} s)"
    )


@clause("S6", "Whatever line endings a fragment holds, the client rebuilds the text", ("deterministic",))
def s6(ctx: Ctx) -> str:
    cap = ctx.stream("framing", ctx.body("framing", k=2))
    expected = list(SCENARIOS["framing"].expected)
    got = [data for _, data in cap.events[: len(expected)]]
    for index, (want, have) in enumerate(zip(expected, got, strict=False)):
        expect(want == have, f"fragment {index}: sent {ascii(want)}, client rebuilt {ascii(have)}")
    expect({kind for kind, _ in cap.events} == {"message"}, "a fragment forged an event type")
    expect(len(cap.events) == len(expected) + 2, f"{len(cap.events)} events: one was forged/split")
    # one event per fragment, plus the source list and [DONE] -- no more, no fewer
    expect(cap.events[-1][1] == "[DONE]", "[DONE] does not end the stream")
    return (
        f"{len(expected)} awkward fragments (CR, CRLF, LF, forged fields, U+2028) rebuilt exactly; "
        "no extra event, no forged type"
    )


@clause("S7", "A failure mid-answer keeps the 200 and ends with [ERROR], never [DONE]", ("deterministic",))
def s7(ctx: Ctx) -> str:
    cap = ctx.stream("fails", ctx.body("fails-midstream", k=2))
    expect(cap.status == 200, f"status {cap.status}: headers had already gone out")
    kind, detail = terminal_of(cap)
    expect(kind == "error", f"terminal event is {kind}")
    expect(detail == SCENARIOS["fails-midstream"].fails_with, f"message {detail!r}")
    expect(not any(data == "[DONE]" for _, data in cap.events), "[DONE] after a failure")
    contents = [data for _, data in cap.events[:-1]]
    expect(contents == list(SCENARIOS["fails-midstream"].expected), f"content before it: {contents}")
    return f"200, then {len(contents)} content events, then [ERROR] {detail}"


@clause("S8", "A question that retrieves nothing streams one refusal, then [DONE]", ("deterministic",))
def s8(ctx: Ctx) -> str:
    cap = ctx.stream("nohits", ctx.body("no-hits"))
    expect(cap.status == 200, f"status {cap.status}")
    expect(len(cap.events) == 2, f"{len(cap.events)} events: {[d[:30] for _, d in cap.events]}")
    expect(cap.events[1][1] == "[DONE]", "no [DONE]")
    expect("Sources:" not in cap.events[0][1], "a source list with nothing retrieved")
    return f"one refusal event ({ascii(cap.events[0][1][:50])}...) then [DONE]"


@clause("S9", "A client that walks away stops the server asking the model for more", ("deterministic",))
def s9(ctx: Ctx) -> str:
    yields, _, left = ctx.abandon()
    late = [t for t in yields if t > left + 0.5]
    expect(not late, f"{len(late)} fragments generated more than 0.5 s after the client left")
    return (
        f"client left after 3 events; the model produced {len(yields)} of "
        f"{len(SCENARIOS['slow'].fragments)} fragments, none more than 0.5 s after it left"
    )


@clause("S10", "A slow stream does not block other requests or other streams", ("deterministic",))
def s10(ctx: Ctx) -> str:
    results: dict[str, Capture] = {}
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        with httpx.Client(base_url=ctx.base_url, timeout=30.0) as client:
            barrier.wait()
            # both requests leave at the same moment
            results[name] = capture_stream(client, ctx.body("plain"))

    threads = [threading.Thread(target=worker, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    a, b = results["a"], results["b"]
    expect(
        b.event_times[0] < a.event_times[-1] and a.event_times[0] < b.event_times[-1],
        "the two streams did not overlap: one waited for the other to finish",
    )
    with httpx.Client(base_url=ctx.base_url, timeout=30.0) as probe, httpx.Client(
        base_url=ctx.base_url, timeout=httpx.Timeout(30.0, read=5.0)
    ) as slow_client:
        with slow_client.stream("POST", "/chat", json=ctx.body("slow-fragments")) as slow:
            try:
                next(slow.iter_raw())
            except httpx.ReadTimeout as timeout:
                raise Fail(
                    "the slow stream sent nothing for 5 s, so there was no open stream to test"
                    " against: delivery is not incremental"
                ) from timeout
            # the slow stream is now open and mid-generation, a full second between fragments
            latencies = []
            for _ in range(5):
                started = time.perf_counter()
                expect(probe.get("/health").status_code == 200, "/health failed during a stream")
                latencies.append(time.perf_counter() - started)
                time.sleep(0.1)
    expect(max(latencies) < 0.5, f"/health took {max(latencies):.2f} s while a stream ran")
    # a server that ran the generator on its event loop would hold /health back for up to the
    # full second between fragments; a healthy one answers in milliseconds
    return (
        f"two simultaneous streams overlapped (a {a.event_times[0]:.2f}-{a.event_times[-1]:.2f} s, "
        f"b {b.event_times[0]:.2f}-{b.event_times[-1]:.2f} s); /health during a stream: "
        f"max {max(latencies) * 1000:.0f} ms"
    )


@clause("S11", "k decides how many sources are cited", ("deterministic",))
def s11(ctx: Ctx) -> str:
    counts = {}
    for k in (1, 2):
        cap = ctx.stream(f"k{k}", ctx.body("plain", k=k))
        counts[k] = len(parse_sources(cap.events[-2][1]))
        expect(counts[k] == k, f"k={k} cited {counts[k]} sources")
    return f"k=1 -> {counts[1]} source, k=2 -> {counts[2]} sources"


@clause(
    "S12",
    "...and the model's stream is closed at once, releasing its connection to the model server",
    ("deterministic",),
    known_gap="G1: the abandoned generator chain is closed only when the cyclic GC runs",
)
def s12(ctx: Ctx) -> str:
    _, rows, left = ctx.abandon()
    closed = [row for row in rows if row[1] == "closed"]
    expect(closed, "3 s after the client left, the model's stream was still open")
    expect(closed[-1][2] == "completed=False", "the stream was closed, but not as an abandonment")
    return f"closed {float(closed[-1][0]) - left:.2f} s after the client left"


# --- E: errors ----------------------------------------------------------------------------------


@clause("E1", "A body that is not JSON is a 400 invalid_json in the error envelope")
def e1(ctx: Ctx) -> str:
    response = ctx.http.post("/chat", content=b"{bad", headers={"content-type": "application/json"})
    expect(response.status_code == 400, f"status {response.status_code}")
    message = check_envelope(response, "invalid_json")
    note = check_declared(
        ctx, "POST", "/chat", 400, response.headers["content-type"], response.content
    )
    return f"{note}; message: {message!r}"


E2_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("empty object", {"json": {}}, "body.query"),
    ("empty query", {"json": {"query": ""}}, "body.query"),
    ("blank query", {"json": {"query": "   "}}, "body.query"),
    ("k = 0", {"json": {"query": "hi", "k": 0}}, "body.k"),
    ("k = -3", {"json": {"query": "hi", "k": -3}}, "body.k"),
    ("k = 2.5", {"json": {"query": "hi", "k": 2.5}}, "body.k"),
    ("query is a number", {"json": {"query": 5}}, "body.query"),
    ("body is an array", {"json": []}, "body"),
    (
        "text/plain body",
        {"content": b'{"query": "hi"}', "headers": {"content-type": "text/plain"}},
        "body",
    ),
]


@clause("E2", "JSON that breaks a field's rules is a 422 validation_error that names the field")
def e2(ctx: Ctx) -> str:
    for label, kwargs, names in E2_CASES:
        response = ctx.http.post("/chat", **kwargs)
        expect(response.status_code == 422, f"{label}: status {response.status_code}")
        message = check_envelope(response, "validation_error")
        expect(names in message, f"{label}: message does not name {names!r}: {message!r}")
        check_declared(
            ctx, "POST", "/chat", 422, response.headers["content-type"], response.content
        )
    return f"{len(E2_CASES)} bad requests -> 422, each naming its field, each matching its schema"


@clause("E3", "A wrong method is a 405 method_not_allowed that names the allowed method")
def e3(ctx: Ctx) -> str:
    response = ctx.http.get("/chat")
    expect(response.status_code == 405, f"status {response.status_code}")
    check_envelope(response, "method_not_allowed")
    expect(response.headers.get("allow") == "POST", f"Allow: {response.headers.get('allow')}")
    return "405 in the envelope, Allow: POST (undeclared in /openapi.json, as routing errors are)"


@clause("E4", "An unknown path is a 404 not_found in the error envelope")
def e4(ctx: Ctx) -> str:
    response = ctx.http.get("/no-such-route")
    expect(response.status_code == 404, f"status {response.status_code}")
    check_envelope(response, "not_found")
    return "404 in the envelope"


@clause("E5", "500 and 504 are declared, and their shape is pinned where they can be provoked")
def e5(ctx: Ctx) -> str:
    raise Skip(
        "neither can be provoked over HTTP on a healthy server without breaking it; the observed "
        "side is pinned by tests/test_api.py::test_every_error_chat_declares_matches_what_its_"
        "handlers_really_send, and the declared side by clause A2"
    )


@clause("E6", "A trailing slash is redirected with a 307 (method kept) to the exact path")
def e6(ctx: Ctx) -> str:
    response = ctx.http.post("/chat/", json={"query": "hi", "k": 1}, follow_redirects=False)
    expect(response.status_code == 307, f"status {response.status_code}")
    location = response.headers.get("location", "")
    expect(location.endswith("/chat"), f"Location is {location!r}")
    return f"POST /chat/ -> 307 {location} (a client that does not follow redirects must call /chat)"


# --- X: cross-cutting -------------------------------------------------------------------------


@clause("X1", "Every response checked against /openapi.json matched what it declares")
def x1(ctx: Ctx) -> str:
    expect(not ctx.declared_failures, f"mismatches: {ctx.declared_failures}")
    expect(ctx.observed, "no response was checked against the declared contract in this run")
    declared = {
        (method.upper(), path, int(status))
        for path, item in ctx.spec["paths"].items()
        for method, operation in item.items()
        for status in operation["responses"]
    }
    never = sorted(declared - set(ctx.observed))
    return (
        f"{len(ctx.observed)} responses checked, all matched; "
        f"declared but not observed in this run: {[f'{m} {p} {s}' for m, p, s in never]}"
    )


@clause("X2", "The API sends no CORS headers, so only server-side clients can call it")
def x2(ctx: Ctx) -> str:
    origin = "http://localhost:8090"
    simple = ctx.http.get("/health", headers={"Origin": origin})
    expect(
        "access-control-allow-origin" not in simple.headers,
        "CORS headers appeared: a decision to allow browser pages must update docs/api-contract.md",
    )
    preflight = ctx.http.options(
        "/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    expect(preflight.status_code == 405, f"preflight status {preflight.status_code}")
    expect("access-control-allow-origin" not in preflight.headers, "CORS headers on a preflight")
    return (
        "no Access-Control-Allow-Origin on GET /health; the preflight OPTIONS /chat is a 405 -- "
        "a browser page on another origin can neither read responses nor send a JSON POST"
    )


# --- running ----------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_stub(
    yield_log: Path, app_path: str = "stub_server:app", extra_env: dict[str, str] | None = None
) -> tuple[subprocess.Popen, str, Path]:
    """Starts a stub server (stub_server.py unless told otherwise) on a free port and waits
    until /health answers. Returns the process, its base URL and its captured output file."""
    port = free_port()
    env = {
        **os.environ,
        **(extra_env or {}),
        "PYTHONPATH": os.pathsep.join([str(HERE), str(REPO_ROOT)]),
        "STUB_YIELD_LOG": str(yield_log),
        "STUB_DISABLE_GC": "1",
        # so clause S12 sees whether an abandoned stream is closed by the response teardown
        # itself, rather than by a garbage collection that happened to run during the 3 s wait
    }
    out_fd, out_name = tempfile.mkstemp(suffix=".log", prefix="stub_server_")
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app_path, "--port", str(port), "--log-level", "warning"],
        cwd=REPO_ROOT,
        env=env,
        stdout=out_fd,
        stderr=subprocess.STDOUT,
    )
    os.close(out_fd)
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        if process.poll() is not None:
            break
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code == 200:
                return process, base_url, Path(out_name)
        except httpx.HTTPError:
            time.sleep(0.5)
    process.kill()
    sys.exit(f"the stub server did not start:\n{Path(out_name).read_text(errors='replace')[-2000:]}")


def run(ctx: Ctx, only: set[str] | None) -> list[tuple[str, str, str, str]]:
    """Runs every clause that applies; prints each result as it completes."""
    results = []
    for item in CLAUSES:
        if ctx.tier not in item.tiers or (only and item.cid not in only):
            continue
        started = time.perf_counter()
        try:
            evidence = item.run(ctx)
            verdict = "PASS"
            if item.known_gap:
                verdict = "FAIL"
                evidence = f"the known gap is gone ({evidence}); remove known_gap and update the docs"
        except Skip as skipped:
            verdict, evidence = "SKIP", str(skipped)
        except Fail as failure:
            if item.known_gap:
                verdict, evidence = "GAP", f"{item.known_gap} -- observed: {failure}"
            else:
                verdict, evidence = "FAIL", str(failure)
        except Exception as error:
            verdict, evidence = "FAIL", f"{type(error).__name__}: {error}"
        elapsed = time.perf_counter() - started
        print(f"{verdict}  {item.cid:<4}{item.title}  ({elapsed:.1f} s)", flush=True)
        print(f"          {evidence}", flush=True)
        results.append((item.cid, verdict, item.title, evidence))
    return results


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--tier", choices=["deterministic", "real"], required=True)
    parser.add_argument("--base-url", help="the server to check (deterministic: default is a new stub)")
    parser.add_argument("--yield-log", type=Path, help="the STUB_YIELD_LOG file of a stub you started")
    parser.add_argument("--only", help="comma-separated clause ids, e.g. S2,S6")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None

    stub: subprocess.Popen | None = None
    stub_output: Path | None = None
    yield_log = args.yield_log
    base_url = args.base_url
    if args.tier == "deterministic" and base_url is None:
        yield_log = Path(tempfile.mkstemp(suffix=".log", prefix="stub_yields_")[1])
        stub, base_url, stub_output = start_stub(yield_log)
    elif args.tier == "real":
        base_url = base_url or "http://127.0.0.1:8000"
    if base_url is None:
        parser.error("--tier deterministic with a running stub needs --base-url")
    try:
        ctx = Ctx(base_url, args.tier, yield_log)
        title = ctx.http.get("/openapi.json").json()["info"]
        print(f"API contract check | tier={args.tier} | {base_url} | {title['title']} {title['version']}")
        results = run(ctx, only)
    finally:
        if stub is not None:
            stub.terminate()
            stub.wait(timeout=15)
    tally = {v: sum(1 for r in results if r[1] == v) for v in ("PASS", "GAP", "SKIP", "FAIL")}
    print(
        f"=== {tally['PASS']} PASS, {tally['GAP']} known GAP, {tally['SKIP']} SKIP, "
        f"{tally['FAIL']} FAIL ({len(results)} clauses) ==="
    )
    if tally["FAIL"] and stub_output is not None:
        print(f"stub server output: {stub_output}")
    return 1 if tally["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
