# Phase 2, Step 2.2 — Document loader: `src/ingestion/loader.py`

**Date:** 2026-08-21

### What was built
- [src/ingestion/loader.py](src/ingestion/loader.py):
  - `LoadedDocument` — a frozen dataclass holding one file's raw extracted text +
    minimal metadata (`source_path`, `category`, `title`, `format`, `char_count`).
    Deliberately simpler than `DocumentChunk` (Step 2.1) — no `chunk_id`/`doc_id`/
    `chunk_index`, since this document hasn't been chunked yet; those identity fields only
    make sense once a chunking strategy exists.
  - `discover_documents(data_root)` — like Step 2.1's `discover_markdown_files` but for all
    three supported extensions, reusing the same `EXCLUDED_DIR_NAMES` exclusion set (see
    below — this constant was promoted from private to shared).
  - `load_document(source_path, data_root)` — loads one file, dispatching to the right
    extraction function by extension.
  - `load_all_documents(data_root, skip_errors=False)` — loads every supported file;
    `skip_errors` controls whether one bad file aborts the whole run or gets collected as a
    reported failure (see trade-off below).
  - `_read_text_file` — reads `.txt`/`.md`, with a UTF-8 → Latin-1 fallback for
    mis-encoded files (see real bug class below).
  - `_read_pdf_file` — extracts text from `.pdf` via `pypdf`, page by page, joined with
    blank lines.
- Added `pypdf==6.16.1` to `pyproject.toml`'s core `dependencies` (not `dev` — PDF loading
  is a runtime capability of the actual application, not a dev-only tool) and mirrored it
  into `requirements.txt`, per Step 1.1's sync convention.
