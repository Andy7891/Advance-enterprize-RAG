# Fix #2 — `ModuleNotFoundError: No module named 'src'` PERSISTED after correct kernel

**Date:** 2026-08-21

### What happened
After Fix #1 (registering and selecting the correct `.venv` kernel — confirmed via
`sys.executable` pointing at the right venv), `from src.ingestion.schemas import ...` STILL
raised `ModuleNotFoundError: No module named 'src'`. This meant Fix #1's diagnosis, while
correct as far as it went, wasn't the WHOLE story — there was a second, independent bug
underneath it.

### Root cause — a real bug in `pyproject.toml`, not a notebook/kernel issue at all
`[tool.setuptools.packages.find] where = ["src"]` tells setuptools: "packages live INSIDE
`src/`; treat `src/` itself as invisible, and install whatever's inside it (`ingestion`,
`api`, `core`) as top-level packages." I verified this directly:
```python
import ingestion  # NO "src." prefix
print(ingestion.__file__)  # -> .../src/ingestion/__init__.py  -- this actually worked!
```
So the package that `pip install -e .` genuinely registered was named `ingestion` (bare),
NOT `src.ingestion`. Every `from src.xxx import ...` statement in this entire codebase
(written since Step 1.2) had ONLY ever "worked" as an accident of `sys.path` — when a
terminal command runs with the current working directory AT the repo root, Python
implicitly adds that cwd to `sys.path`, and the plain `src/` folder (which has an
`__init__.py`, from Step 1.2) becomes importable purely as a side effect of THAT, completely
independent of the real `pip install -e .` package registration. I proved this directly by
reproducing the exact failure from a terminal:
```bash
cd "practice codes" && ../.venv/Scripts/python.exe -c "from src.ingestion.loader import discover_documents"
# ModuleNotFoundError: No module named 'src'   <- reproduced outside the notebook entirely
```
This confirms it was NEVER a kernel/notebook-specific problem — any process (notebook OR
terminal) run from a directory other than the repo root would hit this exact same error.
Fix #1 (the kernel registration) was still a legitimate, necessary fix on its own — the
venv genuinely lacked `ipykernel` — but it was fixing a DIFFERENT problem than the one
actually causing this specific error message.

### Fix
Changed the package-discovery config so `src` itself becomes the real, properly-registered
top-level package — matching what every file in this codebase (and prior NOTES.md entries)
already assumes:
```toml
[tool.setuptools.packages.find]
where = ["."]
include = ["src", "src.*"]
```
Reinstalled with `pip install -e ".[dev]" --force-reinstall --no-deps`, then verified:
- `from src.ingestion.loader import discover_documents` now works from `practice codes/`
  (a real subdirectory, NOT the repo root) — the actual bug is fixed, not just relocated.
- The old accidental bare `import ingestion` (no `src.` prefix) now correctly FAILS — proof
  the package registration changed to match our code's real import convention instead of
  the standard "src-layout" convention we were never actually following consistently.
- Full test suite (23 tests), `ruff check`, and `mypy` all still pass unchanged.
- **Not yet re-verified inside Docker** — Docker Desktop wasn't running at the time of this
  fix. The Dockerfile's `pip install --prefix=/install .` uses the same
  `pyproject.toml`/setuptools mechanism, so it should behave identically, but this is a
  claim based on the mechanism being environment-agnostic, not a directly re-run Docker
  build — worth confirming with `docker compose build api` next time Docker Desktop is up.

### Key concept
**"src layout" is a real, named Python packaging convention** — but it specifically expects
NO `__init__.py` directly in `src/`, and code to import bare package names (`from
ingestion import ...`, not `from src.ingestion import ...`). This project has an `src/
__init__.py` (added early on, Step 1.2, as unremarkable-seeming boilerplate) AND every
import statement uses the `src.` prefix — a real, internally-consistent convention, just
NOT the standard src-layout one. `where = ["src"]` in `pyproject.toml` was leftover
configuration matching the STANDARD convention, silently mismatched against what the actual
codebase does. This is a good example of how two individually-reasonable-looking choices
(an `src/__init__.py`, and a standard-looking `packages.find` config) can combine into a bug
that only surfaces once something (a notebook in a subdirectory) breaks the ONE
undocumented assumption — "always run from the repo root" — holding it together.

### How to do this yourself (reproduce without me)
1. When `from src.x import y` works from a terminal at the repo root but fails from
   anywhere else (a notebook, a different cwd), suspect the package registration itself,
   not just the environment/kernel — the two are separate possible causes and BOTH may be
   real simultaneously (as they were here).
2. Directly test what your editable install actually registered:
   `python -c "import <suspected-bare-name>; print(<suspected-bare-name>.__file__)"` — if a
   bare, unprefixed import unexpectedly succeeds, your `packages.find` config doesn't match
   your code's actual import convention.
3. Reproduce the failure from a plain terminal, `cd`'d into a non-root subdirectory — this
   isolates whether the bug is notebook-specific or a genuine packaging config issue that
   would affect ANY process run from that location.
4. Fix `[tool.setuptools.packages.find]` to match whichever convention your code ACTUALLY
   uses (`src.x` prefixed imports vs bare `x` imports) — don't change decades of existing
   import statements across a whole codebase to match a config file; change the config to
   match the code that already exists and is already tested.
5. Reinstall (`pip install -e . --force-reinstall --no-deps` is enough — only the package
   metadata/registration changed, not the dependency list) and re-verify from a non-root
   directory specifically, not just the repo root (which would have looked fine even with
   the bug still present, per the accidental-cwd-import behavior described above).
