# The toy embedding model, step by step: words -> slots -> counts -> unit vector -> cosine.
import hashlib
import math
import re
import subprocess
import sys

# Run from the project root: the `tests` package is importable from there, not from this folder.
sys.path.insert(0, ".")
from tests.support import BagOfWordsClient  # noqa: E402

# Step 1: Python's built-in hash() changes between processes; hashlib does not
code = "import hashlib; print(hash('acme') % 768, int(hashlib.md5(b'acme').hexdigest(), 16) % 768)"
for seed in ("1", "2"):
    out = subprocess.run([sys.executable, "-c", code], env={"PYTHONHASHSEED": seed, "SYSTEMROOT": "C:\\Windows"},
                         capture_output=True, text=True).stdout.split()
    print(f"1. PYTHONHASHSEED={seed}: hash('acme') % 768 = {out[0]:>3} | md5('acme') % 768 = {out[1]}")

# Step 2: which slot each word lands in, and which words are ignored
model = BagOfWordsClient()
text = "search_document: Acme was founded in 2015 by Ada Lovelace."
words = re.findall(r"[a-z0-9]+", text.lower())
for w in words:
    slot = "ignored" if w in model.IGNORED else int(hashlib.md5(w.encode()).hexdigest(), 16) % 768
    print(f"2. {w!r:<12} -> {slot}")

# Step 3: counts become a unit vector
v = model.vector(text)
nonzero = {i: round(x, 3) for i, x in enumerate(v) if x}
print(f"3. non-zero slots: {nonzero} | length = {math.sqrt(sum(x * x for x in v)):.3f}")


def cosine(a, b):
    return sum(x * y for x, y in zip(model.vector(a), model.vector(b)))


# Step 4: similarity is word overlap, nothing more
q = "search_query: Who founded Acme?"
for doc in ["Acme was founded in 2015 by Ada Lovelace.", "Acme employs thirty people across two offices.",
            "Widget pricing starts at ten dollars per unit."]:
    print(f"4. cos(question, {doc[:34]!r:<37}) = {cosine(q, 'search_document: ' + doc):.3f}")

# Step 5: the known blind spot -- synonyms share no words
print(f"5. cos('How much does a widget cost?', 'Widget pricing starts...') = "
      f"{cosine('How much does a widget cost?', 'Widget pricing starts at ten dollars'):.3f} (only 'widget' shared)")

# Step 6: collisions -- different words landing in the same slot
corpus_words = set(re.findall(r"[a-z0-9]+", open("data/company/about.md", encoding="utf-8").read().lower()))
slots = {}
for w in corpus_words - model.IGNORED:
    slots.setdefault(int(hashlib.md5(w.encode()).hexdigest(), 16) % 768, []).append(w)
shared = {s: ws for s, ws in slots.items() if len(ws) > 1}
print(f"6. about.md: {len(corpus_words - model.IGNORED)} distinct words -> {len(slots)} slots; "
      f"{len(shared)} slots shared, e.g. {list(shared.values())[:2]}")
