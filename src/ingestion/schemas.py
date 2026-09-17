"""Contract 1: the DocumentChunk data contract, plus the utility functions that
construct it from a raw markdown file on disk.

Every later ingestion/retrieval module (chunking strategy, embedding, Qdrant upsert,
Neo4j graph linking, citation rendering in the UI) consumes DocumentChunk objects —
this file is the single place that shape is defined.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Folders under data/ that are NOT real content — excluded from ingestion.
# .ipynb_checkpoints in particular is Jupyter's autosave directory; it silently
# duplicates every file you've opened in a notebook, which would double-count
# content in the knowledge base if not filtered out.
EXCLUDED_DIR_NAMES = {".ipynb_checkpoints", ".git", "__pycache__"}


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """One retrievable unit of text, plus everything needed to trace, filter,
    and re-ingest it correctly.

    Frozen (immutable) on purpose: text, char_count and approx_token_count are
    computed together in `make_chunk`. If the dataclass were mutable, code
    elsewhere could edit `.text` after construction and leave the counts (and
    any vector already made from it) silently describing different content.
    Immutability makes that an error instead of a silent data-integrity bug. If
    you need a "changed" chunk, construct a new one (typically via `make_chunk`).
    """

    chunk_id: str
    """"<source_path>_<chunk_index>" — derived from POSITION, not content (see
    `compute_chunk_id`). Re-ingesting reproduces the same IDs, so an upsert
    overwrites instead of duplicating; but re-chunking reuses the same IDs for
    different text and can leave surplus old chunks behind, which is why the
    upsert layer must delete a document's chunks by doc_id first."""

    doc_id: str
    """Deterministic hash of the source file's path (relative to data/).
    Stable across re-ingestion, groups all chunks that came from one file —
    used later to delete/replace all of a file's chunks when it's edited."""

    source_path: str
    """Path to the source .md file, relative to the data/ root (e.g.
    'contracts/Contract with DriveSmart Insurance for Carllm.md'). Used for
    citations in the UI and for debugging retrieval quality."""

    category: str
    """Top-level data/ subfolder this file came from (e.g. 'contracts',
    'employees'). Enables filtered/hybrid search later (e.g. "only search
    contracts") without needing a separate classification step."""

    title: str
    """Document title — the file's first '# H1' heading, or the filename
    stem if no H1 exists. Human-readable context alongside the chunk text."""

    chunk_index: int
    """0-based position of this chunk within its source document. Lets you
    reconstruct chunk order, or fetch a chunk's neighbors for expanded
    context at query time."""

    text: str
    """The actual chunk content that gets embedded and retrieved."""

    char_count: int
    """len(text) at construction time — cheap sanity check for chunking
    strategy (e.g. "are my chunks the size I intended?")."""

    approx_token_count: int
    """A cheap, provider-agnostic ESTIMATE of token count (see
    `count_tokens_approx` below for why this is an approximation, not an
    exact count). Used for chunk-sizing decisions relative to an embedding
    model's context limit."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Escape hatch for anything that doesn't deserve a first-class field yet
    (e.g. later: extracted entity names for the Neo4j graph). Keep first-class
    fields for things every chunk has and downstream code will filter/sort by
    often; use metadata for everything else."""


@dataclass(frozen=True, slots=True)
class Embedding:
    """Contract 2: one chunk's vector, plus the provenance needed to trust it.

    Carries the whole chunk rather than just its chunk_id so the upsert step
    receives the vector and its payload as one object — there is no second list
    to join back by ID, and therefore no way to attach a vector to the wrong
    chunk.
    """

    chunk: DocumentChunk

    vector: tuple[float, ...]
    """A tuple rather than a list so the frozen dataclass is immutable all the
    way down; a list could be edited in place after validation."""

    model: str

    model_digest: str
    """Vectors are only comparable with vectors from the same weights. Recording
    the digest on every vector makes a mixed-model collection detectable after
    the fact, instead of only preventable at startup."""

    document_prefix: str
    """The task prefix is part of what was embedded, so it is as much a part of
    the vector's identity as the model is — and unlike the model, nothing else
    records it."""


