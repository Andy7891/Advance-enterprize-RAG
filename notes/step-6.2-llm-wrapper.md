# Phase 6, Microstep 2 — LLM Wrapper / Integration

**Date:** 2026-09-30
**Core learning: Yes — LLM API integration.** This is the first class in the whole project that
*produces* new text rather than fetching or ranking existing text, and the first that has to
account for a call whose exact output can't be predicted in advance.

## The gap this step closes
P6-M1 proved a local model exists and can answer *something*. Nothing yet exposed a stable way
to actually ask it something specific, with controllable randomness (`temperature`) and a bounded
answer length (`max_tokens`), or to receive the answer incrementally as it's generated
(`stream`). `LocalLLM` is that interface — and, per the task, the *only* thing later Phase 6 steps
should need to know about how generation actually happens.

## What was built
- **[`src/orchestration/llm.py`](../src/orchestration/llm.py)**
  - `LocalLLM(client, *, model=None)` — `model` resolved from `settings.llm_model` at
    *construction* time, not import time (see Finding 1).
  - `generate(prompt, temperature, max_tokens) -> str`: one real, non-streaming call, mapping
    `max_tokens` to Ollama's own `num_predict` option.
  - `stream(prompt, temperature) -> Iterator[str]`: one real, streaming call, yielding each
    text fragment as Ollama sends it, skipping the final empty `done=True` fragment.
  - `LLMClient` — a Protocol with two `@overload`-decorated `generate` signatures (`stream:
    Literal[False]` returning one response, `stream: Literal[True]` returning an iterator),
    matching the real `ollama.Client.generate`'s own overload shape exactly.
  - `build_llm(ollama_url, *, model=None)` — reuses `src/generation/setup.py`'s `build_client`
    rather than duplicating a client factory, since both files serve the same concern.
- **[`tests/orchestration/test_llm.py`](../tests/orchestration/test_llm.py)**: 35 tests — 33
  hermetic (a fake client built from real `ollama.GenerateResponse`/`ollama.ListResponse`
  objects) and 2 live, against the already-pulled `mistral`.
- No existing file changed except a background-task suggestion (see Finding 1).

## Your choices (all "recommended") and how each one could fail
| Decision | Chosen | Rejected | Failure mode |
|---|---|---|---|
| D1 Client library | raw `ollama` package, reusing `build_client` | `langchain_ollama.OllamaLLM`; LlamaIndex | LlamaIndex would be a first, unused-elsewhere dependency; LangChain remains available if a later step wants chains/prompt templates |
| D2 Return shape | plain `str` only | a richer `generate_with_details` twin (model, real token/timing metadata) | no lineage to preserve yet — this is the first stage to produce this kind of output, nothing downstream joins back to it |
| D3 `stream()` signature | matches the spec exactly, no `max_tokens` | add `max_tokens` anyway, for safety | an unbounded stream from a wedged/looping model has no cap — a real, accepted risk |
| D4 Validation bounds | `temperature` finite ≥ 0, no upper bound; `max_tokens` positive int | also cap temperature (e.g. ≤ 2.0) | a caller can pass an unusually large temperature; only Ollama itself would reject or misbehave |
| D5 Errors | propagate unchanged | catch and return a fallback string | the fifth time this project has made this exact choice (P5-M2/M3/M4, P6-M1) |
| D6 Location | new `src/orchestration/llm.py`, `src/generation/` untouched | move/rename `src/generation/` into `src/orchestration/` now | a bigger, unrequested reorganization; deferred |
| D7 Acceptance | hermetic fakes + live against the warm, already-pulled model | hermetic only | P6-M1 found two real bugs specifically because of live testing; this step found two more the same way |