- Promoted `schemas.py`'s `_EXCLUDED_DIR_NAMES` (private, underscore-prefixed) to
  `EXCLUDED_DIR_NAMES` (public) — `loader.py` needed the exact same exclusion set, and
  importing a private name across module boundaries is a real code smell (nothing stops the
  two lists from drifting if they'd stayed separately defined; sharing one guarantees they
  can't).
- [tests/ingestion/test_loader.py](tests/ingestion/test_loader.py) — 9 tests, including
  extraction from a REAL, hand-constructed, byte-valid single-page PDF (built with a correct
  xref table and trailer — pypdf's lenient-recovery parsing does NOT cover every malformed
  PDF, discovered by hitting a real "Stream has ended unexpectedly" error on my first,
  invalid attempt at the fixture and fixing it properly rather than working around it), a
  UTF-8-fallback test using a deliberately non-UTF-8 byte sequence, and a full run against
  the real 76-file `data/` corpus.

### Why this approach (trade-offs vs alternatives)
- **`pypdf` over `pdfplumber`/`PyMuPDF`/`unstructured`:** pure Python (no compiled/system
  dependencies to manage in Docker), permissive BSD license (PyMuPDF is AGPL — a real
  licensing consideration for a project that might be open-sourced or distributed),
  reasonably good on typical text-based PDFs (contracts, reports). Trade-off: weaker on
  complex multi-column layouts/tables than `pdfplumber`, and zero OCR capability for
  scanned/image-only PDFs — both explicitly documented as limitations in the code, not
  silently assumed away.
- **`skip_errors` as an explicit caller choice, not a fixed policy:** whether one bad file
  should abort a whole ingestion run or be skipped-and-reported is a genuine judgment call
  that depends on context (a small curated knowledge base vs a large messy one) — the
  module refuses to decide this for you; `load_all_documents(data_root)` fails fast by
  default (safer default: don't silently ingest an incomplete corpus), and
  `skip_errors=True` opts into best-effort collection when that's the right call.
- **UTF-8 → Latin-1 fallback, not UTF-8-or-crash:** a real, easy-to-miss bug class —
  not every text file on disk is actually UTF-8 (files saved by older tools or on different
  locales are often Latin-1/cp1252). Latin-1 never raises `UnicodeDecodeError` (every byte
  value is a valid Latin-1 code point), making it a safe last-resort that keeps one
  mis-encoded file from aborting an entire ingestion run — at the cost of possibly
  mis-decoding some characters if the file was actually some OTHER non-UTF-8 encoding.
- **Promoting `EXCLUDED_DIR_NAMES` to shared/public instead of duplicating the exclusion
  set in `loader.py`:** two independently-maintained copies of "which folders are noise"
  WILL eventually drift (someone adds a new noise-folder type to one and forgets the
  other) — one shared constant makes that impossible by construction.

### Key concepts used
- **Format dispatch** — routing to different logic based on a file's type (here, extension),
  a simple but foundational pattern for any multi-format ingestion pipeline.
- **PDF text extraction (lossy, layout-dependent)** — flagged as a genuine core RAG concept:
  unlike plain text, PDFs store positioned glyphs, not paragraphs; extraction quality varies
  by library and by the PDF's own internal structure (single vs multi-column, embedded text
  layer vs scanned image) — see [pypdf docs](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) and the practice exercises for how to actually verify extraction quality rather than assume it worked.
- **Encoding fallback** — trying a strict decode first, falling back to a permissive one,
  a general pattern for handling real-world files that don't perfectly match your primary
  assumption.
- **Fail-fast vs best-effort error handling** — a genuine design axis (not a "correct
  answer") for any batch-processing pipeline: does one bad input abort everything, or does
  the pipeline degrade gracefully and report failures separately?

### Real bugs hit and fixed while building this (not just "should work")
1. **Wrong file count in my own test assumption:** I initially asserted the real data corpus
   has 82 files, matching an earlier (imprecise) chat summary — running the code against
   the actual `data/` folder immediately proved that wrong (76, once `.ipynb_checkpoints`
   duplicates are correctly excluded). Fixed the TEST, not the code — the code was already
   correct; my assumption was stale. This is exactly why every deep-dive doc in this project
   pushes "run it against real data and check the actual number," not "trust what you
   remember from three steps ago."
2. **Invalid hand-crafted test PDF:** my first attempt at a minimal PDF fixture (raw bytes,
   no xref table, no `%%EOF`) failed with `PdfReadError: Stream has ended unexpectedly` —
   pypdf's lenient recovery mode does NOT successfully parse every malformed PDF. Fixed by
   building the xref table and trailer with byte-accurate offsets computed programmatically
   (see `_make_minimal_pdf` in the test file), verified in a scratch script before porting
   into the real test — proof that even a "fake" test PDF needs a genuinely valid internal
   structure, which is itself a small, concrete lesson in what PDF parsers actually require.

### How this connects to the pipeline
**Previous:** Step 2.1 defined `DocumentChunk`, the eventual output shape. **This step**
produces the raw text `DocumentChunk`s will be built FROM — `LoadedDocument.text` is exactly
what a chunking function will take as input.
**Next:** the actual **chunking strategy** (Phase 2's next microstep) — splitting each
`LoadedDocument.text` into appropriately-sized, possibly-overlapping pieces, then calling
Step 2.1's `make_chunk()` for each piece using `LoadedDocument.source_path`/`category`/
`title` to carry that metadata through. This is where chunk size/overlap decisions (a real
core RAG concept) get made — this loader intentionally does zero chunking itself.

### How to do this yourself (reproduce without me)
1. Look at what file formats actually exist (or will realistically exist) in your knowledge
   base before choosing extraction libraries — don't build PDF support speculatively if you
   have zero PDFs; don't skip it if you know contracts/reports will arrive as PDFs later.
2. For any new PDF library, test it against a REAL PDF from your own domain (or a hand-built
   minimal one, done correctly) before trusting it — read the library's own docs for known
   limitations (multi-column layout, tables, scanned/image-only files) rather than assuming
   "no exception raised" means "extraction succeeded well."
3. Decide, explicitly, your fail-fast vs best-effort policy for batch loading — don't let it
   default silently one way or the other; make it a parameter callers choose, if both are
   legitimate for different call sites.
4. For any "read this file as text" operation, ask what happens if the file ISN'T in your
   assumed encoding — test with an intentionally mis-encoded file, not just well-formed ones.
5. Before introducing a second module that needs the same filtering/exclusion logic as an
   existing one, promote the shared logic/constant to a public, shared location instead of
   copy-pasting it — copies drift, one source of truth can't.
6. Run the real test suite, `ruff check`, `ruff format --check`, and `mypy` before calling
   the module done — exactly as this step did, catching the file-count assumption error and
   the invalid-PDF-fixture bug along the way rather than after the fact.

### Practice exercises
1. Add a 4th format (e.g. `.docx` via `python-docx`) — extend `SUPPORTED_EXTENSIONS`,
   add a branch in `_extract_text`, and write a real test for it, following the exact
   pattern `.pdf` support used here.
2. Drop a REAL scanned/image-only PDF (no embedded text layer — e.g. export a page as an
   image and wrap it in a PDF) into a scratch folder and run `load_document` on it — observe
   that it "succeeds" (no exception) but `char_count` comes back near-zero — a live
   demonstration of pypdf's core limitation, not a hypothetical one.
3. Feed `load_all_documents` a folder containing one file with Windows CRLF line endings and
   one with Unix LF — does `char_count` treat `\r\n` as one character or two? Does this
   matter for later chunking?
4. Deliberately corrupt one real `.md` file's bytes (truncate it mid-file) and run
   `load_all_documents(data_root, skip_errors=True)` against a copy of `data/` containing
   it — confirm it's NOT collected as an error (since truncated text is still valid UTF-8
   text, just incomplete) — a good illustration of a failure mode this loader does NOT
   currently detect at all.
5. Multi-column PDF test: find or construct a 2-column PDF and compare pypdf's extracted
   text order against the visual reading order — is text from column 1 fully extracted
   before column 2 starts, or interleaved line-by-line?
6. Benchmark `load_all_documents` against the real 76-file corpus — how long does it take,
   and would it still be acceptably fast at 10x or 100x the file count?
7. Add a `page_count` field to `LoadedDocument` for PDFs specifically (via
   `len(reader.pages)`) — think about whether this belongs as a first-class field or in
   `metadata`-equivalent territory (note `LoadedDocument` currently has no metadata escape
   hatch at all, unlike `DocumentChunk` — is that a gap worth fixing?).
8. Change `skip_errors=True`'s behavior to also log a SUMMARY count at the end
   ("Loaded 74/76 documents, 2 failed") — a small but realistic operational-visibility
   improvement for a real ingestion run.
9. Try loading a `.md` file that's actually binary garbage (e.g. copy a `.pdf` and rename its
   extension to `.md`) — does the UTF-8/Latin-1 fallback logic handle it gracefully, crash,
   or silently produce nonsense text? Decide if this is a gap worth guarding against.
10. Write a property-style test: for every real file in `data/`, assert
    `load_document(...).char_count > 0` — does this hold for all 76 files, and would it
    still hold if a genuinely empty `.md` file were added?
