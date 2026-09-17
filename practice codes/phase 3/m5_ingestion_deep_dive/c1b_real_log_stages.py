# Read the REAL run log written by `python -m src.vectorstore.ingest` and see where the time went.
import json
from pathlib import Path

log = json.loads(Path("data/ingestion_log.json").read_text(encoding="utf-8"))
total = sum(log["stage_seconds"].values())
print(f"run {log['started_at']} -> {log['finished_at']}  status={log['status']}  verified={log['verified']}")
for name, seconds in log["stage_seconds"].items():
    bar = "#" * max(1, round(40 * seconds / total))
    print(f"  {name:<9} {seconds:>8.3f}s  {100 * seconds / total:5.1f}%  {bar}")
print(f"  {'total':<9} {total:>8.3f}s")
print("chunks per second while embedding:", round(log["chunks_on_disk"] / log["stage_seconds"]["embed"], 2))