## Verified, not assumed
```
this step's tests    : 35 (33 hermetic + 2 live), all pass
mutation check         : 9 deliberate breakages of llm.py, each caught by a test, none survived
                         (one is the EXACT `or`-vs-`is None` bug described in Finding 2)
src/orchestration coverage : 100%
CI mode               : 637 passed, 88 deselected
whole suite (services up)  : 716 passed, 6 skipped (Cohere-gated, unrelated), 3 xfailed  (~106s)
ruff, format, mypy     : clean
```

## Findings
1. **I found the same "default frozen at import" bug this project already knows to avoid — in
   P6-M1's own code, not this step's.** `src/graph/setup.py`'s `build_driver` has an explicit
   docstring warning: "Defaults are resolved inside the function rather than as default
   arguments, which would freeze the values at import and ignore later configuration." P6-M1's
   `health_check(client, model: str = settings.llm_model)` does exactly the thing that warning
   exists to prevent — `settings.llm_model` is evaluated once, at module import, not per call.
   It has never actually misfired (nothing currently overrides `settings.llm_model` after
   import), so it's a real but latent bug, not an active one. I built `LocalLLM` correctly from
   the start (`model: str | None = None`, resolved inside `__init__`) and flagged the P6-M1 fix
   as a separate, self-contained background task rather than editing already-published,
   already-doc-quoted code as a drive-by inside this step.
2. **My own first version of `LocalLLM` repeated a mistake this project has explicitly
   documented before, and a test caught it immediately.** I wrote `resolved_model = model or
   settings.llm_model` — but `"" or settings.llm_model` evaluates to `settings.llm_model`, so an
   *explicitly* passed empty string was silently treated as "use the default" instead of being
   rejected. `test_a_blank_model_name_is_rejected_at_construction[""]` failed the moment I wrote
   it, for the exact reason `hybrid_retriever.py`'s own comment already names elsewhere in this
   project ("is None, not `or`: `0 or 1.0` would give `1.0`"). Fixed to `settings.llm_model if
   model is None else model`.
3. **This local model does not reliably follow "reply with exactly this word" instructions, even
   at `temperature=0`, and this is now confirmed twice, independently.** P6-M1's own probe asked
   for exactly "OK" and got "Understood." My first live test asked for exactly "PONG" and got "
   Fun!" — a friendly, plausible, completely wrong literal answer. This isn't a bug in
   `LocalLLM`; it's a real, measured property of this specific model at this size. The live test
   now asks a factual question ("What is the capital of France?") instead, which the model
   answers correctly and consistently.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **LLM API wrapper / adapter pattern:** hiding a specific library's calling convention behind
  a stable interface, so the rest of the codebase depends on `LocalLLM.generate`, not on
  `ollama.Client.generate`'s exact keyword arguments.
- 🔴 **Streaming as a Python generator:** `stream()` is a generator function — nothing runs until
  the caller starts iterating it, and each fragment reaches the caller the moment Ollama sends
  it, never buffered into one big string first.
