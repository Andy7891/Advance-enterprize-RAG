"""Proves check_api_contract.py can FAIL, by running it against deliberately broken copies of the app.

For every mutant in mutant_stub.py it starts the mutated app, runs the whole deterministic checker
against it, and compares the clauses that went red with the ones that bug SHOULD break. A mutant
that leaves the checker green is a hole in the checker; a clause that goes red for a bug it has
nothing to do with is noise. Both make this script exit 1.

  python run_mutants.py            run every mutant (about two minutes)
  python run_mutants.py cors-on    run only the named ones
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from check_api_contract import HERE, start_stub

EXPECTED_RED: dict[str, set[str]] = {
    "(baseline: no mutation)": set(),
    # an unmutated app must leave every clause green
    "old-framing": {"S2", "S6"},
    # the original defect: a lone CR is left inside a data line
    "splitlines-fix": {"S6"},
    # the obvious wrong repair: U+2028 split, trailing newline dropped
    "buffers-everything": {"S5", "S10"},
    # the whole answer is generated before any of it is sent; S10 also goes red, because its
    # premise -- a stream held open mid-generation -- no longer exists to test against
    "blocks-the-loop": {"S5", "S10"},
    # the blocking generator runs on the event loop: other requests stall (S10), and on Windows'
    # proactor loop even this stream's own events are only flushed once the loop is free (S5)
    "default-http-errors": {"E3", "E4"},
    # FastAPI's stock {"detail": ...} body for 404 / 405
    "default-422-docs": {"A2", "E2", "X1"},
    # /openapi.json documents the stock 422 schema again, which accepts any object
    "cors-on": {"X2"},
    # every origin allowed
    "closes-abandoned-streams": {"S12"},
    # not a bug: the candidate fix for gap G1, so the known gap must flip to FAIL ("gap is gone")
}


def run_one(name: str) -> tuple[set[str], str]:
    """Starts the mutant `name`, runs the checker against it, returns (red clauses, raw output)."""
    yield_log = Path(tempfile.mkstemp(suffix=".log", prefix="mutant_yields_")[1])
    mutant = "" if name.startswith("(") else name
    server, base_url, _ = start_stub(yield_log, "mutant_stub:app", {"MUTANT": mutant})
    try:
        done = subprocess.run(
            [
                sys.executable,
                str(HERE / "check_api_contract.py"),
                *["--tier", "deterministic", "--base-url", base_url, "--yield-log", str(yield_log)],
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
    finally:
        server.terminate()
        server.wait(timeout=15)
    verdicts = re.findall(r"^(PASS|FAIL|GAP|SKIP)\s+(\S+)", done.stdout, flags=re.MULTILINE)
    return {cid for verdict, cid in verdicts if verdict == "FAIL"}, done.stdout


def main() -> int:
    wanted = sys.argv[1:]
    names = [n for n in EXPECTED_RED if not wanted or n in wanted or n.startswith("(baseline")]
    print(f"{'mutant':<28}{'should go red':<18}{'went red':<18}result")
    bad = 0
    for name in names:
        red, _ = run_one(name)
        expected = EXPECTED_RED[name]
        result = "OK" if red == expected else ("MISSED" if expected - red else "NOISE")
        bad += result != "OK"
        print(
            f"{name:<28}{', '.join(sorted(expected)) or '-':<18}"
            f"{', '.join(sorted(red)) or '-':<18}{result}",
            flush=True,
        )
    print(f"=== {len(names) - bad} of {len(names)} as expected ===")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
