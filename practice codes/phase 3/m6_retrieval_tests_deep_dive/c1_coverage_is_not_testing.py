# Line coverage says which lines RAN during tests, not whether anything was CHECKED.
import importlib
import sys
import tempfile
from pathlib import Path

import coverage

module_dir = Path(tempfile.mkdtemp())
(module_dir / "retrieval_demo.py").write_text(
    "def top_k(scores, k):\n"
    "    ranked = sorted(scores, key=lambda s: s[1])      # BUG: ascending, worst first\n"
    "    return ranked[:k]\n",
    encoding="utf-8",
)
sys.path.insert(0, str(module_dir))

cov = coverage.Coverage(data_file=None, include=[str(module_dir / "*")])
cov.start()
retrieval_demo = importlib.import_module("retrieval_demo")
result = retrieval_demo.top_k([("a", 0.9), ("b", 0.2), ("c", 0.5)], k=1)
cov.stop()

weak_test_passed = len(result) == 1                     # "returns one result" -- checks nothing useful
_, statements, _, missing, _ = cov.analysis2(str(module_dir / "retrieval_demo.py"))
covered = len(statements) - len(missing)
print(f"weak test (one result returned) passed : {weak_test_passed}")
print(f"line coverage                          : {covered}/{len(statements)} statements = "
      f"{100 * covered // len(statements)}%")
print(f"actual top-1                           : {result}  <- the WORST chunk")
print(f"strong test (best chunk first) passed  : {result[0][0] == 'a'}")
