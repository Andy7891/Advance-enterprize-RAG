"""The REAL FastAPI app served with a scripted stand-in for the retriever and the model.

Every route, exception handler, middleware and the SSE framing function are exactly the ones in
src/api/main.py: only the app's lifespan is swapped, so it never touches Qdrant, Neo4j or Ollama.
Nothing in src/api/main.py is patched, which means what a client observes here is what it would
observe against the real stack, minus the model's own words -- and with those words under the
test's control, so every result is exact and repeatable.

Two extras exist only here, never in the real app, and neither appears in /openapi.json:
  /web/...             the browser page in web/, served from the SAME origin as the API so a real
                       browser can call it without CORS (the real app has no CORS, see C-X2)
  /web/scenarios.json  the scenario table, so the page and the stub read one source of truth

Run (from the repo root, with the project venv; PYTHONPATH entries are separated by ";" on
Windows and ":" elsewhere):
    PYTHONPATH="practice codes/phase 7/m7_api_contract_checkpoint;." \
        python -m uvicorn stub_server:app --port 8004
Set STUB_YIELD_LOG=<file> to have the stub model append a line every time it produces a
fragment and when its generator is closed -- how check_api_contract.py sees an abandoned stream.
Set STUB_DISABLE_GC=1 to switch the cyclic garbage collector off (see the note in the code).
"""

import gc
import os
import re
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from scenarios import DEFAULT_SCENARIO, SCENARIOS, Scenario
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.api.main import app
from src.ingestion.schemas import DocumentChunk
from src.orchestration.rag import RAGPipeline
from src.retrieval.pipeline import RankedChunk, RetrievalResult

if os.environ.get("STUB_DISABLE_GC"):
    gc.disable()
    # a determinism switch for check_api_contract.py's clause S12: with no cyclic garbage
    # collection running by itself, an abandoned stream is closed only if the app's own response
    # teardown closes it. Never set for normal use -- the real app runs with the GC on.

HERE = Path(__file__).resolve().parent
MARKER = re.compile(r"\[\[scenario:([\w-]+)\]\]")
MAX_CHUNKS = 5
# the most chunks the stub retriever will ever return, however large a k the client asks for


def scenario_of(text: str) -> Scenario:
    """The scenario named by a marker in `text`, or the default one when there is no marker."""
    found = MARKER.search(text)
    name = found.group(1) if found else DEFAULT_SCENARIO
    if name not in SCENARIOS:
        raise ValueError(f"unknown stub scenario {name!r}; known: {sorted(SCENARIOS)}")
    return SCENARIOS[name]


def log_line(text: str) -> None:
    """Appends one timestamped line to the yield log, if the run asked for one."""
    path = os.environ.get("STUB_YIELD_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.time():.3f} {text}\n")


def make_ranked(rank: int) -> RankedChunk:
    """One retrieved chunk, numbered `rank`, with a distinct source path."""
    text = f"Acme fact number {rank}."
    chunk = DocumentChunk(
        chunk_id=f"doc{rank}_0",
        doc_id=f"doc{rank}",
        source_path=f"company/doc{rank}.md",
        category="company",
        title=f"Doc {rank}",
        chunk_index=0,
        text=text,
        char_count=len(text),
        approx_token_count=max(1, len(text) // 4),
    )
    return RankedChunk(
        chunk=chunk,
        final_rank=rank,
        relevance_score=1.0,
        hybrid_score=1.0,
        hybrid_rank=rank,
        vector_rank=rank,
        graph_rank=None,
        vector_score=1.0,
    )


class StubRetriever:
    """Returns min(k, MAX_CHUNKS) canned chunks, or none when the scenario says so."""

    def retrieve(self, query: str, k: int) -> RetrievalResult:
        if scenario_of(query).retrieves_nothing:
            return RetrievalResult(query=query, results=())
        ranked = tuple(make_ranked(rank) for rank in range(1, min(k, MAX_CHUNKS) + 1))
        return RetrievalResult(query=query, results=ranked)


class StubLLM:
    """Streams the scenario's fragments, with its delay, then optionally fails."""

    def generate(self, prompt, temperature, max_tokens, *, system=None) -> str:
        return "".join(scenario_of(prompt).fragments)

    def stream(self, prompt, temperature, *, system=None) -> Iterator[str]:
        scenario = scenario_of(prompt)
        # the prompt carries the question, so the question's marker picks the scenario
        completed = False
        try:
            for index, fragment in enumerate(scenario.fragments):
                time.sleep(scenario.delay)
                # a real, BLOCKING sleep, like a blocking read of the model's next token
                log_line(f"yielded {index}")
                yield fragment
            if scenario.fails_with is not None:
                raise RuntimeError(scenario.fails_with)
            completed = True
        finally:
            log_line(f"closed completed={completed}")
            # runs when the consumer finishes OR abandons this generator -- the signal that the
            # server stopped asking the model for more, which is what frees a real model


@asynccontextmanager
async def stub_lifespan(stub_app):
    stub_app.state.rag = RAGPipeline(StubRetriever(), StubLLM())
    yield


app.router.lifespan_context = stub_lifespan
# swaps ONLY startup/shutdown: no Qdrant/Neo4j/Ollama client is ever built


async def scenarios_json(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            name: {"fragments": list(s.fragments), "expected": list(s.expected)}
            for name, s in SCENARIOS.items()
        }
    )


app.add_route("/web/scenarios.json", scenarios_json, include_in_schema=False)
# registered BEFORE the static mount below, which would otherwise answer /web/* with a 404 first
app.mount("/web", StaticFiles(directory=HERE / "web", html=True), name="web")
