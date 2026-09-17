# Contract on disk: dataclass -> dict -> JSON -> dict -> dataclass, and two glob pitfalls.
import dataclasses
import json
import tempfile
from pathlib import Path

from src.ingestion.pipeline import REPORT_FILENAME, read_chunks, write_chunks
from src.ingestion.schemas import DocumentChunk

chunk = read_chunks()[0]

# Step 1: dataclass -> plain dict (nested dicts included)
record = dataclasses.asdict(chunk)
print("1. asdict keys:", list(record))

# Step 2: dict -> JSON text -> dict (what write_chunks / read_chunks do)
text = json.dumps(record)
back = json.loads(text)
print("2. JSON is", len(text), "chars; types after reload:", type(back["chunk_index"]).__name__, type(back["metadata"]).__name__)

# Step 3: dict -> dataclass via keyword unpacking, and equality by value
rebuilt = DocumentChunk(**back)
print("3. rebuilt == original:", rebuilt == chunk)

# Step 4: an unknown key is rejected, so a schema drift in the file is loud
try:
    DocumentChunk(**{**back, "embedding": [0.1]})
except TypeError as e:
    print("4. extra key ->", e)

# Step 5: the two glob pitfalls read_chunks avoids
with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    write_chunks([chunk], d / "company__about.md.json")
    write_chunks([dataclasses.replace(chunk, source_path="company/memo.txt")], d / "company__memo.txt.json")
    (d / REPORT_FILENAME).write_text(json.dumps({"chunks_written": 2}), encoding="utf-8")

    print("5a. glob('*.md.json') finds", len(list(d.glob("*.md.json"))), "file(s) -> the .txt chunks would be lost")
    try:
        [DocumentChunk(**r) for p in d.glob("*.json") for r in json.loads(p.read_text("utf-8"))]
    except TypeError as e:
        print("5b. glob('*.json') including the report ->", type(e).__name__, e)
    print("5c. read_chunks finds", len(read_chunks(d)), "chunks")