def compute_doc_id(source_path: str) -> str:
    """Deterministic ID for a source file, derived from its path alone.

    TEACH: why hash the PATH, not the file's content, for doc_id specifically?
    Because doc_id's job is to group chunks belonging to "the same document
    across re-ingestion runs" — even after the file's content is edited, we
    still want new chunks to replace the OLD chunks of the same document, not
    become an unrelated new document. If doc_id were content-based, editing a
    contract file would silently orphan its old chunks in Qdrant (nothing
    would know to delete them) instead of them being findable/replaceable by
    doc_id. Content changes are tracked at the chunk level via chunk_id
    instead (see compute_chunk_id).
    """
    normalized = source_path.replace("\\", "/").strip("/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def compute_chunk_id(source_path: str, chunk_index: int) -> str:
    """Human-readable chunk ID: "<source_path>_<index>", e.g.
    "company/about.md_3".

    DANGER — read before changing chunking parameters. This ID is derived
    from POSITION ONLY, never content. Re-chunking the same file with
    different settings reuses the same IDs for entirely different text, so a
    vector-store upsert silently replaces chunk N's meaning. Worse, if the
    new run produces FEWER chunks than the old one, the surplus old chunks
    (e.g. _5.._7) are never overwritten and linger forever, so retrieval can
    still return text that no longer exists in the source.

    The guard that makes this safe: callers MUST delete every existing chunk
    for a document (query by doc_id) before re-ingesting it. That guard
    belongs in the vector-store upsert layer, which does not exist yet — it
    is the single most important thing to remember when building it.

    Full source path (not just the filename) is used so that two files with
    the same name in different categories cannot collide.
    """
    normalized = source_path.replace("\\", "/").strip("/")
    return f"{normalized}_{chunk_index}"


def infer_category(source_path: Path, data_root: Path) -> str:
    """The top-level data/ subfolder a file lives in, e.g. 'contracts'.

    Raises ValueError if source_path isn't actually under data_root, or if a
    file sits directly in data_root with no category subfolder — both are
    treated as a real ingestion-time configuration error, not silently
    ignored, since every chunk MUST have a category for filtering to work.
    """
    try:
        relative = source_path.resolve().relative_to(data_root.resolve())
    except ValueError as exc:
        raise ValueError(f"{source_path} is not inside data root {data_root}") from exc

    if len(relative.parts) < 2:
        raise ValueError(
            f"{source_path} has no category subfolder under {data_root} "
            "(files must live in e.g. data/<category>/file.md)"
        )
    return relative.parts[0]


def extract_title(text: str, fallback: str) -> str:
    """First '# Heading' line in the markdown, or `fallback` (typically the
    filename stem) if the file has no H1.
    """
    match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return fallback


def count_tokens_approx(text: str) -> int:
    """Cheap, offline, provider-agnostic token count ESTIMATE.

     — a real trade-off, not a shortcut: an exact token count needs a
    specific tokenizer (e.g. OpenAI's `tiktoken`, which encodes text into the
    literal token IDs a given model would see). That's more accurate but
    couples this schema module to one provider's tokenizer AND, for some
    encodings, requires a one-time network fetch of encoding data on first
    use — a bad fit for a project meant to build/test offline and stay
    provider-agnostic (recall Step 1.1: Cohere for reranking, Ollama for
    local inference — this codebase deliberately isn't OpenAI-only).
    `len(text) / 4` is a widely-used rule of thumb for English text (roughly
    4 characters per token) — good enough to catch "this chunk is wildly too
    big," not precise enough to catch off-by-one limits. See the practice
    exercises below for swapping in tiktoken and measuring the real error.
    """
    return max(1, len(text) // 4)


def discover_markdown_files(data_root: Path) -> list[Path]:
    """All *.md files under data_root, excluding noise directories like
    .ipynb_checkpoints (see EXCLUDED_DIR_NAMES).
    """
    return sorted(
        path for path in data_root.rglob("*.md") if not EXCLUDED_DIR_NAMES & set(path.parts)
    )


def make_chunk(
    *,
    source_path: Path,
    data_root: Path,
    title: str,
    chunk_index: int,
    text: str,
    start_char: int | None = None,
    end_char: int | None = None,
) -> DocumentChunk:
    """Construct a fully-populated, correctly-ID'd DocumentChunk.

    This is the one place chunk construction should happen from — it's what
    guarantees chunk_id/doc_id are always computed consistently, rather than
    every call site re-implementing the ID logic (and risking drift).

    start_char/end_char are offsets into the ORIGINAL LoadedDocument.text
    (not into any intermediate cleaned/normalized copy), so a UI can
    highlight the exact source span a retrieved answer came from. They land
    in `metadata` rather than as first-class fields: a deliberate trade-off
    accepting weaker typing to avoid widening the frozen Contract 1 field
    list (see decision.md, D-2026-09-15-02). They are omitted entirely when
    not supplied, so `"start_char" in chunk.metadata` is a meaningful check
    rather than always-present-but-maybe-None.
    """
    relative_path = source_path.resolve().relative_to(data_root.resolve())
    source_path_str = str(relative_path).replace("\\", "/")
    doc_id = compute_doc_id(source_path_str)
    chunk_id = compute_chunk_id(source_path_str, chunk_index)
    category = infer_category(source_path, data_root)

    metadata: dict[str, Any] = {}
    if start_char is not None:
        metadata["start_char"] = start_char
    if end_char is not None:
        metadata["end_char"] = end_char

    return DocumentChunk(
        chunk_id=chunk_id,
        doc_id=doc_id,
        source_path=source_path_str,
        category=category,
        title=title,
        chunk_index=chunk_index,
        text=text,
        char_count=len(text),
        approx_token_count=count_tokens_approx(text),
        metadata=metadata,
    )
