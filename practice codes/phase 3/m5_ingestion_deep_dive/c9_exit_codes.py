# Exit codes: how a script tells other programs whether it worked, without anyone reading its output.
import subprocess
import sys

cases = {
    "success": "raise SystemExit(0)",
    "failure": "raise SystemExit(1)",
    "uncaught exception": "raise ValueError('boom')",
    "printed 'FAILED' but exited normally": "print('FAILED')",
}
for label, code in cases.items():
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    print(f"{label:<38} -> exit code {r.returncode}")

# How a shell uses it: the second command only runs if the first returned 0
for first in ("raise SystemExit(0)", "raise SystemExit(1)"):
    r = subprocess.run(f'"{sys.executable}" -c "{first}" && echo "  next step ran"', shell=True,
                       capture_output=True, text=True)
    print(f"`{first}` && next ->", r.stdout.strip() or "  next step skipped")

r = subprocess.run([sys.executable, "-m", "src.vectorstore.store", "health"], capture_output=True, text=True)
print("real CLI `store health` -> exit code", r.returncode, "|", r.stdout.strip())
