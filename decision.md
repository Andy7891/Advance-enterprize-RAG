# Decision Log

One line per decision: **decision | alternative rejected | reason | condition that would flip it**.
Newest at the bottom. Fuller reasoning lives in `NOTES.md`; this file is the scannable index.

---

## Phase 2, Microstep 2 — Text Chunking Strategy (2026-09-15)

**D-2026-09-15-01 — chunk_id scheme**
`chunk_id = f"{source_path}_{index}"` | SHA-256 content hash of (doc_id, index, text) | Human-readable IDs are trivially debuggable in logs, Qdrant payloads and error messages (`company/about.md_3` vs `a3f9c1b7e2d84f01`), and the spec called for it explicitly | Flip back to hashing the moment re-ingestion runs without a delete-by-doc_id guard, or if silent index corruption is ever observed in practice.

**D-2026-09-15-02 — start_char / end_char placement**
Stored inside `DocumentChunk.metadata` | First-class typed fields on the dataclass | Avoids widening the frozen Contract 1 field list that 14 tests and 3 deep-dive docs already reference; keeps the amendment additive | Flip to first-class fields as soon as retrieval or UI code reads offsets on a hot path, where a `KeyError` from a typo'd `metadata["strat_char"]` would be a production bug rather than a test failure.

**D-2026-09-15-03 — chunking strategy**
Semantic chunking (embedding-based boundary detection) | Recursive character splitting; fixed-size windows; markdown-header splitting | Cuts on actual meaning shifts rather than arbitrary character counts, giving chunks that are internally coherent and therefore embed more cleanly | Flip to recursive character splitting if ingestion latency becomes unacceptable, or if measured retrieval quality does not beat the recursive baseline enough to justify ~5-8k extra embedding calls per full run.

**D-2026-09-15-04 — embedding provider for boundary detection**
Ollama (`nomic-embed-text`, local) | Cohere API; sentence-transformers in-process | Free, offline, no API key, and already a running service in `docker-compose.yml` with `OLLAMA_URL` plumbed through `Settings` | Flip to Cohere if CPU inference time makes full-corpus chunking impractical and an API budget exists; flip to sentence-transformers if offline is required but Ollama's server dependency becomes a burden.

**D-2026-09-15-05 — chunker implementation**
`langchain_experimental.SemanticChunker` | Hand-rolling the ~60-line algorithm | Battle-tested implementation with four threshold methods already built; ships fastest | **Flip condition is already partially met** — the package emits `DeprecationWarning: langchain-experimental is being sunset and is no longer actively maintained`. Flip to a hand-rolled implementation at the first breaking upgrade, or before any production deployment.

**D-2026-09-15-06 — breakpoint threshold method**
`standard_deviation` (mean + 1.5σ) | `percentile`; absolute distance threshold | Cuts only where a distance is statistically unusual *for that document*, so a genuinely uniform document is not forced to split at invented boundaries | Flip to `percentile` if chunk-count variance across documents becomes unmanageable, since percentile guarantees a predictable number of cuts per document.

**D-2026-09-15-07 — undersized trailing chunks**
Merge backwards into the predecessor chunk | Keep as-is; drop below a min-size threshold | A 30-character fragment embeds to near-noise and pollutes retrieval with context-free hits, while dropping it would silently lose content (e.g. "Total contract value: $2.4M") | Flip to keeping them if a hard maximum chunk size ever becomes a real constraint, since merging deliberately breaks the size cap.

**D-2026-09-15-08 — chunk size as soft cap**
`target_chunk_chars=500` logged-but-not-enforced | Hard split at 500 chars | Semantic chunking derives boundaries from meaning; hard-splitting would override the exact signal the strategy exists to follow, reintroducing the mid-sentence cuts we chose semantic chunking to avoid | Flip to hard enforcement if a downstream embedding model has a strict token limit that oversized chunks actually exceed.

**D-2026-09-15-09 — overlap parameter**
Dropped entirely (not implemented) | Implementing ~15% overlap as originally chosen | Overlap is a sliding-window concept with no meaning in semantic chunking — boundaries are detected, not stepped, so there is no window to overlap | Flip if the strategy ever changes to recursive/fixed-size, where overlap becomes both meaningful and necessary.

**D-2026-09-15-10 — Ollama client library**
`langchain-ollama==1.1.0` | `langchain_community.embeddings.OllamaEmbeddings` | The community class is deprecated and `langchain-community` is itself being sunset; installing the standalone package added no conflicts and no version bumps | Flip only if `langchain-ollama` is itself deprecated in favour of another integration package.

**D-2026-09-15-11 — embedder injection**
Injected via constructor, typed as a `Protocol` | Constructing `OllamaEmbeddings` inside `Chunker` | Lets the test suite pass a deterministic fake, keeping CI fast and offline instead of depending on a live model server | Flip never; this is a straightforward dependency-inversion win with no real cost.

**D-2026-09-15-12 — git hygiene scope**
Forward-only untrack of `.venv` (new commit) | Amend Initial commit + force-push; local-only amend | The Initial commit is already published on GitHub, so rewriting history would require force-pushing over public `main` | Flip to a history rewrite if repository clone size becomes a genuine problem and the remote is confirmed to have no other consumers.

**D-2026-09-15-13 — clearing deliberate learning exercises**
Filled in all five CI TODOs and ran `ruff format` | Leaving them as hands-on exercises | CI was passing while asserting nothing, which is worse than no CI — a green check that verifies nothing actively misleads | Flip never; the exercises remain available in `docs/ci_deep_dive.md` without leaving the live pipeline hollow.
