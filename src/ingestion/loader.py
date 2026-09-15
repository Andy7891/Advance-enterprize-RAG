"""Document loading: reads raw files off disk (.txt, .md, .pdf) into plain text
plus minimal metadata — the step BEFORE chunking. `DocumentChunk` (schemas.py)
is what chunking eventually produces FROM the `LoadedDocument`s this module
produces; this module never constructs a DocumentChunk itself.
"""

# WHAT: Postpones evaluation of type hints (e.g. `-> list[Path]`) to string form.
# WHY: Lets us use modern syntax like `list[Path]` / `tuple[Path, Exception]`
#      without every caller needing Python 3.9+ typing imports at runtime.
from __future__ import annotations

# WHAT: Standard library logging module.
# WHY: Lets us emit warnings (encoding fallback, skipped files) that calling
#      code can capture/filter, instead of using print() which can't be turned off.
import logging

# WHAT: The dataclass decorator, used below to build LoadedDocument.
# WHY: Auto-generates __init__/__repr__/__eq__ for a data-holding class —
#      same reasoning as DocumentChunk in schemas.py (Step 2.1).
from dataclasses import dataclass

# WHAT: Object-oriented filesystem path handling.
# WHY: Every function here takes/returns Path objects, not raw strings — Path
#      gives us .suffix, .rglob(), .resolve(), .relative_to() as methods
#      instead of manual string splitting.
from pathlib import Path

# WHAT: The PDF-reading class from the pypdf library.
# WHY: This is the actual engine that opens a .pdf file and exposes its pages.
from pypdf import PdfReader

# WHAT: pypdf's own exception type for malformed/unreadable PDF files.
# WHY: We need to catch THIS specific exception type to wrap it into our own
#      DocumentLoadError (see _read_pdf_file below) — catching generic
#      Exception would be too broad and could hide unrelated bugs.
from pypdf.errors import PdfReadError

# WHAT: Three functions/constants shared with schemas.py (Step 2.1).
# WHY: Reused, not reimplemented — EXCLUDED_DIR_NAMES, extract_title, and
#      infer_category must behave IDENTICALLY here and in schemas.py, so
#      importing the real ones guarantees they can never quietly drift apart
#      (see docs/loader_deep_dive.md, section 1.6, for the full reasoning).
from src.ingestion.schemas import EXCLUDED_DIR_NAMES, extract_title, infer_category

# WHAT: A logger instance scoped to this module's name ("src.ingestion.loader").
# WHY: Using __name__ (not a hardcoded string) means log output always shows
#      exactly which module emitted it, and lets callers configure logging
#      per-module if they ever want to (e.g. silence this module specifically).
logger = logging.getLogger(__name__)

# Every extension this loader knows how to read. Adding a new format means:
# 1. add its suffix here, 2. add a branch in `_extract_text`.
# WHAT: A set (not a list) of the three file extensions this module handles.
# WHY: Set membership (`in`) is O(1) and we never need ordering here — this
#      is purely a lookup table, used both to filter discover_documents and
#      to build the "unsupported extension" error message.
SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}


# WHAT: A custom exception class, subclassing the built-in Exception.
# WHY: Lets calling code catch failures FROM THIS MODULE specifically
#      (e.g. `except DocumentLoadError`) instead of a generic Exception,
#      and lets us attach the failing file's path into the message every
#      time one is raised (see docs/loader_deep_dive.md section 1.1).
class DocumentLoadError(Exception):
    """Raised when a specific file fails to load — wraps the underlying
    library exception (UnicodeDecodeError, PdfReadError, etc.) with the
    file path attached, so callers/logs can say WHICH file failed, not just
    that something, somewhere, failed.
    """


