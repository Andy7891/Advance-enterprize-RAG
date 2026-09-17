# Pre-flight: the same mistake found before vs after the expensive stage (live, harmless).
# Mistake used: running with a changed document prefix against the real 'chunks' collection.
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

log = Path(tempfile.gettempdir()) / "dd15_preflight_log.json"
env = {**os.environ, "EMBED_DOCUMENT_PREFIX": ""}          # the mistake

started = time.perf_counter()
result = subprocess.run([sys.executable, "-m", "src.vectorstore.ingest", "--log", str(log)],
                        env=env, capture_output=True, text=True)
elapsed = time.perf_counter() - started
print(f"WITH preflight : exit code {result.returncode} after {elapsed:.1f}s (includes Python start-up)")
print("                ", result.stdout.strip().splitlines()[-2].strip()[:150])

# Without preflight the same run would only fail (if at all) after embedding every chunk.
import json
real = json.loads(Path("data/ingestion_log.json").read_text(encoding="utf-8"))
print(f"WITHOUT        : the embed stage alone took {real['stage_seconds']['embed']:.1f}s in the last real run,"
      " and nothing would have stopped the write afterwards")
