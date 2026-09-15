from pathlib import Path

import pytest

from src.ingestion.loader import (
    DocumentLoadError,
    discover_documents,
    load_all_documents,
    load_document,
)

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def test_discover_documents_finds_real_markdown_files():
    files = discover_documents(DATA_ROOT)
    assert files, "expected at least one supported file under data/"
    assert all(f.suffix.lower() in {".txt", ".md", ".pdf"} for f in files)


def test_discover_documents_excludes_ipynb_checkpoints():
    files = discover_documents(DATA_ROOT)
    assert not any(".ipynb_checkpoints" in f.parts for f in files)


def test_load_document_on_real_markdown_file():
    source = DATA_ROOT / "company" / "about.md"
    doc = load_document(source, DATA_ROOT)

    assert doc.source_path == "company/about.md"
    assert doc.category == "company"
    assert doc.format == "md"
    assert doc.title == "About Insurellm"
    assert doc.char_count == len(doc.text)
    assert "Insurellm" in doc.text


def test_load_document_raises_on_unsupported_extension(tmp_path):
    bad_file = tmp_path / "data" / "company" / "notes.docx"
    bad_file.parent.mkdir(parents=True)
    bad_file.write_text("irrelevant")

    with pytest.raises(DocumentLoadError):
        load_document(bad_file, tmp_path / "data")


def test_load_document_falls_back_to_latin1_on_bad_utf8(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "company").mkdir(parents=True)
    bad_file = data_root / "company" / "weird.txt"
    # 0x92 is a right-single-quote in cp1252/latin-1 but invalid as a UTF-8
    # continuation byte on its own -- a realistic "not actually UTF-8" file.
    bad_file.write_bytes(b"Bob\x92s notes")

    doc = load_document(bad_file, data_root)
    assert doc.format == "txt"
    assert doc.char_count == len(bad_file.read_bytes())


def _make_minimal_pdf(path: Path, text: str) -> None:
    """Build a tiny, real, valid single-page PDF containing `text`, by hand,
    including a correct xref table/trailer with byte-accurate offsets.

    pypdf's own PdfWriter can create blank pages but has no text-drawing API
    (that's normally reportlab's job, which we don't want as a test-only
    dependency) -- so this hand-writes the minimal PDF object structure
    needed for one page of readable text. A PDF without a valid xref table
    (an earlier draft of this fixture omitted one) reliably fails to parse
    -- pypdf's lenient-recovery mode does NOT cover every malformed case, so
    getting the offsets right here is what makes this fixture actually work.
    """
    content = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
        b"/Resources<</Font<</F1 5 0 R>>>>/Contents 4 0 R>>",
        f"<</Length {len(content)}>>stream\n".encode("latin-1") + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]

    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += f"{i} 0 obj".encode("latin-1") + obj + b"endobj\n"

    xref_offset = len(body)
    count = len(objects) + 1
    body += f"xref\n0 {count}\n".encode("latin-1")
    body += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        body += f"{offset:010d} 00000 n \n".encode("latin-1")
    body += f"trailer<</Size {count}/Root 1 0 R>>\n".encode("latin-1")
    body += f"startxref\n{xref_offset}\n%%EOF".encode("latin-1")

    path.write_bytes(bytes(body))


def test_load_document_extracts_real_pdf_text(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "products").mkdir(parents=True)
    pdf_path = data_root / "products" / "sample.pdf"
    _make_minimal_pdf(pdf_path, "Hello PDF World")

    doc = load_document(pdf_path, data_root)

    assert doc.format == "pdf"
    assert doc.category == "products"
    assert "Hello PDF World" in doc.text


def test_load_all_documents_skip_errors_collects_failures(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "company").mkdir(parents=True)
    (data_root / "company" / "good.md").write_text("# Good\nFine content.")
    bad_pdf = data_root / "company" / "broken.pdf"
    bad_pdf.write_bytes(b"not a real pdf")

    documents, errors = load_all_documents(data_root, skip_errors=True)

    assert len(documents) == 1
    assert documents[0].source_path == "company/good.md"
    assert len(errors) == 1
    assert errors[0][0] == bad_pdf


def test_load_all_documents_raises_by_default(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "company").mkdir(parents=True)
    (data_root / "company" / "broken.pdf").write_bytes(b"not a real pdf")

    with pytest.raises(DocumentLoadError):
        load_all_documents(data_root)


def test_load_all_documents_on_real_data_root():
    # 76, not 82: data/ has 82 total .md files on disk, but 6 of those live
    # under .ipynb_checkpoints/ duplicate folders, which are deliberately
    # excluded (see EXCLUDED_DIR_NAMES) -- 76 is the real, de-duplicated count.
    documents, errors = load_all_documents(DATA_ROOT, skip_errors=True)
    assert len(documents) == 76
    assert errors == []
    assert {d.category for d in documents} == {"company", "contracts", "employees", "products"}