# WHAT: Decorator marking LoadedDocument as an immutable (frozen), memory-
#       efficient (slots) dataclass.
# WHY: Same discipline as DocumentChunk (schemas.py, Step 2.1) — this object
#      is meant to be a fixed, trustworthy fact about a file once built;
#      frozen=True stops any code from mutating it after construction,
#      slots=True avoids a per-instance __dict__ we never actually need.
@dataclass(frozen=True, slots=True)
class LoadedDocument:
    """One source file's worth of raw text, ready to be handed to a chunker.

    Deliberately much simpler than DocumentChunk (schemas.py): no chunk_id/
    doc_id/chunk_index here, because this document hasn't been chunked yet —
    those identity fields only make sense once a chunking strategy has
    decided how many chunks this document becomes, and in what order.
    """

    # WHAT: The file's path, relative to data/, forward-slash normalized.
    # WHY: Stored as a plain str (not a Path) so it's directly usable for
    #      citations/display and safely comparable/hashable across OSes.
    source_path: str
    """Path to the source file, relative to the data/ root, POSIX-style
    (forward slashes) — same convention as DocumentChunk.source_path."""

    # WHAT: The top-level data/ subfolder name this file came from.
    # WHY: Lets downstream code filter/group by category (e.g. "contracts
    #      only") without re-deriving it from source_path every time.
    category: str
    """Top-level data/ subfolder, e.g. 'contracts' — same meaning as
    DocumentChunk.category, computed the same way (schemas.infer_category)."""

    # WHAT: A human-readable title for the document.
    # WHY: Gives every LoadedDocument a display-friendly label, even for
    #      formats (.txt/.pdf) that have no markdown heading to extract.
    title: str
    """First H1 heading (markdown) or filename stem fallback. For .txt/.pdf,
    there's no markdown heading syntax to find, so this is almost always the
    filename stem for those formats — see extract_title's docstring."""

    # WHAT: The file's extension, without the leading dot.
    # WHY: Lets later code branch on source format (e.g. treat PDF-derived
    #      text with more suspicion) without re-parsing source_path's suffix.
    format: str
    """The file extension that was loaded, without the dot (e.g. 'pdf',
    'md', 'txt') — lets downstream code treat PDF-sourced text differently
    if extraction quality ever needs special-casing (see NOTES.md)."""

    # WHAT: The actual extracted text content.
    # WHY: This is the payload — everything else on this class is metadata
    #      ABOUT this field. Chunking (the next pipeline step) will read this.
    text: str
    """The extracted plain text content, ready for chunking."""

    # WHAT: The character length of `text`, computed once at construction.
    # WHY: A cheap, always-available signal for sanity-checking extraction —
    #      a near-zero count on a PDF is a strong hint something went wrong
    #      silently (see docs/loader_deep_dive.md section 1.5).
    char_count: int
    """len(text) — a cheap sanity check; a suspiciously small char_count for
    a PDF is a strong signal extraction silently failed/degraded (e.g. a
    scanned, image-only PDF with no embedded text layer at all)."""


# WHAT: Reads a single .txt or .md file's contents as a Python string.
# WHY: Both formats are "just text" — no special parsing needed at THIS
#      stage (markdown structure like headings is handled later/elsewhere),
#      so both share one function rather than two near-identical ones.
def _read_text_file(path: Path) -> str:
    """Read a .txt or .md file as text.

    TEACH — a real, easy-to-miss bug class: files are not guaranteed to be
    UTF-8. A file saved by an older tool, or edited on a different OS/locale,
    can be Latin-1 (cp1252 on Windows) instead. Reading UTF-8-only and
    letting `UnicodeDecodeError` propagate would silently kill an entire
    ingestion run over ONE mis-encoded file. We try UTF-8 first (the correct
    assumption for the vast majority of real files, including everything
    currently in this project's data/) and fall back to Latin-1 (which
    NEVER raises UnicodeDecodeError — every byte value is a valid Latin-1
    character — so it's a safe last-resort, at the cost of possibly
    mis-decoding non-Latin-1 bytes as the wrong characters).
    """
    # WHAT: Attempt to decode the file's bytes as UTF-8.
    # WHY: UTF-8 is the correct assumption for the vast majority of real
    #      files (including every file currently in this project's data/),
    #      so it's tried FIRST, not as an afterthought.
    try:
        return path.read_text(encoding="utf-8")
    # WHAT: Catch specifically a decode failure, not any/all exceptions.
    # WHY: Only a UnicodeDecodeError means "wrong encoding guessed" — any
    #      OTHER exception (e.g. file not found) should propagate normally,
    #      not be silently swallowed by an overly broad except clause.
    except UnicodeDecodeError:
        # WHAT: Log a warning naming the specific file that needed fallback.
        # WHY: The fallback is silent to the CALLER (still returns text
        #      successfully) but this is worth a operator-visible trace,
        #      since Latin-1 decoding CAN produce subtly wrong characters.
        logger.warning("File %s is not valid UTF-8 — falling back to latin-1", path)
        # WHAT: Retry the same read, forcing Latin-1 decoding this time.
        # WHY: Latin-1 maps every possible byte value to SOME character, so
        #      this read can never raise UnicodeDecodeError — a guaranteed-
        #      to-succeed last resort (see docs/loader_deep_dive.md, 1.4).
        return path.read_text(encoding="latin-1")


