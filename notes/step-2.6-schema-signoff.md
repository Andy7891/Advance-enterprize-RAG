# Phase 2, Microstep 6 — Integration checkpoint: chunk schema sign-off

**Date:** 2026-09-16

## What the audit found before starting

Rather than eyeballing 1–2 files as the spec suggested, the verification ran across **all
214 chunks in all 76 files**. Result:

```
DISTINCT top-level key sets: 1     (all identical, matching DocumentChunk exactly)
DISTINCT metadata key sets:  1     (start_char, end_char, tokens_approx)
TYPE consistency:            100%
9 semantic invariants:       ALL HOLD
```

The data was already sound, so this step's value was never "find the bug" — it was
**turning a one-time check into something that keeps being true.**

## What was built

- **[tests/ingestion/test_chunk_contract.py](../tests/ingestion/test_chunk_contract.py)** —
  11 tests split across two deliberately distinct layers:
  - **Structural** (3 tests): exact key set per chunk, correct types, uniform `metadata`
    keys. This is the layer a JSON Schema would cover.
  - **Semantic** (7 tests): `char_count == len(text)`, both token counts agreeing,
    `chunk_id` format, non-empty text, ordered offsets, contiguous `chunk_index` per
    document, and offsets resolving against the real source document.
  - **Drift guard** (1 test): parses the README schema table and asserts it matches
    `dataclasses.fields(DocumentChunk)`.
- **[README.md](../README.md)** — signed-off schema table between
  `<!-- chunk-schema:start/end -->` markers, the verified invariant list, an explicit
  statement of what the sign-off does NOT cover, and a fixed pointer (it previously
  linked to `NOTES.md`, which is now itself a stub pointing at `notes/`).

93 tests total, up from 82. All static checks clean.

## Verified by experiment, not assumed

Two properties were proven by deliberately breaking things:

1. **Tests skip loudly without chunk data.** `data/chunks/` moved aside →
   `SKIPPED ... no chunk output in data/chunks/ — run python -m src.ingestion.pipeline first`.
2. **The drift guard catches real drift.** Introducing `char_kount` into the README table
   failed the test with:
   `README table and DocumentChunk disagree — only in README: {'char_kount'}, only in code: {'char_count'}`.

A useful property fell out of the design: with no chunk data present the run is
**`1 passed, 10 skipped`** — the drift guard needs no data, so **CI still enforces
README-vs-code consistency** even though the data checks cannot run there.

## Why these choices

- **Permanent tests over manual inspection:** a manual check proves nothing about the other
  74 documents and nothing about tomorrow.
- **Structural AND semantic layers:** a chunk can be perfectly well-formed and still claim
  `char_count: 500` for a 12-character string. Schema languages check shape; only invariants
  check that values agree with each other.
- **Skip, not fail, when data is absent:** `data/chunks/` is gitignored generated output, so
  a fresh clone legitimately has none. **Accepted cost:** these tests therefore always skip
  in CI, and a skip reads as green.
- **Prose table + dataclass as source of truth:** a hand-written JSON Schema would be a
  second definition to keep in sync; generating one needs a dependency for 10 fields. The
  drift guard is what stops the table becoming a lie.
- **Qualified sign-off:** the README states plainly that this covers format, not quality.

## Key concepts

- **Data contract** — an agreement about the shape and meaning of data passed between
  stages, so producer and consumer can change independently.
- **Structural validation** — are the right keys present with the right types?
- **Invariant** — a statement that must always be true about the relationship *between*
  values (`char_count == len(text)`), which no type system or schema can express.
- **Drift** — documentation and code describing the same thing gradually disagreeing.
- **Skip vs fail** — a test declining to run for a legitimate environmental reason, rather
  than reporting a defect.

## Honest limitations

1. **The contract tests never run in CI**, because chunk output is gitignored. They protect a
   developer who ran the pipeline locally, and nobody else.
2. **A skip looks like a pass** in summary output. `pytest -rs` is needed to see skip reasons.
3. **The sign-off covers format only.** 69% of chunks exceed the size target and the largest
   is 13,599 characters — schema-valid, badly sized.
4. **The drift guard checks field NAMES only**, not the types or descriptions in the table.
   The table could describe `chunk_index` as a string and nothing would notice.

## How this connects

**Previous:** M4 produced the chunk dataset; M5 verified the code that produces it.
**This step:** verifies the *output itself*, and records the contract downstream code will
rely on. **Next:** embedding + Qdrant upsert, whose payload schema is exactly this contract —
and where `chunk_id` being position-derived makes a delete-by-`doc_id` guard mandatory.

## How to do this yourself

1. Load every output file and collect the set of top-level keys per record. More than one
   distinct set means drift; that check alone catches most format bugs.
2. Compare against the code's own definition programmatically
   (`dataclasses.fields(...)`), never against a list you typed by hand.
3. Write down the invariants that must hold *between* fields, then assert each one. These
   are what a schema language cannot express.
4. Decide explicitly whether absent data should skip or fail, and make the skip reason name
   the command that fixes it.
5. If you document a schema in prose, add a test that parses your own documentation and
   compares it to the code — otherwise it will drift.
6. State what the sign-off does not cover. An unqualified "verified" is read as a warranty.

## Practice exercises

1. Corrupt one chunk file by hand (change `char_count` to 9999) and run the contract tests —
   which one fails, and is the message enough to find the file?
2. Delete a key from one chunk object and rerun — does the structural or semantic layer catch
   it first?
3. Add a fourth key to one chunk's `metadata` and confirm the uniformity test fails.
4. Add a field to `DocumentChunk` without touching the README and watch the drift guard fail.
5. Extend the drift guard to check the *types* column too, closing limitation #4 above.
6. Run `pytest tests/ingestion/test_chunk_contract.py -rs` with `data/chunks/` moved aside
   and read the skip reasons.
7. Re-run the pipeline with `--threshold 0.5`, then rerun the contract tests — do all
   invariants still hold with completely different chunk boundaries?
8. Write the equivalent JSON Schema by hand and compare its expressiveness against the
   semantic tests — which invariants can it *not* encode?
9. Make one chunk's `chunk_index` non-contiguous (edit a file) and confirm that test fails.
10. Time the contract suite against 214 chunks, then reason about whether it would still be
    practical at 100,000.
