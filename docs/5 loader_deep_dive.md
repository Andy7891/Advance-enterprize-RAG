# Loader Deep Dive: Reverse-Engineering `src/ingestion/loader.py`

This is a self-contained learning path for Step 2.2:
[`src/ingestion/loader.py`](../src/ingestion/loader.py), validated against your real
[`tests/ingestion/test_loader.py`](../tests/ingestion/test_loader.py) and the actual 76-file
`data/` knowledge base.

**How to use this doc:** for each concept, run every exercise yourself in a real Python
REPL — actually run it, actually look at the output — form your own explanation, *then*
read the checkpoint. Each exercise ends with a **Concept & Learning** line stating exactly
what it proves.

---

## 0. High-level summary + concept map

In plain language: this file is a translator at a border crossing. Documents arrive in
different "languages" — plain text (`.txt`), markdown (`.md`), PDF (`.pdf`) — and this
module's whole job is to convert every one of them into ONE common language (plain Python
strings) before handing them onward. It doesn't decide how to chop that text up later
(that's the next step, chunking) — it only answers "what does this file actually SAY,"
regardless of what format it arrived in.

Concretely: it walks `data/`, finds every file it knows how to read, opens each one with the
right technique for its format, and hands back a small, uniform record (`LoadedDocument`)
per file — same shape every time, no matter which of the three formats produced it.

Concept map, in dependency order:

```
Custom exceptions:     a dedicated exception class (DocumentLoadError) that carries
                        WHICH file failed, not just that something failed
Data modeling:          LoadedDocument — a second, simpler frozen dataclass, contrasted
                        against DocumentChunk from Step 2.1
Format dispatch:        routing by file extension → one function per format
Text extraction:        .txt/.md — trivial read → encoding fallback (UTF-8 → Latin-1)
                        .pdf — pypdf page-by-page extraction, and its real limitations
Shared code reuse:      importing infer_category/extract_title/EXCLUDED_DIR_NAMES from
                        schemas.py instead of reimplementing them
Batch processing:       discover_documents (find everything) → load_all_documents
                        (load everything) → fail-fast vs best-effort as an explicit choice
```
Understand "format dispatch" before "text extraction" — dispatch is what decides which
extraction function even runs.

---

## 1. Concept-by-concept

---

### 1.1 A custom exception class (`DocumentLoadError`)

**Definition:** a project-specific `Exception` subclass, raised instead of letting a raw
library exception (`UnicodeDecodeError`, `PdfReadError`, etc.) propagate unchanged.

**Why here:** when loading 76+ files in a batch (`load_all_documents`), something WILL
eventually fail on some file, some day — a corrupted PDF, a permissions issue, a genuinely
unsupported format someone drops into `data/`. A generic exception tells you "something
broke"; `DocumentLoadError` (constructed with the file path baked into its message, e.g. in
`_read_pdf_file`) tells you WHICH file broke and WHY, directly in the traceback/log line,
without needing to add path-tracking logic at every call site separately.

**What breaks if changed:** let `PdfReadError`/`UnicodeDecodeError` propagate raw instead of
wrapping them — `load_all_documents`'s `except (DocumentLoadError, OSError)` clause
wouldn't catch `PdfReadError` at all (it's not a subclass of either), so a single bad PDF
would crash the ENTIRE batch even with `skip_errors=True` — the whole point of that
parameter would silently stop working for PDF-specific failures.

**Canonical reading:** [Python docs: user-defined exceptions](https://docs.python.org/3/tutorial/errors.html#user-defined-exceptions)

**Exercise 1 (5 min) — see the wrapping happen directly:**
```python
from src.ingestion.loader import _read_pdf_file, DocumentLoadError
from pathlib import Path

Path("scratch_bad.pdf").write_bytes(b"not a real pdf at all")
try:
    _read_pdf_file(Path("scratch_bad.pdf"))
except DocumentLoadError as e:
    print(type(e).__name__, "-", e)
```
> **Concept & Learning:** you get a `DocumentLoadError` whose message names the exact file
> path AND embeds the original `PdfReadError`'s message — direct proof this is a wrapping
> exception, not a suppression. The original error information isn't lost, it's carried
> forward inside a more specific, catchable type.

**Exercise 2 (10 min) — prove `except (DocumentLoadError, OSError)` wouldn't catch a raw
`PdfReadError`:**
```python
from pypdf.errors import PdfReadError
from src.ingestion.loader import DocumentLoadError

print(issubclass(PdfReadError, DocumentLoadError))
print(issubclass(PdfReadError, OSError))
```
> **Concept & Learning:** both print `False` — confirming, at the type-hierarchy level, that
> `load_all_documents`'s catch clause genuinely would NOT catch a raw `PdfReadError` if
> `_read_pdf_file` didn't wrap it first. This is why the wrapping isn't decorative — it's
> load-bearing for `skip_errors` to work at all.

**Exercise 3 (10 min) — use `raise ... from exc` and inspect the chain:**
```python
try:
    try:
        raise ValueError("original problem")
    except ValueError as exc:
        raise DocumentLoadError("wrapped problem") from exc
except DocumentLoadError as e:
    print(e)
    print(e.__cause__)
```
> **Concept & Learning:** `e.__cause__` gives you back the ORIGINAL `ValueError` object,
> not just its message — Python's exception chaining (`from exc`) preserves the full
> original traceback for debugging, while still letting calling code catch the more
> specific, wrapped type. This is exactly what `_read_pdf_file` does with
> `raise DocumentLoadError(...) from exc`.

**Checkpoint:**
*If `_read_pdf_file` just did `raise DocumentLoadError("...")` without `from exc`, would you
lose any real information?*

<details><summary>Reveal</summary>
Yes — you'd lose the automatic traceback chaining that lets someone debugging the error see
BOTH the wrapped message AND the original `PdfReadError`'s traceback (shown as "The above
exception was the direct cause of the following exception" in Python's default traceback
formatting). You could still manually include `str(exc)` in the message text (which
`_read_pdf_file` also does, separately) but you'd lose the structured, inspectable
`__cause__` link and the full original stack trace.
</details>

