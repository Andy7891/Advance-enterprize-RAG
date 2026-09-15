# Debugging `ModuleNotFoundError`: The Full Causal Map + What You'd Need to Know

You hit `ModuleNotFoundError: No module named 'src'` twice in a row, and each time the real
cause turned out to be different from the first guess. That's not bad luck — this specific
error has an unusually large number of plausible, unrelated root causes that all produce
the *exact same message*. This doc is the map you'd need to navigate that on your own next
time, plus the two causes that actually applied to you, clearly marked.

**Core learning flag:** this is NOT a core RAG concept — it's general Python environment/
packaging literacy. But it's exactly the kind of "incidental engineering" skill (per your
own rule 4) that, once solid, saves you hours across every future Python project, not just
this one. Worth the investment.

---

## 1. What the error actually means, mechanically

`ModuleNotFoundError: No module named 'src'` means: at the moment Python executed
`import src` (or `from src.x import y`), it walked its list of search locations
(`sys.path`, in order) looking for something named `src` — a `.py` file, or a directory
containing `__init__.py` (or, for "namespace packages," even without one) — and found
nothing, in ANY of those locations, before reaching the end of the list.

Every single cause below is really just a different reason `sys.path` didn't contain the
right thing, or `src` wasn't correctly registered/named where Python could find it. **The
one command you should ALWAYS run first, before guessing anything else:**

```python
import sys
print(sys.executable)   # WHICH Python interpreter is actually running?
print(sys.path)         # WHERE is it looking, in order?
```

Everything else in this doc is really just "what could make one of THOSE two things wrong."