# WHAT: Extracts plain text from a .pdf file.
# WHY: PDFs need an entirely different extraction mechanism than plain text
#      files — this isolates that complexity in its own function.
def _read_pdf_file(path: Path) -> str:
    """Extract text from a PDF using pypdf.

    TEACH — a core RAG concept, not boilerplate: PDF text extraction is
    inherently lossy and layout-dependent. pypdf extracts text page by page,
    in roughly reading order for SIMPLE, single-column layouts — but multi-
    column pages, tables, and headers/footers can come out interleaved or
    duplicated, and a SCANNED (image-only) PDF with no embedded text layer
    at all will silently extract to an EMPTY or near-empty string (pypdf
    has no OCR capability — that would require a separate tool like
    `pytesseract`). We join pages with a blank line to at least preserve
    page boundaries as a readable signal, but no PDF library gives you
    perfect, guaranteed-correct text — see the practice exercises for how
    to actually verify extraction quality on a real file, not just assume it
    worked because no exception was raised.
    """
    # WHAT: Try to open/parse the PDF's internal structure.
    # WHY: `str(path)` because PdfReader expects a filename string/stream,
    #      not a Path object directly, in the pypdf API.
    try:
        reader = PdfReader(str(path))
    # WHAT: Catch pypdf's own "this PDF is malformed" exception type.
    # WHY: We want to wrap this into OUR exception type (DocumentLoadError)
    #      so callers only need to know about ONE exception type across
    #      every format this module supports, not pypdf's internals too.
    except PdfReadError as exc:
        # WHAT: Re-raise as DocumentLoadError, with the path and original
        #       error message embedded in the new message.
        # WHY: `from exc` preserves the original exception as `__cause__`,
        #      so a full traceback (both errors) is still available for
        #      debugging, while callers only need to catch DocumentLoadError.
        raise DocumentLoadError(f"Could not read PDF {path}: {exc}") from exc

    # WHAT: Extract text from every page, defaulting to "" if a page
    #       returns None instead of a string.
    # WHY: `page.extract_text()` can return None for certain malformed/
    #      edge-case pages in some pypdf scenarios — `or ""` guards against
    #      that so the list is always safely joinable strings, never None.
    pages_text = [page.extract_text() or "" for page in reader.pages]
    # WHAT: Join every page's text with a blank line between them.
    # WHY: Preserves page boundaries as a readable signal in the final text,
    #      at the cost of being ambiguous with genuine paragraph breaks
    #      within a page (see docs/loader_deep_dive.md, section 1.5, Ex. 3).
    return "\n\n".join(pages_text)


