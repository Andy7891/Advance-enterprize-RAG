# Full vs incremental ingestion: how change detection works, and how mtime lies.
import dataclasses
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from src.ingestion.pipeline import read_chunks

chunks = read_chunks()
by_doc = {}
for c in chunks:
    by_doc.setdefault(c.source_path, []).append(c)


def fingerprint(doc_chunks):
    # hash of everything that affects the stored vectors: the ordered chunk texts
    h = hashlib.sha256()
    for c in sorted(doc_chunks, key=lambda c: c.chunk_index):
        h.update(c.text.encode("utf-8"))
        h.update(b"\x00")                      # separator, so "ab"+"c" != "a"+"bc"
    return h.hexdigest()[:16]


# 1. "Last run" fingerprints, then pretend one document was edited
last_run = {path: fingerprint(cs) for path, cs in by_doc.items()}
edited = "company/about.md"
by_doc[edited][0] = dataclasses.replace(by_doc[edited][0], text=by_doc[edited][0].text + " Edited.")
changed = [p for p, cs in by_doc.items() if fingerprint(cs) != last_run[p]]
print(f"1. documents: {len(by_doc)} | chunks: {len(chunks)}")
print(f"   FULL run embeds        : {len(chunks)} chunks")
print(f"   INCREMENTAL run embeds : {sum(len(by_doc[p]) for p in changed)} chunks from changed documents {changed}")

# 2. Why file modification time is a weak signal: copying changes it, content does not change
tmp = Path(tempfile.mkdtemp())
src = Path("data/company/about.md")
copy = tmp / "about.md"
shutil.copy(src, copy)                          # shutil.copy does NOT preserve mtime
same_content = hashlib.sha256(src.read_bytes()).hexdigest() == hashlib.sha256(copy.read_bytes()).hexdigest()
print(f"2. copied file: content identical={same_content}, mtime changed={os.path.getmtime(copy) != os.path.getmtime(src)}")

# 3. And the reverse: content changes but mtime is set back (e.g. restored by a tool)
before = os.path.getmtime(copy)
copy.write_text(copy.read_text(encoding="utf-8") + "\nNew line.", encoding="utf-8")
os.utime(copy, (before, before))
print(f"3. edited file with restored mtime: mtime changed={os.path.getmtime(copy) != before}, "
      f"content changed={hashlib.sha256(copy.read_bytes()).hexdigest() != hashlib.sha256(src.read_bytes()).hexdigest()}")
shutil.rmtree(tmp)