**Knowledge you need:** [Python docs — The import system](https://docs.python.org/3/reference/import.html) (dense, but section 5.1–5.3 on the module search path is the core of it). A gentler entry point: [Python docs — Modules tutorial, section 6.1.2](https://docs.python.org/3/tutorial/modules.html#the-module-search-path).

---

## 2. The full causal map

Organized as you'd actually investigate — cheapest/most-likely checks first. Each branch
says how to TEST for it specifically, not just what it is.

### Branch A — Wrong interpreter/environment entirely
**What it is:** the Python actually running isn't the one where you installed the package
(a different venv, system Python, Anaconda, etc.).
**How to test:** `sys.executable` — does the printed path point inside THIS project's
`.venv`?
**Applied to you?** This was the suspected cause behind your FIRST error, and it was a real,
independent problem (your `.venv` genuinely had no `ipykernel` installed, meaning whatever
kernel you'd been using couldn't have been this venv). Fixed by installing `ipykernel` and
registering a kernel — **necessary, but not sufficient**, as it turned out.
**Knowledge needed:** [Python docs — venv](https://docs.python.org/3/library/venv.html); understanding that "a virtual environment" and "a Jupyter kernel" are two different registration systems (see Branch E).

### Branch B — Package genuinely never installed anywhere
**What it is:** you never ran `pip install -e .` at all, in any environment.
**How to test:** `pip show advance-rag` (or whatever your project's declared name is) in the
active environment — does it report "Package not found"?
**Applied to you?** No — we'd run `pip install -e ".[dev]"` successfully multiple times
across earlier steps; this branch was ruled out quickly.
**Knowledge needed:** basic `pip install` mechanics — [pip docs: installing packages](https://pip.pypa.io/en/stable/getting-started/).

### Branch C — Installed, but under a DIFFERENT name than you're importing (your real bug)
**What it is:** the package installed successfully, but the NAME Python registered it under
doesn't match what your code is trying to import.
**How to test:** try importing the SUSPECTED real name directly —
`import ingestion; print(ingestion.__file__)` — if a name you never explicitly typed in your
own code (bare `ingestion`, not `src.ingestion`) resolves successfully, your package
registration doesn't match your import convention.
**Applied to you?** **Yes — this was the actual root cause of your persisting error.**
`pyproject.toml`'s `[tool.setuptools.packages.find] where = ["src"]` told setuptools "treat
`src/` as invisible, register what's INSIDE it as top-level packages" — so `ingestion`,
`api`, `core` were the real registered names, never `src.ingestion` etc. Every
`from src.x import y` in the whole codebase had been silently relying on Branch D (below) to
paper over this, the entire time.
**Knowledge needed:** this is the deepest, most specific knowledge gap in the whole
incident — **Python packaging layout conventions**:
- [Python Packaging User Guide — src layout vs flat layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/) (the canonical explanation of exactly this distinction)
- [setuptools docs — package discovery](https://setuptools.pypa.io/en/latest/userguide/package_discovery.html) (what `where=`/`include=`/`exclude=` in `packages.find` actually control)
- The specific, easy-to-miss trap: **standard "src layout" expects code to import bare
  names** (`from ingestion import x`), not `src.`-prefixed ones — this project does the
  latter throughout, which is a legitimate, internally-consistent choice, but it means the
  STANDARD `where=["src"]` config (which every packaging tutorial shows) was actively wrong
  for this codebase's actual import convention. Nobody tutorial-level would flag this
  mismatch, because both halves look individually "correct" — you have to already know both
  conventions to spot they'd been mixed.

### Branch D — cwd-dependent accidental imports, MASKING Branch C
**What it is:** in some Python invocation modes, the current working directory itself gets
added to the module search path — so a plain directory with an `__init__.py` in it can
become "importable" purely by coincidence of where you happened to run the command from,
completely independent of any real package installation.
**How to test:** run the exact same import from TWO different working directories — one at
your repo root, one somewhere else entirely (`cd` into a subfolder first). If it works from
one and not the other, this is almost certainly in play.
**Applied to you?** **Yes — this is WHY Branch C's bug was invisible for so long.** I proved
it directly: `python -c "from src.ingestion... import ..."` run with cwd AT the repo root
worked; the identical command run with cwd inside `practice codes/` failed with your exact
error — reproduced entirely outside any notebook.
**Knowledge needed:** the specific mechanics differ by HOW Python is invoked, and this is
worth knowing precisely, not approximately (verified directly for this project, not assumed):
- `python -c "..."`, the interactive REPL, and Jupyter kernels all set
  `sys.path[0] = ''` — and Python resolves that empty string DYNAMICALLY to "the current
  working directory," checked at the moment each import actually runs.
- Running an actual script file (`python somefile.py`) is DIFFERENT — `sys.path[0]` becomes
  that SCRIPT's own directory, not the process's cwd. (Verified directly: running a probe
  script placed at the repo root, invoked from a subdirectory, showed `sys.path[0]` as the
  repo root — the script's location — not the subdirectory it was launched from.)
- Reference: [Python docs — sys.path](https://docs.python.org/3/library/sys.html#sys.path), specifically the note about `sys.path[0]` under interactive/`-c`/`-m` invocation.

### Branch E — Kernel/environment registration is a SEPARATE system from venvs
**What it is:** installing a package into a venv and making a Jupyter NOTEBOOK use that venv
are two unrelated actions — a notebook's "kernel" is a separate registration
(`~/.local/share/jupyter/kernels/` or the Windows equivalent) that has to be explicitly
created and explicitly selected per-notebook.
**How to test:** `jupyter kernelspec list` shows what's registered; the kernel picker in
your notebook UI shows what's currently selected; `sys.executable` inside a cell confirms
which one is ACTUALLY active right now.
**Applied to you?** Yes, as part of the first fix — your venv had no kernel registered for
it at all (`ipykernel`/`jupyter` were both missing packages), meaning no notebook could ever
have been using this venv until that was fixed.
**Knowledge needed:** [Jupyter docs — kernels](https://docs.jupyter.org/en/latest/install/kernels.html); specifically that `ipykernel install` is a REGISTRATION step, separate from having `ipykernel` merely installed as a library.

### Branch F — Stale cached state (didn't apply here, but a real possibility)
**What it is:** a previously-imported module stays cached in `sys.modules` for the lifetime
of a running Python process/kernel — if you fix a packaging bug but don't RESTART the
kernel, the old (broken or differently-configured) import machinery can still be what's
active in memory.
**How to test:** `'src' in sys.modules` or `'src.ingestion' in sys.modules` — if a module is
already cached, re-running an `import` statement for it does nothing (Python skips re-
importing an already-loaded module by default).
**Applied to you?** Not directly diagnosed as a factor here, but worth flagging: after ANY
packaging config change (like Fix #2), a notebook kernel that was already running BEFORE the
fix needs a restart, not just re-running the import cell — otherwise you can chase a "fix
didn't work" ghost that's actually just stale cached state.
**Knowledge needed:** [Python docs — sys.modules](https://docs.python.org/3/reference/import.html#the-module-cache).

### Branch G — `PYTHONPATH` / `.pth` files / pytest's own path insertion (didn't apply, but common elsewhere)
**What it is:** several mechanisms outside your project can inject extra entries into
`sys.path` — an env var (`PYTHONPATH`), `.pth` files sitting in `site-packages`, or (for
tests specifically) `pytest`'s own `rootdir`/`conftest.py`-based path insertion, which
follows DIFFERENT rules than plain `python` invocation.
**How to test:** `echo $PYTHONPATH` (or `$env:PYTHONPATH` in PowerShell); inspect
`sys.path` for unexpected entries pointing at unfamiliar locations.
**Applied to you?** Not implicated here — but worth knowing this exists, since it's a classic
source of "works in pytest but not in a plain script" or vice versa, in OTHER projects.
**Knowledge needed:** [pytest docs — pythonpath and sys.path](https://docs.pytest.org/en/stable/explanation/pythonpath.html) — genuinely worth reading once, since this project's `tests/` directory relies on this exact mechanism working correctly.

### Branch H — Docker/container path layout mismatches (relevant to this project, not yet re-verified)
**What it is:** a container has its OWN filesystem and its OWN separately-run
`pip install`; a packaging fix validated on your host venv doesn't automatically prove the
Dockerfile's build produces the same result, if the Dockerfile does anything different (a
different `WORKDIR`, different `COPY` paths, a stale cached layer).
**How to test:** actually rebuild the image (`docker build ...`) and exec into a running
container to check `sys.path`/`import` behavior fresh, don't assume host-venv testing
transfers automatically.
**Applied to you?** Flagged as an open item in the previous fix — Docker Desktop wasn't
running at the time, so this genuinely has NOT been re-verified yet for Fix #2. Worth doing
next time Docker's available.
**Knowledge needed:** Step 1.1's own [docs/docker_deep_dive.md](docker_deep_dive.md), section 1.3 (layer caching) — a stale cached layer from BEFORE the `pyproject.toml` fix is exactly the kind of thing that could make a rebuild misleadingly still show the old, broken behavior if the build cache isn't invalidated correctly.

### Branch I — Naming collisions / shadowing (didn't apply, but a real "gotcha" class)
**What it is:** something else entirely reachable on `sys.path` happens to already be named
`src` (a different installed package, a stray `src.py` file sitting somewhere unexpected) —
Python takes the FIRST match it finds, so an unrelated shadow can mask or replace the one
you meant.
**How to test:** after a successful import, always check `<module>.__file__` — does the path
printed match where YOUR code actually lives, or does it point somewhere surprising?
**Applied to you?** No evidence of this here, but it's exactly why every diagnostic step
above insists on checking `.__file__`, not just "did the import succeed" — a shadowed,
wrong `src` importing successfully would be a WORSE bug than a clean `ModuleNotFoundError`,
because it fails silently instead of loudly.

---

## 3. The knowledge domains, distilled

If you wanted to have diagnosed both bugs entirely on your own, here's the actual concept
list — roughly in the order you'd need to reach for them:

1. **`sys.executable` and `sys.path` as the two ground-truth diagnostic values** — always
   check these FIRST, before forming any hypothesis. (Branches A, C, D, I)
2. **Virtual environments are isolated interpreters; Jupyter kernels are a SEPARATE
   registration on top of one** — installing a library into a venv does not make any
   particular notebook use it. (Branches A, E)
3. **How `sys.path[0]` differs by invocation mode** — `-c`/REPL/Jupyter use the CURRENT
   WORKING DIRECTORY (dynamically); running a `.py` file uses that FILE's own directory.
   These are genuinely different mechanisms that produce different debugging symptoms.
   (Branch D)
4. **"src layout" vs "flat layout" as named, competing Python packaging conventions**, and
   that `setuptools`'s `packages.find(where=..., include=...)` config has to be chosen
   to match whichever one your actual import statements use — not just copied from a
   tutorial. (Branch C — this was the deepest, least "guessable" piece of knowledge in the
   whole incident)
5. **The module cache (`sys.modules`) means a running process can hold onto stale/broken
   import state even after you fix the underlying cause** — a kernel/process restart is
   sometimes a REQUIRED step, not an optional one. (Branch F)
6. **A debugging methodology, not just facts**: isolate variables one at a time. Specifically,
   the technique that actually cracked Fix #2 was reproducing the EXACT SAME failure from a
   plain terminal (removing "notebook" as a variable) by deliberately `cd`-ing into a non-
   root directory first — turning a vague "the notebook is broken" into a precise,
   scriptable, shareable repro case. This single habit — "can I reproduce this outside the
   original context, with one variable changed at a time" — generalizes far beyond Python
   imports.

---

## 4. A reusable checklist for next time

When you see `ModuleNotFoundError` again (any project, not just this one), work through
this in order — stop as soon as one step reveals the problem:

1. `import sys; print(sys.executable)` — right interpreter/venv?
2. `import sys; print(sys.path)` — does it contain what you expect, in the order you expect?
3. `pip show <your-package-name>` (in that SAME environment) — is it actually installed?
4. Try importing the SUSPECTED bare/alternate name directly, and check `.__file__` on
   success — does the registered package name match what your code actually imports?
5. Reproduce the exact same import from a DIFFERENT working directory, in a plain terminal
   (not the notebook) — does the behavior change? If yes, you have a cwd-dependent bug
   (Branch D), possibly masking a deeper one (Branch C).
6. If you just changed any packaging config or reinstalled, RESTART the kernel/process
   before concluding a fix didn't work.
7. If this involves Docker, don't assume host-venv success transfers — rebuild and check
   inside the actual container.

## 5. Practice exercises (build the pattern-recognition, not just the memory)

1. Create a throwaway folder with a subfolder containing an `__init__.py`, `cd` into the
   PARENT folder, and confirm `import subfolder` works — then `cd` anywhere else and watch
   it fail. Directly feel Branch D happening on a trivial example, decoupled from this
   project's complexity.
2. Deliberately misconfigure a scratch project's `packages.find` (swap `where=["src"]` for
   the wrong value) and watch `pip show`/`pip install -e .` succeed anyway — packaging
   misconfigurations frequently DON'T error at install time, only at import time. Confirm
   this for yourself.
3. In a running Python REPL, `import json`, then edit `json`'s source file on disk (a
   throwaway change), then `import json` again in the SAME session — confirm nothing
   changes, because it's cached in `sys.modules`. Then restart the REPL and confirm the edit
   now takes effect. This is Branch F, deliberately triggered.
4. Set `PYTHONPATH` to a bogus directory in your shell, start Python, and inspect `sys.path`
   — see the injected entry directly, then unset it and confirm it disappears. Concrete
   proof Branch G is a real, inspectable mechanism, not a theoretical one.
5. Next time you get ANY `ModuleNotFoundError` in ANY project, run through the section 4
   checklist top-to-bottom BEFORE asking for help, and notice which numbered step actually
   resolves it — that's real, calibrated data on which branch is most common for you
   personally going forward.