# WHAT: Routes a file to the correct extraction function based on its suffix.
# WHY: This is the ONE place "which extraction logic runs" is decided — every
#      caller just gets text back, without needing to know the dispatch rules.
def _extract_text(path: Path) -> str:
    """Dispatch to the right extraction function based on file extension."""
    # WHAT: Read the file's extension, normalized to lowercase.
    # WHY: `.lower()` makes dispatch case-INSENSITIVE — "Report.PDF" and
    #      "report.pdf" both correctly route to the PDF branch.
    suffix = path.suffix.lower()
    # WHAT: Route .txt and .md to the same plain-text reader.
    # WHY: Both formats need identical handling (no special parsing at this
    #      stage), so they intentionally share one function, not two.
    if suffix in {".txt", ".md"}:
        return _read_text_file(path)
    # WHAT: Route .pdf to the dedicated PDF extraction function.
    # WHY: PDFs need a fundamentally different extraction mechanism (see
    #      _read_pdf_file above), so this gets its own branch.
    if suffix == ".pdf":
        return _read_pdf_file(path)
    # WHAT: Raise a clear, actionable error for any other extension.
    # WHY: Fails loudly and immediately for an unsupported format, rather
    #      than silently skipping it or crashing with a confusing error
    #      later — the message names the exact suffix AND lists what IS
    #      supported, so the fix is obvious from the error alone.
    raise DocumentLoadError(
        f"Unsupported file extension {suffix!r} for {path} "
        f"(supported: {sorted(SUPPORTED_EXTENSIONS)})"
    )


# WHAT: Finds every loadable file under data_root.
# WHY: The single discovery step every other function in this module (and
#      load_all_documents specifically) builds on top of.
def discover_documents(data_root: Path) -> list[Path]:
    """All files under data_root matching SUPPORTED_EXTENSIONS, excluding
    noise directories (.ipynb_checkpoints, .git, __pycache__ — same
    exclusion list as schemas.discover_markdown_files, reused here so the
    two never drift out of sync).
    """
    # WHAT: Recursively walk data_root, filter to real files with a
    #       supported extension, excluding noise directories, then sort.
    # WHY: `rglob("*")` (not per-extension globs) walks the tree ONCE, with
    #      all filtering logic centralized here; `path.is_file()` excludes
    #      directories that might coincidentally match a supported suffix;
    #      `EXCLUDED_DIR_NAMES & set(path.parts)` reuses schemas.py's noise-
    #      folder list (see the import at the top of this file);
    #      `sorted(...)` gives deterministic, reproducible ordering.
    return sorted(
        path
        for path in data_root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not EXCLUDED_DIR_NAMES & set(path.parts)
    )


# WHAT: Loads exactly one file into a fully-populated LoadedDocument.
# WHY: The single "correct" way to construct a LoadedDocument — mirrors
#      schemas.py's make_chunk() being the sole DocumentChunk constructor.
def load_document(source_path: Path, data_root: Path) -> LoadedDocument:
    """Load one file into a LoadedDocument. Raises DocumentLoadError (or lets
    a category/path error from infer_category propagate) if the file can't
    be read.
    """
    # WHAT: Extract the file's raw text via format dispatch.
    # WHY: Delegates the "which format, which library" decision entirely to
    #      _extract_text — this function doesn't need to know the details.
    text = _extract_text(source_path)
    # WHAT: Compute source_path's location relative to data_root.
    # WHY: `.resolve()` on both sides first converts each to an absolute,
    #      canonical form, so this works correctly whether source_path was
    #      passed in as relative or absolute (see docs/schemas_deep_dive.md
    #      section 1.7 for why .resolve() matters here).
    relative_path = source_path.resolve().relative_to(data_root.resolve())
    # WHAT: Convert the relative path to a string with forward slashes.
    # WHY: Ensures source_path is stored the SAME way (POSIX-style)
    #      regardless of whether this runs on Windows or Linux — matching
    #      DocumentChunk's convention in schemas.py.
    source_path_str = str(relative_path).replace("\\", "/")
    # WHAT: Determine the file's category from its containing folder.
    # WHY: Reuses schemas.py's infer_category so this NEVER disagrees with
    #      what DocumentChunk would compute for the same file.
    category = infer_category(source_path, data_root)
    # WHAT: Extract a title from the text, falling back to the filename.
    # WHY: Reuses schemas.py's extract_title — same H1-then-fallback logic
    #      chunking will eventually use too.
    title = extract_title(text, fallback=source_path.stem)
    # WHAT: Get the file's extension without the leading dot.
    # WHY: `.lstrip(".")` turns ".pdf" into "pdf" — LoadedDocument.format
    #      is documented/used as the bare extension, not the dotted suffix.
    fmt = source_path.suffix.lower().lstrip(".")

    # WHAT: Assemble and return the final, immutable LoadedDocument.
    # WHY: One single construction point (like make_chunk in schemas.py)
    #      guarantees every field is computed consistently, every time.
    return LoadedDocument(
        source_path=source_path_str,
        category=category,
        title=title,
        format=fmt,
        text=text,
        char_count=len(text),
    )


