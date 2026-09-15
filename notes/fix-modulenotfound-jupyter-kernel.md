# Fix — `ModuleNotFoundError: No module named 'src'` in Jupyter

**Date:** 2026-08-21

### What happened
Running `from src.ingestion.loader import ...` in a Jupyter notebook raised
`ModuleNotFoundError: No module named 'src'`, even though the exact same import works fine
from a terminal (`.venv/Scripts/python.exe -c "from src.ingestion... "`).

### Root cause
`src` is only importable inside THIS project's `.venv` (registered there via
`pip install -e .` back in Step 1.1). Checking the venv directly
(`pip show ipykernel jupyter`) showed neither package was installed at all — meaning
whatever notebook kernel was in use belonged to a COMPLETELY DIFFERENT Python environment
(e.g. a system-wide Python, Anaconda, or a different venv) that never had this project
installed into it. The notebook's `import` was running in an environment that has never
heard of this project, not the one all the terminal commands in this whole learning log have
been using.

### Fix
1. Added `ipykernel==7.3.0` to `pyproject.toml`'s `dev` extras and reinstalled
   (`pip install -e ".[dev]"`).
2. Registered a NAMED kernel pointing at this exact venv:
   ```bash
   .venv/Scripts/python.exe -m ipykernel install --user --name=advance-rag --display-name "Advance RAG (.venv)"
   ```
3. **You must then select "Advance RAG (.venv)" as the kernel** in your notebook (Jupyter's
   kernel picker, or VS Code's kernel selector in the top-right of a notebook file) — this
   step doesn't happen automatically; registering a kernel just makes it AVAILABLE to
   choose, it doesn't switch any already-open notebook to use it.
4. Verified the fix from the terminal (not the notebook, to isolate variables):
   `from src.ingestion.loader import discover_documents` succeeded and returned 76, and
   the full test suite (23 tests) + `ruff check` + `mypy` still pass after the
   `pyproject.toml` change.

### Key concept
**A Jupyter kernel IS a Python environment/interpreter, chosen independently per notebook**
— installing a package into a venv from your terminal does NOT make it available to a
notebook unless that notebook's kernel points at that exact same venv. This is a very common
first-time gotcha: terminal and notebook can silently be running completely different
Pythons even when opened from the "same" project folder.

### How to do this yourself (reproduce without me)
1. From inside the project's venv, run
   `python -m pip show ipykernel` — if it errors "package not found," that confirms the venv
   itself can't run notebooks yet.
2. Install it: add `ipykernel` to your dev dependencies, reinstall.
3. Register a kernel: `python -m ipykernel install --user --name=<short-name> --display-name "<Human Readable Name>"`.
4. In your notebook UI, open the kernel picker and select the new display name explicitly —
   don't assume it's already selected.
5. Sanity-check with a trivial cell: `import sys; print(sys.executable)` — this should print
   a path INSIDE your project's `.venv`, not a system Python or a different environment. If
   it doesn't, the kernel picker selection didn't take effect.
