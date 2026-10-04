"""P8-M3 mutation check: plant ONE small bug at a time in the new code, run the tests, restore the file.

    python "practice codes/phase 8/m3_ui_configuration/mutate_p8m3.py"

A test suite that has never failed has never been shown to work. This script edits three source files
(src/ui/endpoint.py, src/ui/app.py, src/core/config.py) one deliberate bug at a time, runs
tests/ui/test_endpoint.py and tests/ui/test_app.py with `-x`, and reports the first test that failed.
"SURVIVED" would mean a bug no test noticed.

Safety: each file is restored from its original bytes in a `finally`, and the script compares file hashes
at the end and exits non-zero if anything differs. Do not edit those three files, and do not run other
tests, while it runs (about four minutes): they would see the planted bugs.
"""

import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# the repository root: practice codes/phase 8/m3_ui_configuration/ is three folders below it
PY = sys.executable
ENDPOINT = ROOT / "src" / "ui" / "endpoint.py"
APP = ROOT / "src" / "ui" / "app.py"
CONFIG = ROOT / "src" / "core" / "config.py"
TESTS = ["tests/ui/test_endpoint.py", "tests/ui/test_app.py"]

MUTATIONS = [
    # (label, file, old text, replacement) -- the old text must occur exactly once
    ("endpoint: trailing slashes not stripped", ENDPOINT, 'path = parts.path.rstrip("/")', "path = parts.path"),
    ("endpoint: scheme not checked", ENDPOINT, 'if parts.scheme not in ("http", "https"):', "if False:"),
    ("endpoint: password or token echoed back", ENDPOINT, 'if any(mark in value for mark in "@?#"):', "if False:"),
    ("endpoint: a token after ? or # echoed back", ENDPOINT, 'if any(mark in value for mark in "@?#"):', 'if "@" in value:'),
    ("endpoint: restart reminder dropped", ENDPOINT, '"then restart streamlit run (settings are read once, when it starts)."', '"."'),
    ("endpoint: spaces/control chars allowed", ENDPOINT, "if any(character.isspace() or not character.isprintable() for character in text):", "if False:"),
    ("endpoint: query/fragment allowed", ENDPOINT, 'if "?" in text or "#" in text:', "if False:"),
    ("endpoint: credentials allowed", ENDPOINT, 'if "@" in parts.netloc:', "if False:"),
    ("endpoint: missing host allowed", ENDPOINT, "if not parts.hostname:", "if False:"),
    ("endpoint: port never checked", ENDPOINT, "if port is not None and not 1 <= port <= MAX_PORT:", "if False:"),
    ("endpoint: port 0 allowed", ENDPOINT, "not 1 <= port <= MAX_PORT", "not 0 <= port <= MAX_PORT"),
    ("endpoint: pasted endpoint allowed", ENDPOINT, "if last_segment in PATHS_THE_CLIENT_ADDS:", "if False:"),
    ("endpoint: /health not recognised", ENDPOINT, 'PATHS_THE_CLIENT_ADDS = ("/chat", "/health")', 'PATHS_THE_CLIENT_ADDS = ("/chat",)'),
    ("endpoint: scheme keeps the typed case", ENDPOINT, 'return f"{parts.scheme}://{parts.netloc}{path}"', 'return f"{text[: len(parts.scheme)]}://{parts.netloc}{path}"'),
    ("endpoint: value not stripped", ENDPOINT, "text = value.strip()", "text = value"),
    ("endpoint: empty value not recognised", ENDPOINT, "if not text:", "if False:"),
    ("endpoint: setting ignored (hard-coded)", ENDPOINT, "return normalize_api_url(settings.api_url)", 'return normalize_api_url("http://localhost:8000")'),
    ("page: config error does not stop the page", APP, "        st.stop()", "        pass"),
    ("page: config error text not escaped", APP, "st.error(as_plain_text(problem.message))", "st.error(problem.message)"),
    ("page: ask() gets the built-in default", APP, "stream = client.ask(api_url, question, k)", 'stream = client.ask("http://localhost:8000", question, k)'),
    ("page: health check gets the built-in default", APP, "health = client.check_health(api_url)", 'health = client.check_health("http://localhost:8000")'),
    ("page: sidebar shows the built-in default", APP, 'st.success(f"API reachable at {api_url}")', 'st.success("API reachable at http://localhost:8000")'),
    ("config: default changed", CONFIG, 'api_url: str = "http://localhost:8000"', 'api_url: str = "http://localhost:8001"'),
    ("config: .env no longer read", CONFIG, 'env_file=".env"', "env_file=None"),
    ("config: .env found relative to the repo, not the folder", CONFIG, 'env_file=".env"', 'env_file=__import__("pathlib").Path(__file__).resolve().parents[2] / ".env"'),
    ("config: api_url validated inside Settings (empty refused)", CONFIG, 'api_url: str = "http://localhost:8000"', 'api_url: str = __import__("pydantic").Field("http://localhost:8000", min_length=1)'),
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


before = {p: sha(p) for p in (ENDPOINT, APP, CONFIG)}
results = []
for label, path, old, new in MUTATIONS:
    original = path.read_bytes()
    text = original.decode("utf-8")
    if text.count(old) != 1:
        results.append((label, f"SETUP ERROR: pattern found {text.count(old)} times"))
        print(f"{label:62} {results[-1][1]}", flush=True)
        continue
    try:
        path.write_bytes(text.replace(old, new).encode("utf-8"))
        done = subprocess.run(
            [PY, "-m", "pytest", *TESTS, "-x", "-q", "--no-header", "-p", "no:cacheprovider", "-W", "ignore"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
    finally:
        path.write_bytes(original)
        # always put the real file back, even if pytest crashed or this script is interrupted
    failed = [line.strip() for line in done.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))]
    if done.returncode == 0:
        results.append((label, "SURVIVED  <-- no test noticed"))
    else:
        first = failed[0].split(" - ")[0] if failed else done.stdout.strip().splitlines()[-1]
        results.append((label, "caught by " + first.replace("FAILED ", "")))
    print(f"{label:62} {results[-1][1]}", flush=True)

after = {p: sha(p) for p in (ENDPOINT, APP, CONFIG)}
print("\nfiles restored byte-for-byte:", before == after)
survivors = [r for r in results if not r[1].startswith("caught")]
print(f"{len(results) - len(survivors)} of {len(results)} mutations caught; survivors: {survivors}")
sys.exit(0 if before == after else 1)