- 🟡 **Sampling parameter mapping:** `max_tokens` (this task's vocabulary) → `num_predict`
  (Ollama's own vocabulary) — the same concept, different names, and getting the mapping wrong
  would silently do nothing (an unrecognized options key is just ignored, not rejected).
- 🟡 **`is None` vs `or` for resolving a default:** a falsy-but-valid input (`""`, `0`, `False`)
  is silently swallowed by `or`; only an explicit `is None` check tells "not given" apart from
  "given, but empty/zero/false." This project has now hit this exact mistake at least twice.
- ⚪ Protocol-typed injection, validators, a factory function reusing an existing one — plumbing.

## Honest limitations
1. **No token-budget enforcement beyond `num_predict`.** Ollama's own cap, not a hard guarantee
   verified by this wrapper — an answer could still come back shorter or (rarely) longer than
   requested depending on the model's own stopping behavior.
2. **`stream()` has no cap at all (D3).** A genuinely looping model has nothing here to stop it.
3. **This model's instruction-following is measurably imperfect** for exact-word replies (Finding
   3) — worth remembering once prompt construction (a later step) starts asking for structured
   or constrained output.
4. **Still inherits P6-M1's open item**: the Docker memory-budget risk applies to every live call
   this step makes too.

## How this connects
**Previous:** P6-M1 proved a model exists and answers *something*. **This step:** the stable
interface everything else calls to make it answer *something specific*. **Next (not yet specified
to me):** almost certainly prompt construction — reading a `RetrievalResult` (Phase 5's own
output) and the original question, building the actual prompt string `LocalLLM.generate`/`stream`
receive.

## How to do this yourself
1. Before wrapping a library, list the exact methods you'll call and only type-check against
   those (a `Protocol`), not the library's whole surface.
2. When a library method is `@overload`-decorated for different call shapes (streaming vs not),
   match each shape with its own overload in your own Protocol, not one loosened signature.
3. Resolve configuration defaults inside the function/constructor body, checked with `is None`,
   never as a parameter default expression and never with a bare `or`.
4. Test live behavior with a real, open-ended enough question that a working model can't
   accidentally fail it by being *too creative* — a strict "say exactly this" test can fail for
   reasons that have nothing to do with your code.
5. Write a test for the specific failure mode BEFORE you're sure your code avoids it — the
   blank-model-name test caught a real bug within seconds of being written.

## Practice exercises
1. Call `llm.generate(...)` twice with the same prompt and `temperature=0.0`. Are the two
   replies identical? Now try `temperature=1.0`. What changes?
2. Call `llm.stream(...)` and print each fragment as it arrives, with a timestamp. Does the delay
   between fragments look roughly even, or bursty?
3. Set `max_tokens=5` on a question that would naturally need a long answer. Read the real
   output — does it stop cleanly at a word boundary, or mid-word?
4. Build a `LocalLLM` with an intentionally wrong model name (`model="not-a-real-model"`) and
   call `.generate(...)`. What real exception type do you get, and does the message clearly say
   why?
5. Write your own fake `LLMClient` from scratch (don't copy `FakeClient`) and get `mypy` to
   accept it against `LocalLLM`'s constructor. Which two methods does it need, and in what exact
   shape?
6. Change `stream()`'s loop to yield `chunk.response or ""` (never skipping the empty final
   fragment) and re-run the tests. Which one catches it, and what does a caller iterating the
   stream see differently?
7. Time `generate()` vs collecting all of `stream()`'s fragments into one string, for the same
   prompt. Which is faster overall? Which one lets you show something to a user *sooner*?
8. Read Ollama's own docs for `num_predict`. Does it describe it as a hard cap or a guideline?
   Find one real example where the generated text's length doesn't match your `max_tokens`.
9. Design (on paper) what `generate_with_details` (D2's rejected option) would return, using
   real fields Ollama's `GenerateResponse` already carries (`eval_count`, `eval_duration`,
   `prompt_eval_count`). What would you use each one for?
10. Ask the model the *exact* same "reply with exactly one word" instruction from Finding 3 five
    times at `temperature=0.0`. Is the (wrong) answer at least consistent across runs, or does it
    vary even at zero temperature?

## Sources
- [Ollama API reference](https://github.com/ollama/ollama/blob/main/docs/api.md) — `/api/generate`,
  `options.temperature`, `options.num_predict`, `stream`.
- [Ollama modelfile parameters](https://github.com/ollama/ollama/blob/main/docs/modelfile.md#parameter) —
  what `num_predict` and `temperature` actually control.
- [LangChain `OllamaLLM`](https://python.langchain.com/docs/integrations/llms/ollama/) — the
  rejected alternative (D1b), confirmed already available with no new dependency.
- Python docs, [`typing.overload`](https://docs.python.org/3/library/typing.html#typing.overload) —
  the mechanism behind `LLMClient`'s two `generate` signatures.
- [notes/step-6.1-ollama-llm-setup.md](step-6.1-ollama-llm-setup.md) — `build_client`, reused
  here rather than duplicated (D1a/D6a).
