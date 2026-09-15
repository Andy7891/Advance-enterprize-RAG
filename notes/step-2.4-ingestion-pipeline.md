# Phase 2, Microstep 4 — Ingestion pipeline script

**Date:** 2026-09-15

## Pre-flight audit (what the spec collided with)

1. **`data/raw/` doesn't exist.** The spec assumed it; real data is in `data/<category>/`.
   Kept the existing layout. A verified trap made this the safer call: files under
   `data/raw/` with `data_root=data/` make `infer_category` return `"raw"` for **every**
   chunk, silently destroying the category dimension hybrid search filters on.
2. **The spec omitted M3.** It said "loader → chunker", skipping token enrichment, which
   would have made its own "token distribution" report describe the char÷4 estimate we'd
   already measured as ~27% wrong. Enrichment is included.
3. **`data/chunks/` would have been committed** — `data/` is tracked and `.gitignore` had no
   rule for generated output.

## What was built

- [src/ingestion/pipeline.py](../src/ingestion/pipeline.py):
  - `run_pipeline(...)` — discover → load → chunk → enrich → write, with `embedder` and
    `counter` injected (never constructed inside).
  - `PipelineReport` / `TokenDistribution` / `DocumentFailure` — frozen dataclasses, so the
    run's outcome is data (returnable, assertable, serialisable), not console output.
  - `output_filename()` — flattens `company/about.md` → `company__about.md.json`.
  - `summarise()` / `format_report()` — computation and rendering kept separate.
  - `main()` — the composition root and `argparse` CLI (`python -m src.ingestion.pipeline`).
- [tests/ingestion/test_pipeline.py](../tests/ingestion/test_pipeline.py) — 13 tests driving
  the whole pipeline with `FakeEmbedder` + `FakeCounter`, so the suite stays offline.
- `data/chunks/` added to `.gitignore`.

## Real verified run

```
documents processed : 76      documents failed : 0
chunks written      : 214     avg chunk chars  : 1417.6
tokens  min=20 p25=84 median=200 p75=347 max=2788 mean=298.6
wall clock          : 9m12s
```
Independently verified against the output files: 214/214 chunks have
`approx_token_count == metadata.tokens_approx` (enrichment genuinely ran), and offsets
round-trip correctly into the source text.

## The finding that matters most

**The chunking configuration is wrong, and the report is what revealed it.** Measured
directly from the 214 output files:

- **149 of 214 chunks (69%) exceed the 500-character target.**
- The largest single chunk is **13,599 characters** (~3,400 tokens) — a wall of text.
- p75 (347 tokens) is roughly **4x** p25 (84) — wildly inconsistent sizes.
- Only **2.8 chunks per document** on average; the chunker is barely cutting.

This is exactly the behaviour predicted in
[docs/6 chunker_deep_dive.md](../docs/6%20chunker_deep_dive.md): `standard_deviation`
thresholding at 1.5σ cuts only at statistical outliers, and `buffer_size=1` blends each
sentence with its neighbours, diluting the topic shifts it's trying to detect. Worth tuning
(`--threshold 0.5`) and re-comparing `report.json` before building retrieval on top of this.

## Why these choices (trade-offs)

- **Always re-chunk, overwrite** over "skip if output exists": skipping is silently wrong
  after any parameter change — untouched files keep OLD boundaries, mixing two chunking
  strategies in one dataset. Cost: the full 9-minute run even for a one-file edit.
- **Skip-and-continue on failure** over fail-fast: one unreadable file shouldn't discard
  minutes of completed embedding work. **Cost, stated plainly:** the dataset LOOKS complete,
  so `documents_failed` is the only thing separating a partial corpus from silent recall
  loss. The run does not exit non-zero on failures.
- **Report as an object + JSON + printed summary** over printing only: tests assert on it,
  runs can be diffed, humans can read it.
- **One JSON per document** over a single combined file: one document can be inspected or
  regenerated in isolation, and a partial failure damages exactly one file.
- **`method="inclusive"` quantiles** over the default: the default extrapolates beyond the
  observed range — verified, it reported p25=5 and p75=11 for a two-chunk dataset whose
  range was 6–10.

## Key concepts

- **Orchestration** — code whose only job is calling other code in the right order; it
  contains no domain logic of its own.
- **Composition root** — the single place (here, `main()`) where real dependencies are
  constructed, so everything beneath it is a pure function of its inputs.
- **Keyword-only parameters** (the bare `*`) — forces callers to name arguments, making two
  same-typed parameters impossible to swap silently.
- **Idempotency** — running twice produces the same result as running once.
- **Failure isolation** — catching specific exception types so expected per-document
  problems are recorded while genuine code bugs still crash loudly.
- **Observability** — emitting structured data about what happened, rather than leaving it
  to be inferred from the filesystem.

## How this connects

**Previous:** M1–M3 built loader, chunker and enrichment as independent specialists.
**This step:** wires them into one operable command and produces a persisted dataset.
**Next:** embedding those chunks and upserting into Qdrant — where the positional `chunk_id`
scheme from M2 makes a delete-by-`doc_id` guard mandatory before re-ingesting.

## How to do this yourself

1. Write the orchestration module so it imports the specialists but they never import it —
   verify with `grep -n import` that the dependency arrows only point one way.
2. Take every external dependency as a parameter; construct real ones only in `main()`.
3. Put a bare `*` in the signature whenever two parameters share a type.
4. Decide the re-run policy explicitly, and write down what makes the fast option unsafe.
5. Catch specific exception types, never bare `Exception`, so your own bugs still surface.
6. Return a structured report; render it separately. Test the data, not the printed text.
7. Read `statistics.quantiles`' `method` parameter before trusting its output on small data.

## Practice exercises

1. Re-run with `--threshold 0.5` and diff the two `report.json` files — does the max chunk
   shrink below 13,599 characters?
2. Find the document that produced the 13,599-character chunk and read it — why did semantic
   chunking refuse to cut it?
3. Delete `data/chunks/` and re-run; confirm the file count returns to exactly 77.
4. Hash one output file across two runs and confirm the digests match (idempotency).
5. Drop a corrupt `.pdf` into `data/company/`, run, and confirm `documents_failed` is 1 and
   the other 76 still processed. Delete it afterwards.
6. Make `documents_failed > 0` cause a non-zero exit code, and argue whether that should be
   the default.
7. Add `--limit N` to process only the first N documents, for faster experiments.
8. Add `avg_tokens` to `PipelineReport` and see which existing tests break.
9. Time a full run with `FakeEmbedder` vs real Ollama — how much of the 9m12s is embedding?
10. Plot the 214 chunks' token counts as a histogram; compare its shape to what the five
    quantile numbers alone suggested.