# WHAT: Loads every discovered file under data_root, with a caller-chosen
#       policy for what happens if one file fails to load.
# WHY: The top-level batch entrypoint most real ingestion code will call.
def load_all_documents(
    data_root: Path, *, skip_errors: bool = False
) -> tuple[list[LoadedDocument], list[tuple[Path, Exception]]]:
    """Load every supported file under data_root.

    TEACH — a real ingestion-pipeline design question: should ONE bad file
    (corrupted PDF, permission error) abort the ENTIRE ingestion run, or
    should ingestion best-effort skip it and keep going? Neither answer is
    universally "correct" — failing fast is safer for a small, curated
    knowledge base where every document matters and silent gaps are
    dangerous; skipping is more practical for a large, messy corpus where
    one bad file blocking everything else is worse than a documented gap.
    `skip_errors=False` (the default) fails fast — raises immediately on the
    first bad file. `skip_errors=True` collects failures into the second
    return value instead of raising, so YOU decide, per call site, which
    behavior fits — this module doesn't decide for you.
    """
    # WHAT: Two accumulator lists — successes and (path, exception) failures.
    # WHY: Explicit type annotations here help mypy (and readers) know
    #      exactly what these lists will hold before any values exist yet.
    documents: list[LoadedDocument] = []
    errors: list[tuple[Path, Exception]] = []

    # WHAT: Iterate every file discover_documents finds.
    # WHY: This is the single loop that turns "list of paths" into "list of
    #      loaded documents (and/or recorded failures)".
    for path in discover_documents(data_root):
        # WHAT: Attempt to load this one file.
        try:
            documents.append(load_document(path, data_root))
        # WHAT: Catch only the exception types we know how to handle.
        # WHY: DocumentLoadError (from _extract_text/_read_pdf_file) and
        #      OSError (e.g. permission errors) are the realistic failure
        #      modes for one file; anything else (a genuine bug) should
        #      still propagate normally, not be silently absorbed here.
        except (DocumentLoadError, OSError) as exc:
            # WHAT: If skip_errors is False (the default), re-raise now.
            # WHY: Fail-fast is the SAFER default — a silently incomplete
            #      knowledge base is a worse failure mode than stopping
            #      immediately and making the problem visible.
            if not skip_errors:
                raise
            # WHAT: Otherwise, log a warning naming the file and the error.
            # WHY: Even in best-effort mode, a skipped file should leave a
            #      visible trace, not disappear silently.
            logger.warning("Skipping %s due to load error: %s", path, exc)
            # WHAT: Record the failure instead of the loaded document.
            # WHY: Lets the caller inspect exactly which files failed and
            #      why, via the second return value, rather than only
            #      knowing a smaller-than-expected count of documents.
            errors.append((path, exc))

    # WHAT: Return both accumulated lists as a tuple.
    # WHY: The caller decides what to do with successes vs. failures —
    #      this function only collects, it never decides policy beyond
    #      the fail-fast/best-effort switch itself.
    return documents, errors