---

### 1.2 `LoadedDocument` — a second, deliberately simpler dataclass

**Definition:** a frozen, slotted dataclass (same pattern as Step 2.1's `DocumentChunk`)
holding one file's extracted text plus minimal metadata — no chunk-related fields at all.

**Why here:** it would be tempting to reuse `DocumentChunk` itself for this (avoid defining
a second class), but `DocumentChunk` requires `chunk_id`, `doc_id`, `chunk_index` — all
things that only make sense once chunking has happened. Forcing `load_document` to invent
placeholder values for those fields (e.g. `chunk_index=0` always) would create a
`DocumentChunk` that LOOKS complete but is semantically meaningless until real chunking
runs — a subtle correctness trap. `LoadedDocument` instead has exactly the fields that are
TRUE at this stage, no more.

**What breaks if changed:** merge `LoadedDocument` into `DocumentChunk` and call
`make_chunk()` directly from the loader with `chunk_index=0` for everything — every
multi-chunk document would get exactly one fake "chunk" (the whole file's text), silently
skipping real chunking entirely, and any code built assuming "one DocumentChunk = one
retrievable unit sized for embedding" would be quietly wrong for every document loaded this
way.

**Canonical reading:** [Python docs: dataclasses](https://docs.python.org/3/library/dataclasses.html) (same reference as Step 2.1 — same mechanism, different shape)

**Exercise 1 (5 min) — compare the two dataclasses' fields side by side:**
```python
import dataclasses
from src.ingestion.schemas import DocumentChunk
from src.ingestion.loader import LoadedDocument

chunk_fields = {f.name for f in dataclasses.fields(DocumentChunk)}
doc_fields = {f.name for f in dataclasses.fields(LoadedDocument)}
print("only in DocumentChunk:", chunk_fields - doc_fields)
print("only in LoadedDocument:", doc_fields - chunk_fields)
print("shared:", chunk_fields & doc_fields)
```
> **Concept & Learning:** you get the EXACT field difference printed, not asserted from
> memory — `chunk_id`/`doc_id`/`chunk_index`/`approx_token_count`/`metadata` only exist on
> `DocumentChunk` (chunking-stage concerns), while `format`/`char_count` overlap in NAME
> concept but are separately defined; `source_path`/`category`/`title` are genuinely shared
> meaning across both stages.

**Exercise 2 (10 min) — try the "shortcut" and see what goes wrong:**
```python
from pathlib import Path
from src.ingestion.loader import load_document
from src.ingestion.schemas import make_chunk

source = Path("data/company/about.md")
doc = load_document(source, Path("data"))
# Force the WHOLE file's text into one "chunk" -- the anti-pattern described above:
fake_chunk = make_chunk(
    source_path=source, data_root=Path("data"), title=doc.title, chunk_index=0, text=doc.text
)
print(fake_chunk.char_count)  # compare this to a REAL chunk size you'd expect later
```
> **Concept & Learning:** this "works" with zero errors — nothing stops you from doing it —
> but `fake_chunk.char_count` is the size of the ENTIRE file (likely 1000+ characters),
> nowhere near a realistic single-chunk size for embedding (typically 200-800 characters).
> This is the concrete version of the "semantically meaningless but structurally valid"
> trap described above — a good example of a bug that produces no exception, just silently
> wrong downstream behavior (bad retrieval quality, since the "chunk" is really a whole
> document).

**Exercise 3 (10 min) — check both classes really are independently frozen/slotted:**
```python
doc = load_document(Path("data/company/about.md"), Path("data"))
try:
    doc.text = "tampered"
except Exception as e:
    print(type(e).__name__)
try:
    doc.new_field = "oops"
except Exception as e:
    print(type(e).__name__)
```
> **Concept & Learning:** both raise the same errors `DocumentChunk` did in Step 2.1
> (`FrozenInstanceError` for reassignment, `AttributeError` for a new undeclared attribute)
> — confirming `LoadedDocument` inherited the SAME immutability/slots discipline as
> `DocumentChunk`, even though it's a completely separate class with different fields. The
> pattern, not just the specific fields, was deliberately carried over.

**Checkpoint:**
*Why doesn't `LoadedDocument` have a `metadata: dict[str, Any]` escape-hatch field the way
`DocumentChunk` does?*

<details><summary>Reveal</summary>
It's a genuine, currently-open gap rather than a deliberate design decision documented
anywhere in the code — `DocumentChunk` added `metadata` anticipating future needs (e.g.
extracted entities for the Neo4j graph), but `LoadedDocument` was written without that same
forward-looking escape hatch. This was flagged explicitly as practice exercise #6 in
`NOTES.md`'s Step 2.2 entry — a good example of noticing an inconsistency between two
related classes and deciding whether it's actually a gap worth closing (e.g. if you later
want to attach a PDF's `page_count` without a first-class field for it).
</details>

---

### 1.3 Format dispatch (`_extract_text`)

**Definition:** a function that looks at some property of the input (here, file extension)
and routes to different logic depending on its value — the "traffic controller" of the
module.

**Why here:** `.txt`/`.md` need trivial reading; `.pdf` needs a completely different library
and process. `_extract_text` is the single place that decision gets made, so every caller
(`load_document`) doesn't need to know or care which extraction strategy actually ran —
they just get text back.

**What breaks if changed:** call `_read_text_file` or `_read_pdf_file` directly from
`load_document` instead of going through `_extract_text`'s dispatch — you'd need to
duplicate the extension-checking `if`/`elif` logic at EVERY call site that needs to load a
file, and adding a 4th format later would mean finding and updating every one of those
call sites instead of one dispatch function.

**Canonical reading:** general software design principle — related to the
[Strategy pattern](https://refactoring.guru/design-patterns/strategy) from OOP design
patterns literature (used loosely/functionally here, not the full class-based GoF version).

**Exercise 1 (5 min) — trace the dispatch by hand:**
```python
from src.ingestion.loader import _extract_text
from pathlib import Path

for name in ["notes.txt", "readme.MD", "report.PDF", "image.png"]:
    p = Path(name)
    print(name, "->", p.suffix.lower())
```
Predict which of these four would successfully dispatch (without actually calling
`_extract_text`, which would fail since these files don't exist) — just reason about
`p.suffix.lower()` against `SUPPORTED_EXTENSIONS`.
> **Concept & Learning:** `.suffix.lower()` is what makes dispatch case-INSENSITIVE —
> `readme.MD` and `report.PDF` both correctly normalize to `.md`/`.pdf` before comparison.
> `image.png` would fall through to the `raise DocumentLoadError` branch — proving the
> dispatch function has an explicit "I don't know this format" case, not just silent
> failure.

**Exercise 2 (10 min) — add a trivial 4th format to see how dispatch scales:**
Temporarily add a branch: `if suffix == ".log": return path.read_text(encoding="utf-8")`
right before the `.pdf` check in `_extract_text`. Create a scratch `.log` file and confirm
`_extract_text` now handles it, WITHOUT touching `load_document`, `discover_documents`, or
anything else (you'll also need to add `.log` to `SUPPORTED_EXTENSIONS` for
`discover_documents` to find it — note this is a SEPARATE registration point from
`_extract_text`'s dispatch, both currently required).
> **Concept & Learning:** you directly observe that adding a format touches exactly TWO
> places (`SUPPORTED_EXTENSIONS` set, `_extract_text`'s dispatch) and nothing else — a
> concrete measurement of how well-isolated the dispatch design is. Also notice this reveals
> a real duplication: the "which formats exist" fact is currently expressed in two places
> that must be kept in sync by hand.

**Exercise 3 (10 min) — refactor dispatch into a lookup table and compare:**
```python
DISPATCH = {".txt": _read_text_file, ".md": _read_text_file, ".pdf": _read_pdf_file}

def extract_text_v2(path):
    suffix = path.suffix.lower()
    if suffix not in DISPATCH:
        raise DocumentLoadError(f"Unsupported: {suffix}")
    return DISPATCH[suffix](path)
```
Compare this dict-based dispatch against the actual `if`/`elif` chain in `_extract_text`.
Which is easier to extend? Which is easier to read for someone new to the file?
> **Concept & Learning:** both are valid, common patterns — `if`/`elif` chains read more
> like a simple decision tree (good for 2-3 branches, exactly what this module has);
> dict-based dispatch scales better once you have many formats sharing few actual handler
> functions (notice `.txt` and `.md` already map to the SAME function here — a dict would
> express that sharing more explicitly than the `if suffix in {".txt", ".md"}:` line does).
> Neither is objectively "correct" at this scale — a good example of a real, defensible
> style trade-off rather than a right/wrong answer.

**Checkpoint:**
*If you added support for `.rst` (reStructuredText) files, and they should be read exactly
like `.txt`/`.md` (plain UTF-8 text, no special parsing), what's the MINIMAL correct change
to `_extract_text`?*

<details><summary>Reveal</summary>
Just widen the existing condition: `if suffix in {".txt", ".md", ".rst"}:` — no new function
needed, since `.rst` needs identical handling to what `.txt`/`.md` already get. This is a
good test of whether you understand the dispatch is grouped by BEHAVIOR (which function
handles it), not by a 1:1 mapping of extension-to-function — resisting the urge to write a
redundant `_read_rst_file` that would just duplicate `_read_text_file`'s body is itself part
of understanding the pattern.
</details>

---

### 1.4 UTF-8 → Latin-1 encoding fallback (`_read_text_file`)

**Definition:** attempting a strict, "correct-assumption" decode first (UTF-8), and falling
back to a permissive decode (Latin-1) only if the first one fails.

**Why here:** not every text file on disk is actually UTF-8 — files saved by older tools, or
edited on certain Windows locales, are often Windows-1252/Latin-1 encoded instead. If
`_read_text_file` only tried UTF-8, ONE mis-encoded file anywhere in `data/` would raise
`UnicodeDecodeError` and (depending on `skip_errors`) potentially abort the entire ingestion
run over a single bad file, or need separate handling. Latin-1 is chosen specifically as the
fallback because it CANNOT fail to decode — every possible byte value (0-255) maps to some
valid Latin-1 character, making it a true last-resort that always succeeds.

**What breaks if changed:** remove the fallback (`try`/`except` block), just call
`path.read_text(encoding="utf-8")` directly — `test_load_document_falls_back_to_latin1_on_
bad_utf8` fails immediately with an uncaught `UnicodeDecodeError`, and any real file in
`data/` that happens to not be UTF-8 would crash loading entirely, rather than degrading to
"readable, possibly slightly wrong on a few special characters."

**Canonical reading:** [Python docs: codecs — standard encodings](https://docs.python.org/3/library/codecs.html#standard-encodings) and [Unicode HOWTO](https://docs.python.org/3/howto/unicode.html)

**Exercise 1 (5 min) — reproduce the exact failure this guards against:**
```python
from pathlib import Path

Path("scratch_bad_encoding.txt").write_bytes(b"Bob\x92s notes")  # 0x92 alone is invalid UTF-8
try:
    Path("scratch_bad_encoding.txt").read_text(encoding="utf-8")
except UnicodeDecodeError as e:
    print("Failed as expected:", e)
print(Path("scratch_bad_encoding.txt").read_text(encoding="latin-1"))
```
> **Concept & Learning:** the UTF-8 read genuinely raises (this ISN'T a hypothetical — you
> just reproduced it), while the Latin-1 read succeeds and prints something readable
> (`Bob's notes`, with `\x92` mapping to a Latin-1 right-quote character). Direct,
> reproducible proof of exactly the failure mode `_read_text_file`'s fallback exists for.

**Exercise 2 (10 min) — see WHY Latin-1 can never fail, by trying every byte value:**
```python
for byte_val in range(256):
    try:
        bytes([byte_val]).decode("latin-1")
    except UnicodeDecodeError:
        print(f"Byte {byte_val} failed!")
        break
else:
    print("Every single byte value 0-255 decoded successfully under latin-1")
```
> **Concept & Learning:** the loop completes without ever printing "failed" — direct,
> exhaustive proof that Latin-1 has NO invalid byte sequences (unlike UTF-8, where certain
> byte value SEQUENCES, like a lone `0x92`, are structurally invalid). This is exactly why
> it's chosen as the guaranteed-to-succeed last resort.

**Exercise 3 (10 min) — see the real cost: Latin-1 can produce WRONG (not just different)
text:**
```python
# A genuinely UTF-8 file with a non-ASCII character (e.g. an em-dash "—")
text_bytes = "café".encode("utf-8")
print(text_bytes)                          # the raw UTF-8 bytes
print(text_bytes.decode("latin-1"))        # what happens if we WRONGLY assume latin-1?
```
> **Concept & Learning:** decoding genuinely-UTF-8 bytes AS latin-1 produces garbled,
> WRONG text (`café` becomes something like `cafÃ©`) — not an error, just silently
> incorrect output. This is the real trade-off `_read_text_file`'s fallback accepts: trying
> UTF-8 FIRST minimizes this risk (real UTF-8 files decode correctly), but if a file were
> SOME OTHER non-UTF-8, non-Latin-1 encoding entirely, the Latin-1 fallback would "succeed"
> while producing garbage — a real, currently-accepted limitation, not a guarantee of
> correctness.

**Checkpoint:**
*Does the current fallback logic have any way to tell you AFTER THE FACT that a file was
Latin-1-decoded (as opposed to cleanly UTF-8)?*

<details><summary>Reveal</summary>
Yes, but only via the log line — `logger.warning("File %s is not valid UTF-8 — falling
back to latin-1", path)` fires exactly when the fallback is used. But this is NOT reflected
anywhere in `LoadedDocument`'s fields — there's no `encoding_used: str` field recording
whether a document was cleanly UTF-8 or had to fall back. If you needed to programmatically
audit "which documents in my corpus had encoding issues" without grepping logs, that would be
a genuine, currently-missing feature worth adding.
</details>

---

### 1.5 PDF extraction and its real limitations (`_read_pdf_file`)

**Definition:** using `pypdf.PdfReader` to open a PDF and call `.extract_text()` on each
page, joining the results.

**Why here:** PDFs are the one format in this module that ISN'T just "read the file as
text" — a PDF's internal structure stores positioned glyphs/fonts/drawing instructions, not
paragraphs. `pypdf` reconstructs approximate reading-order text from that structure, which
works well for simple, single-column, genuinely-text-based PDFs (the common case for
contracts/reports) but has real, documented failure modes for anything more complex.

**What breaks if changed:** feed it a SCANNED PDF (a PDF that's really just an image of a
page, with no embedded text layer) — `page.extract_text()` returns an empty string for every
page, `pages_text` ends up as a list of empty strings, and the final joined text is
essentially empty — NO exception is raised anywhere; the function "succeeds" while silently
producing a nearly-empty `LoadedDocument.text`. This is arguably the single most dangerous
failure mode in the whole module, because it looks identical to success.

**Canonical reading:** [pypdf docs: extracting text](https://pypdf.readthedocs.io/en/stable/user/extract-text.html) — includes pypdf's own documented caveats about layout limitations.

**Exercise 1 (10 min) — reproduce the silent-near-empty-text failure on purpose:**
```python
from pypdf import PdfWriter
writer = PdfWriter()
writer.add_blank_page(width=200, height=200)  # a real, valid PDF page with NO text at all
with open("scratch_blank.pdf", "wb") as f:
    writer.write(f)

from src.ingestion.loader import load_document
from pathlib import Path
doc = load_document(Path("scratch_blank.pdf"), Path("."))
print(repr(doc.text), doc.char_count)
```
> **Concept & Learning:** `load_document` succeeds completely (no exception), but
> `doc.char_count` is 0 or near-0 — this is the exact "silent failure" scenario described
> above, reproduced on a genuinely valid PDF (not a corrupted one) that simply has no text
> content. This is why `char_count` was specifically added as a first-class field on
> `LoadedDocument` — it's your only readily-available signal that something might have gone
> wrong, since no exception fires.

**Exercise 2 (10 min) — write a "suspiciously empty" detector:**
```python
def flag_suspicious(doc):
    if doc.format == "pdf" and doc.char_count < 20:
        print(f"WARNING: {doc.source_path} extracted almost no text ({doc.char_count} chars)")

flag_suspicious(doc)  # using `doc` from Exercise 1
```
> **Concept & Learning:** you've just built the missing safety net this module doesn't
> currently have built in — a post-load sanity check specifically for PDFs, since they're
> the one format where "succeeded" and "extracted nothing useful" can look identical.
> Worth considering as a real addition to `load_all_documents` (e.g. logging a warning for
> any PDF result under some character threshold) rather than leaving PDF quality entirely
> unverified.

**Exercise 3 (10 min) — check the page-joining behavior on a real multi-page PDF:**
Using the `_make_minimal_pdf`-style approach from `test_loader.py` (or any real multi-page
PDF you have access to), inspect the joined text for the `"\n\n"` (double newline) separator
between pages. Try changing `_read_pdf_file`'s join to a page-boundary marker instead:
```python
return "\n\n--- PAGE BREAK ---\n\n".join(pages_text)
```
Think through: would a LATER chunking step (that splits on paragraph breaks) treat a
`"\n\n"` page boundary the same as a genuine paragraph boundary within a page? Is that
correct or misleading?
> **Concept & Learning:** a plain `"\n\n"` join is AMBIGUOUS to any downstream code that
> uses blank lines as a paragraph-boundary signal — it can't distinguish "this is a new
> paragraph" from "this is a new PDF page" (which might be mid-paragraph in the original
> document, if a paragraph was split across a page break). This is a real, current
> simplification worth knowing about before chunking logic gets built on top of this
> module's output — flagged directly in NOTES.md's practice exercise #10 for exactly this
> reason.

**Checkpoint:**
*Why does `_read_pdf_file` use `page.extract_text() or ""` instead of just
`page.extract_text()`?*

<details><summary>Reveal</summary>
`extract_text()` can return `None` for certain malformed/edge-case pages in some pypdf
versions/scenarios (rather than always returning at least an empty string) — the `or ""`
guards against that, ensuring `pages_text` is always a list of actual strings that
`"\n\n".join(...)` can safely operate on. Without it, a single `None` in the list would
raise a `TypeError` from `join()` trying to concatenate `None` with strings — a real,
defensive one-liner protecting against an edge case that's easy to overlook until it
actually happens on some unusual real-world PDF.
</details>

---

### 1.6 Reusing `schemas.py`'s functions instead of reimplementing them

**Definition:** `loader.py` imports `EXCLUDED_DIR_NAMES`, `extract_title`, and
`infer_category` from `schemas.py` rather than writing its own versions.

**Why here:** `discover_documents` needs the exact same "which folders are noise" logic
`discover_markdown_files` already has; `load_document` needs the exact same "what's this
file's category/title" logic `make_chunk` already uses. Writing separate copies in
`loader.py` would work initially but creates a maintenance trap: if `data/`'s conventions
ever change (a new noise folder type, a different title-extraction rule), you'd have to
remember to update BOTH copies, and forgetting one produces a subtle, silent inconsistency
between what the loader "sees" and what the chunker later assumes.

**What breaks if changed:** reimplement `infer_category` separately in `loader.py` with
even a SLIGHTLY different rule (e.g. forgetting the "raise if no category subfolder" check)
— `LoadedDocument.category` and `DocumentChunk.category` could, for some edge-case file,
disagree with each other, even though conceptually they're supposed to mean the exact same
thing for the exact same file.

**Canonical reading:** general software engineering principle — [DRY (Don't Repeat
Yourself)](https://en.wikipedia.org/wiki/Don%27t_repeat_yourself), specifically as it
applies to sharing logic across modules within one codebase (as opposed to across separate
projects, where the trade-offs differ).

**Exercise 1 (5 min) — confirm both modules produce IDENTICAL categories for the same file:**
```python
from pathlib import Path
from src.ingestion.schemas import infer_category
from src.ingestion.loader import load_document

source = Path("data/contracts/Contract with DriveSmart Insurance for Carllm.md")
root = Path("data")
print(infer_category(source, root))
print(load_document(source, root).category)
```
> **Concept & Learning:** both print `"contracts"` — not a coincidence, but a direct
> consequence of `load_document` calling the EXACT SAME `infer_category` function
> internally, rather than a separately-written equivalent. There's no possibility of these
> two disagreeing, by construction.

**Exercise 2 (10 min) — simulate the "two copies drift" bug directly:**
```python
def infer_category_duplicate(source_path, data_root):
    # A separately-written "equivalent" -- but this one forgot the
    # "no category subfolder" validation infer_category has.
    relative = source_path.resolve().relative_to(data_root.resolve())
    return relative.parts[0] if relative.parts else "unknown"

# Try it on a file with no category subfolder:
from pathlib import Path
result = infer_category_duplicate(Path("data/onefile.md"), Path("data"))
print(result)
```
> **Concept & Learning:** the duplicate silently returns `"onefile.md"` (treating the
> FILENAME as a category!) instead of raising the clear `ValueError` the real
> `infer_category` would — a concrete demonstration of exactly the "forgot to update both
> copies identically" bug this module's design avoids by sharing one function instead of
> duplicating it.

**Exercise 3 (10 min) — find every shared import and justify each one:**
Look at `loader.py`'s import line: `from src.ingestion.schemas import EXCLUDED_DIR_NAMES,
extract_title, infer_category`. For each of the three, write one sentence explaining
specifically what would go wrong if `loader.py` had its own separate copy instead (you've
already done this for `infer_category` in Exercises 1-2 — do the same reasoning for
`EXCLUDED_DIR_NAMES` and `extract_title`).
> **Concept & Learning:** this is a deliberate practice in articulating WHY a design choice
> was made, for each individual piece, rather than accepting "code reuse is good" as a vague
> platitude — `EXCLUDED_DIR_NAMES` drifting would mean `discover_documents` and
> `discover_markdown_files` could disagree about which files count as "real" content;
> `extract_title` drifting would mean a document's title could differ depending on whether
> it went through the loader or the old markdown-only path, breaking the assumption that
> title is a single, consistent fact about a file.

**Checkpoint:**
*If `schemas.py` had a bug in `infer_category` (say, it crashed on a specific edge case),
would that bug affect `loader.py` too, or is `loader.py` insulated from it?*

<details><summary>Reveal</summary>
It WOULD affect `loader.py` too — and that's the correct, intended behavior here, not a
downside. Because `loader.py` imports and calls the real `infer_category` (not a copy), a
fix in `schemas.py` automatically fixes both call sites simultaneously; conversely, a bug in
`schemas.py` is a bug in both places until fixed once, centrally. This is the entire payoff
of sharing code instead of duplicating it — one source of truth means one place to fix
things, at the cost of the two modules now being coupled (a change to `infer_category`'s
signature would require checking `loader.py` too).
</details>

---

### 1.7 `discover_documents` — recursive multi-extension file discovery

**Definition:** walks `data_root` recursively (`rglob("*")`) and filters down to files whose
extension is in `SUPPORTED_EXTENSIONS`, excluding noise directories.

**Why here:** `rglob("*")` (matching EVERYTHING) followed by manual filtering, rather than
three separate `rglob("*.txt")` + `rglob("*.md")` + `rglob("*.pdf")` calls merged together —
a deliberate choice: one filesystem walk instead of three, and the filtering logic
(extension check + exclusion check) lives in one place, easy to extend when a 4th format is
added (see 1.3, Exercise 2) without needing a 4th separate `rglob` call.

**What breaks if changed:** do three separate `rglob` calls and concatenate the results
instead — functionally equivalent, but you'd walk the same directory tree three times
instead of once (a real, measurable inefficiency at scale, though negligible at 76 files),
and the exclusion-filtering logic would need to be applied three times instead of once.

**Canonical reading:** [Python docs: pathlib — Path.rglob](https://docs.python.org/3/library/pathlib.html#pathlib.Path.rglob) (same reference as Step 2.1's `schemas.py` deep dive — same mechanism, applied slightly differently here)

**Exercise 1 (5 min) — verify `path.is_file()` matters:**
```python
from pathlib import Path
root = Path("data")
without_is_file_check = [p for p in root.rglob("*") if p.suffix.lower() in {".txt", ".md", ".pdf"}]
with_is_file_check = [p for p in without_is_file_check if p.is_file()]
print(len(without_is_file_check), len(with_is_file_check))
```
> **Concept & Learning:** in this specific project these two counts likely match (no
> directory happens to be literally named e.g. `report.pdf`), but the `is_file()` check in
> `discover_documents` guards against exactly that edge case — a directory whose NAME
> happens to end in a supported extension would otherwise be treated as a file to load,
> and crash later when something tries to read it as text.

**Exercise 2 (10 min) — count files by extension to sanity-check real corpus composition:**
```python
from collections import Counter
from src.ingestion.loader import discover_documents
from pathlib import Path

files = discover_documents(Path("data"))
print(Counter(f.suffix.lower() for f in files))
```
> **Concept & Learning:** you should see `Counter({'.md': 76})` — confirming, with real
> data, that this project's knowledge base is currently 100% markdown, zero `.txt`/`.pdf`
> files exist yet. This is worth knowing explicitly: all the PDF-handling code in this
> module is currently UNEXERCISED by the real corpus — only the test suite's synthetic PDF
> fixture actually exercises `_read_pdf_file` right now, a good reminder to eventually add a
> real PDF to `data/` to exercise this code path against real content, not just a test
> fixture.

**Exercise 3 (10 min) — measure the cost of walking the tree multiple times:**
```python
import time
from pathlib import Path

root = Path("data")
start = time.perf_counter()
for _ in range(3):
    list(root.rglob("*.md"))
three_separate = time.perf_counter() - start

start = time.perf_counter()
list(root.rglob("*"))
one_walk = time.perf_counter() - start

print(f"three separate rglobs: {three_separate:.4f}s, one rglob('*'): {one_walk:.4f}s")
```
> **Concept & Learning:** at 76 files the difference is likely microseconds — genuinely
> negligible right now. This exercise is less about proving a performance win TODAY and
> more about recognizing that the "one walk" design choice is the kind of decision that
> pays off at scale (thousands/millions of files) even when it's imperceptible at your
> current corpus size — a useful habit of evaluating design choices against their intended
> scale, not just today's numbers.

**Checkpoint:**
*Does `discover_documents` guarantee the returned list is in any particular meaningful
order (e.g. alphabetical, or by category)?*

<details><summary>Reveal</summary>
Yes, but only incidentally — `sorted(...)` wraps the whole generator expression, so the
result IS alphabetically sorted by full path string. This is deterministic and
reproducible (useful for consistent test output, like Step 2.1's `discover_markdown_files`),
but it's NOT sorted by category, file size, or any other meaningful business property — just
plain lexicographic path order, which happens to roughly group files by category since
category is the first path segment, but that's a side effect of path structure, not a
deliberate sort key.
</details>

---

### 1.8 Batch loading with an explicit fail-fast/best-effort choice (`load_all_documents`)

**Definition:** loops over every discovered file, loading each one, and lets the caller
choose (via `skip_errors`) whether one failure should abort the whole batch or be collected
and reported separately.

**Why here:** this is a genuine, context-dependent trade-off, not something with one
universally correct answer. For a small, curated, "every document matters" knowledge base
(this project's current 76-file corpus), you might WANT ingestion to fail loudly the moment
anything goes wrong — a silent gap is dangerous when completeness matters. For a much
larger, messier corpus (thousands of scraped/uploaded documents), failing the entire batch
over one bad file is often worse than proceeding with a documented, reviewable list of
skipped failures. `load_all_documents` refuses to bake in either answer — it exposes the
choice as a parameter instead.

**What breaks if changed:** hard-code `skip_errors=True` as the ONLY behavior (remove the
parameter, always collect-and-continue) — `test_load_all_documents_raises_by_default` would
fail, and more importantly, a silent gap in your knowledge base (one contract file that
quietly failed to load) could go completely unnoticed unless something explicitly checks the
`errors` return value — which nothing is currently forced to do.

**Canonical reading:** no single canonical reference — this is a recurring, general batch-
processing design question; related in spirit to how build tools/CI (recall Step 1.2's
`ci.yml`) similarly must decide whether one failing job should block everything else or run
independently.

**Exercise 1 (10 min) — observe both behaviors side by side on the same broken input:**
```python
from pathlib import Path
import tempfile

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "data"
    (root / "company").mkdir(parents=True)
    (root / "company" / "good.md").write_text("# Good\nFine.")
    (root / "company" / "bad.pdf").write_bytes(b"not a real pdf")

    from src.ingestion.loader import load_all_documents

    docs, errors = load_all_documents(root, skip_errors=True)
    print("skip_errors=True:", len(docs), "loaded,", len(errors), "failed")

    try:
        load_all_documents(root, skip_errors=False)
    except Exception as e:
        print("skip_errors=False raised:", type(e).__name__)
```
> **Concept & Learning:** identical broken input, two completely different outcomes purely
> based on ONE boolean parameter — direct, side-by-side proof that this is a real behavioral
> switch, not a cosmetic option. Notice also that `skip_errors=True` still successfully
> loaded `good.md` DESPITE `bad.pdf` failing — the batch genuinely continues past the
> failure rather than stopping.

**Exercise 2 (10 min) — the errors list carries the ORIGINAL exception, not just a message:**
```python
docs, errors = load_all_documents(root, skip_errors=True)  # using root from Exercise 1
for path, exc in errors:
    print(path.name, "->", type(exc).__name__, "->", exc)
```
> **Concept & Learning:** `errors` is a list of `(Path, Exception)` TUPLES, not strings —
> meaning calling code can programmatically inspect the exception TYPE (e.g. "was this a
> DocumentLoadError vs an OSError, like a permissions issue") and decide different follow-up
> actions per failure type, rather than only having a flattened error message to parse.

**Exercise 3 (10 min) — build a summary report on top of the raw output:**
```python
def summarize(docs, errors):
    total = len(docs) + len(errors)
    print(f"Loaded {len(docs)}/{total} documents.")
    if errors:
        print("Failures:")
        for path, exc in errors:
            print(f"  - {path}: {exc}")

summarize(docs, errors)  # using docs/errors from Exercise 1
```
> **Concept & Learning:** you've just built exactly the "operational visibility"
> improvement flagged as practice exercise #7 in NOTES.md's Step 2.2 entry — the raw
> `(docs, errors)` tuple `load_all_documents` returns is deliberately UNFORMATTED/low-level,
> leaving presentation (a human-readable summary, a structured report, a logged metric) as a
> separate concern for calling code to build on top, rather than baking one specific report
> format into the loader itself.

**Checkpoint:**
*If you call `load_all_documents(data_root)` with NO `skip_errors` argument at all, what's
the default behavior, and why was THAT chosen as the default rather than the other option?*

<details><summary>Reveal</summary>
The default is `skip_errors=False` — fail-fast. This was chosen as the SAFER default: an
ingestion pipeline that silently drops documents by default, unless you remember to check
for that possibility, is a more dangerous failure mode (a "successful-looking" run that's
actually missing content) than one that stops and makes you look at the problem immediately.
Defaults in APIs are a real design signal — choosing the SAFER behavior as the default,
and requiring an explicit opt-in (`skip_errors=True`) for the riskier-but-sometimes-more-
practical behavior, is a deliberate, defensible convention worth recognizing and reusing in
your own function designs.
</details>

---

## 2. Validating it's ACTUALLY working (not just "no errors")

Direct validation against your real project (run every one of these yourself):

| What to check | Command / snippet | What "working" looks like |
|---|---|---|
| Discovery finds the real corpus | `python -c "from pathlib import Path; from src.ingestion.loader import discover_documents; print(len(discover_documents(Path('data'))))"` | Prints `76` (see section 1.7's correction: 82 total on disk, 76 once `.ipynb_checkpoints` are excluded) |
| Every discovered file is genuinely a supported format | `python -c "from pathlib import Path; from src.ingestion.loader import discover_documents; print({f.suffix.lower() for f in discover_documents(Path('data'))})"` | `{'.md'}` — confirms the current real corpus is 100% markdown (see 1.7, Exercise 2) |
| Loading doesn't silently drop or duplicate content | `docs, errs = load_all_documents(Path('data'), skip_errors=True); print(len(docs), len(errs))` | `76 0` — every real file loads cleanly with zero failures |
| char_count is a meaningful signal, not always the same | Loop over all loaded docs and print `min`/`max`/`mean` of `char_count` | A real spread of values (not all identical, not all suspiciously near-zero) |
| PDF path is exercised at all (currently untested against real data) | Run `tests/ingestion/test_loader.py::test_load_document_extracts_real_pdf_text` specifically | Passes — but remember (1.7) this only tests a synthetic fixture, not a real-world PDF |
| Encoding fallback genuinely triggers when needed | Run `test_load_document_falls_back_to_latin1_on_bad_utf8` and add a temporary `print` inside `_read_text_file`'s except block | The warning log line fires exactly once, for exactly the deliberately-mis-encoded fixture file |
| Static checks clean | `ruff check src/ingestion tests/ingestion && ruff format --check src/ingestion tests/ingestion && mypy src/ingestion` | All three report clean |

**Failure-mode drills** (predict the outcome before running):

1. **Point `discover_documents` at a data_root that doesn't exist at all** — does
   `Path("nonexistent").rglob("*")` raise immediately, or silently return an empty iterator?
   (Worth checking directly — `rglob` on a nonexistent path behaves differently than you
   might assume.)
2. **Load a genuinely empty `.md` file (0 bytes)** — does `load_document` succeed with
   `char_count == 0`, and does `extract_title`'s fallback (filename stem) kick in cleanly,
   or does anything break on empty input?
3. **Run `load_all_documents` twice in a row on the same unchanged `data/`** — confirm the
   two `docs` lists are equal (`==`) field-by-field, proving loading itself is a pure,
   repeatable operation with no hidden state carried between runs (a different but related
   idea to Step 2.1's chunk-ID idempotency — here it's about the LOADING step itself being
   deterministic, before any ID hashing even happens).

---

## 3. Visualizing it working

**Data flow, as this module actually implements it** (not a generic ETL diagram):

```
data/<category>/<file>.{txt,md,pdf}   (currently: 76 real .md files, 0 real .txt/.pdf)
        │
        ▼
discover_documents(data_root)
        │  rglob("*") -> filter: is_file() AND suffix in {.txt,.md,.pdf}
        │               AND NOT under .ipynb_checkpoints/.git/__pycache__
        ▼
  list[Path], sorted alphabetically
        │
        ▼  for each path:
  load_document(path, data_root)
        │
        ├── _extract_text(path)              <- format dispatch (1.3)
        │     ├── .txt/.md -> _read_text_file   <- UTF-8, falls back to Latin-1 (1.4)
        │     └── .pdf     -> _read_pdf_file     <- pypdf, page-by-page (1.5)
        │
        ├── category = infer_category(...)   <- REUSED from schemas.py (1.6)
        ├── title = extract_title(...)       <- REUSED from schemas.py (1.6)
        ├── source_path = relative, POSIX-normalized
        │
        ▼
    LoadedDocument(source_path, category, title, format, text, char_count)
        │  (frozen, slots -- same discipline as DocumentChunk)
        ▼
  [ EITHER surfaced one-by-one via load_document,
    OR batched via load_all_documents, which ALSO decides
    fail-fast vs best-effort per the skip_errors flag (1.8) ]
        │
        ▼
  [ eventually: chunking strategy splits LoadedDocument.text into pieces,
    then calls schemas.make_chunk() for each -- NOT built yet ]
```

**Tools worth using for deeper visibility:**
- **A plain Python REPL from the repo root** — as with `schemas.py`, this module has no
  server/container to inspect; every function is callable directly, side-effect-free
  (except logging), and inspectable by just printing its return value.
- **Python's `logging` module, turned up to see the fallback warnings live:**
  ```python
  import logging
  logging.basicConfig(level=logging.WARNING)
  ```
  Run this once at the top of a REPL session before calling `load_all_documents` — now
  every Latin-1 fallback and every skipped-error warning prints live, instead of silently
  happening with no visible trace (by default, a logger with no handler configured produces
  NO visible output at all — a real, easy-to-miss gotcha worth knowing generally, not just
  for this module).
- **`pytest --collect-only -q tests/ingestion/test_loader.py`** — lists all 9 tests without
  running them, a quick way to see the full list of behaviors already covered vs. which of
  the exercises above would make good NEW permanent tests to add (e.g. the "suspiciously
  empty PDF" detector from section 1.5's Exercise 2).

**One end-to-end "watch it work" exercise, tying validation + visualization together:**

1. Open a Python REPL at the repo root, and turn on logging first:
   `import logging; logging.basicConfig(level=logging.WARNING)`.
2. `from pathlib import Path; from src.ingestion.loader import *; root = Path("data")`.
3. `files = discover_documents(root)` — print `len(files)`, confirm `76`.
4. Pick one real file: `f = root / "employees" / "Alex Chen.md"` (or any real employee
   file) — `doc = load_document(f, root)`.
5. Print `doc.format`, `doc.category`, `doc.title`, `doc.char_count` — sanity-check each
   against what you'd expect just from having glanced at that file's content in Step 2.1's
   exploration.
6. Now deliberately create a broken file to watch the fallback/error paths live:
   `(root / "employees" / "scratch_broken.pdf").write_bytes(b"not a pdf")` — then
   `try: load_document(root / "employees" / "scratch_broken.pdf", root)\n except Exception as e: print(type(e).__name__, e)`.
   Watch the `DocumentLoadError` print with the exact broken file's path embedded in it.
7. Run `load_all_documents(root, skip_errors=True)` NOW (with your scratch broken file
   still sitting in `data/employees/`) — watch the `logging.warning` line print LIVE in your
   REPL output at the exact moment that file is skipped, and confirm `len(errors) == 1` and
   that file's path is in it.
8. **Clean up**: delete `scratch_broken.pdf` from `data/employees/` — this was a real file
   you created inside the actual data folder for this exercise; removing it afterward keeps
   the corpus clean for later steps.
9. Finally, rerun `load_all_documents(root, skip_errors=True)` one more time and confirm
   you're back to `76 loaded, 0 errors` — proof the module has no persistent state of its
   own; it purely reflects whatever is currently on disk, live.

If you can predict, before running each of these 9 steps, exactly what will print, and
explain in one sentence WHY each behavior happens (not just what it does) — you've
internalized this module for real, not just "the tests pass."
