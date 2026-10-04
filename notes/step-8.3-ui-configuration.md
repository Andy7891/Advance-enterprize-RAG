# Phase 8, Microstep 3 — Add Configuration & Environment Setup

**Date:** 2026-10-04
**Core learning: No (configuration).** What it still teaches in a small space: that "where does this value
come from" has an order worth measuring, that a check belongs at the boundary that owns the assumption,
and that the one pitfall in `.env` handling (it is read from the *current folder*) is cheaper to pin with a
test than to remember.

## What was asked vs. what this step closes
The task: make the API URL configurable through `.env` or Streamlit's config; outputs: a configurable
endpoint and environment-variable support. (P8-M2 was never specified; I assumed it was the streaming
display, which P8-M1 already contains: D-2026-10-03-21.)

Most of the task was already true after P8-M1: `Settings.api_url` exists and pydantic-settings already
reads an environment variable and `.env`. What was missing, and is what this step closes:
1. Nobody had **measured** the sources or pinned their order with tests.
2. A mistyped value gave a **misleading message** (measured, see below).
3. The setting was **undocumented** (no `.env.example` line, no README section).
4. The page tests compared against `settings.api_url`, so they **depended on the developer's own `.env`**.

## Facts measured before deciding (not from memory)
Saved in [`practice codes/phase 8/m3_ui_configuration/`](<../practice codes/phase 8/m3_ui_configuration>):
`config_experiments.py` (items 1-6, each probe in a fresh process so nothing is cached) and
`urlsplit_probe.py` (item 7). Predict each line before you run them.
1. **Precedence** (pydantic-settings 2.15.0): with a `.env` containing `API_URL=http://from-dotenv:1` in the
   current folder: `.env` alone gives the `.env` value; `.env` plus an environment variable gives the
   *environment variable*; neither gives `http://localhost:8000`.
2. **`.env` is read from the current folder only.** The same `.env`, but the process started from another
   folder: the default, silently. No warning anywhere.
3. **`API_URL=` (empty) is accepted** by `Settings` and gives `''`.
4. **Streamlit's `config.toml` cannot hold a custom key.** With `[chat] api_url = "…"` in
   `.streamlit/config.toml`, `streamlit.config.get_option("chat.api_url")` raises
   `RuntimeError: Config key "chat.api_url" not defined.` (`server.address` from the same file works).
5. **`st.secrets` raises when there is no `secrets.toml`**, even for `.get("api_url", "DEFAULT")`:
   `StreamlitSecretNotFoundError: No secrets found. Valid paths …`. With the file present it works.
6. **What the page did with a bad value before this step:** `localhost:8000`, `''`, `ftp://host` and
   `http://` all gave a sidebar "Cannot reach the API at … (cannot connect (UnsupportedProtocol))", and `ask()` raised a
   `ChatError` of kind `connection_lost` ("The connection to localhost:8000 failed: Request URL is
   missing an 'http://' or 'https://' protocol"). That points at the network, when the cause is a typo.
7. **How `urllib.parse.urlsplit` (Python 3.11.3) treats the awkward forms:** `localhost:8000` parses as
   scheme `localhost` with path `8000` (it is not read as host and port); `http://host:abc` and
   `http://host:99999` parse, but reading `.port` raises `ValueError`; `http://host:0` gives port `0`;
   `http://host:` gives port `None` and httpx still builds `http://host/chat`; a newline inside the
   address is silently deleted (`http://host\n/x` parses with path `/x`); `.hostname` lower-cases the
   host but `.netloc` keeps its case; `http://[::1` raises `ValueError: Invalid IPv6 URL`.

## What was built
- **[`src/ui/endpoint.py`](../src/ui/endpoint.py)** (new, 110 lines, no Streamlit):
  `normalize_api_url(value)` returns the base address in the exact form `client.py` appends `/chat` to
  (surrounding whitespace and trailing slashes removed, scheme in lower case) or raises `ConfigError`;
  `configured_api_url()` applies it to `settings.api_url` on every call. Every message says what is
  wrong and ends with the fix and a reminder to restart ("Set API_URL in .env or in the environment,
  e.g. …, then restart streamlit run"). A value containing `@`, `?` or `#` (where a password or a token
  can sit) is never repeated in a message.
- **[`src/ui/app.py`](../src/ui/app.py)**: calls `configured_api_url()` right after the title. On a
  `ConfigError` it shows the message (through `as_plain_text`, like every other piece of outside text) and
  calls `st.stop()`: no sidebar, no chat input. Otherwise the tidied address is passed as a parameter to
  `render_sidebar`, `stream_into_page` and `answer_pending_question`; the page no longer imports `settings`.
- **`.env.example`**: a commented `API_URL=http://localhost:8000` with the rules (scheme, trailing slash,
  environment wins, current folder). **README**: a "Configuration" section (order of sources, the two
  pitfalls, a table row, the bash and PowerShell one-off override) and a status row.
- **Tests: 48 new** (152 in `tests/ui/`). `tests/ui/test_endpoint.py` (41): 11 good and 18 bad addresses,
  four password-or-token cases, the order environment > `.env` > default and the current-folder rule through
  `Settings(_env_file=…)` in temporary folders, "`Settings` itself still accepts an empty value", and three
  documentation checks (the template and the README agree with the real default). `tests/ui/test_app.py`
  gained 7: an unusable address stops the page before any widget or request (4 values), the error is shown
  as plain text, a trailing slash is tidied before use, and a guard that the test address differs from the
  default. The `api` fixture now installs a fixed `http://api.test`.

## Decisions (all "recommended")
Full entries in [decision.md](../decision.md) (D-2026-10-04-01 to -08):
1. The address is the existing `Settings.api_url`, not a Streamlit setting (measurements 4 and 5).
2. The check lives in `src/ui/endpoint.py`, not in `Settings` (shared by the API and the scripts).
3. `.env` stays looked up in the current folder: documented and pinned by a test, not changed.
4. `.env.example` and the README document it, and tests keep them honest.
5. Timeouts, `k` bounds and the other page constants stay constants.
6. No compose `ui` service yet; what it will need is written down.
7. Tests: a table of addresses, the sources pinned in temp folders, page tests isolated from your `.env`.
8. Which forms are refused (credentials, query, fragment, `/chat` or `/health` pasted in, port 0).

## Verified, not assumed
- **CI gates, all four green:** `ruff check` clean, `ruff format --check` 97 files already formatted,
  `mypy src/` no issues in 41 files, `pytest tests/ -m "not tiktoken and not live"` **965 passed, 98
  deselected, 5 xfailed** (917 before this step, plus the 48 new tests).
- **Isolation:** the whole UI suite (152) also passes with a hostile `API_URL` (`localhost:8000`, and
  empty) set in the environment, so the page tests no longer depend on your `.env`.
- **Mutation check** (`mutate_p8m3.py`, same folder; it restores the files and compares hashes): 26
  deliberate bugs, planted one at a time in `endpoint.py`, `app.py` and `config.py`
  (no trailing-slash strip, scheme not checked, password or token echoed (two variants), restart
  reminder dropped, spaces allowed, query allowed, credentials allowed, host not required, port not
  checked, port 0 allowed, pasted endpoint allowed,
  `/health` not recognised, scheme case kept, no strip, empty not recognised, setting ignored, page does not
  stop, error not escaped, hard-coded default in `ask`, in the health check and in the sidebar, changed
  default, `.env` not read, `.env` anchored to the repository, a validator inside `Settings`). All 26 were
  caught, and the files were restored byte-for-byte (compared by hash).
- **A real browser, a real Streamlit server started from a scratch folder** (so your `.env` was never
  touched) against the stub API:
  1. `.env` with `API_URL=localhost:8000`: the page shows "API_URL must start with http:// or https://
     (got 'localhost:8000'). Set API_URL in .env or in the environment, e.g. API_URL=http://localhost:8000,
     then restart streamlit run (settings are read once, when it starts)." and nothing else: no input
     box (a search for it finds nothing), no sidebar.
  2. `.env` corrected while the server ran, page reloaded: **the old error is still shown.** Settings are
     read once per process, so editing `.env` needs a restart (README says so).
  3. After a restart, with `API_URL=http://localhost:8004/` (trailing slash): the sidebar says "API
     reachable at http://localhost:8004", a question is answered with its sources, and the stub's log shows
     `GET /health` and `POST /chat` (one slash each).
  4. `.env` saying `http://localhost:9` and an environment variable `API_URL=http://localhost:8004`: the
     page used the environment variable.
  5. `.env` with a made-up `API_URL=http://localhost:8004/?token=…`: the page says "API_URL must not
     contain a query or fragment (nothing after a ? or #) (the value is not shown because it may hold a
     password or a token)", and the made-up token appears nowhere on the page.
  Both override forms from the README were run: `API_URL=… streamlit run …` in bash and
  `$env:API_URL = "…"; …` in PowerShell (the latter checked by reading the setting).

## Findings
1. **`localhost:8000` is not "a host and a port" to a URL parser.** `urlsplit` reads `localhost` as a
   scheme. That is why the old message talked about a lost connection: the error came from deep inside
   httpx, three layers away from the typo.
2. **`urlsplit` silently deletes newlines and tabs**, so the check for spaces and control characters has to
   run *before* parsing, or `http://host\n/x` is accepted and quietly changed.
3. **`.port` raises when it is read, not when the URL is parsed**, and port `0` is accepted by the parser.
4. **My first page test was wrong, not the page.** It looked for `API_URL` in the error text, but the page
   shows the message through `as_plain_text`, so the text Streamlit holds is `API\_URL` (it renders
   correctly in the browser). A test sees the markdown *source*; compare against `as_plain_text(expected)`.
5. **Reloading the browser does not reload `.env`** (browser run, step 2). A browser reload starts a new
   *session* in the same *process*, and the settings object belongs to the process.
6. **All 26 mutations were caught on the first run, which proves less than it sounds.** They are the bugs I
   could think of, and each test was written with its bug in mind. What the run does show is that no check
   is decorative: remove any one rule and a named test fails. It cannot show that no other bug exists.
7. **A PowerShell override outlives the command.** `API_URL=… streamlit run …` in bash applies to that one
   command, but `$env:API_URL = "…"` stays set for the whole terminal window. Because the environment
   beats `.env`, a forgotten override makes later edits to `.env` look ignored. I caught this re-reading my
   own README snippet, which had said "once": it now shows `Remove-Item Env:API_URL` as well.
8. **Re-reading my own error message found two gaps.** It did not say that a reload is not enough (I had
   just been fooled by that myself), and it hid a password but would have printed a `?token=…` back onto
   the page. Both are fixed (the message ends with a restart reminder; any value containing `@`, `?` or
   `#` is withheld), and each has a test and a mutation.

## Key concepts
🔴 = core and worth understanding deeply; 🟡 = useful; ⚪ = boilerplate.
- 🔴 **A configuration value has an order of sources**, and the order is a contract: environment variable,
  then `.env`, then the default in the code. Know it, measure it, test it (twelve-factor "config in the
  environment").
- 🔴 **Validate at the boundary that owns the assumption.** `Settings` is shared by the API, the scripts and
  the tests; only the page needs a usable HTTP address, so only the page refuses a bad one.
- 🟡 **`.env` is found relative to the current folder**, not to the project: a silent default is the
  failure mode. Pin it with a test; mention it where people start the program.
- 🟡 **Configuration versus secrets**: `st.secrets` is for credentials and fails when its file is absent;
  `config.toml` is for Streamlit's own options and rejects unknown keys. An API address is neither.
- 🟡 **Never echo a value that may hold a secret** into a page or a log (a password in `user:password@`, a token after `?` or `#`).
- ⚪ `.env.example` as the first documentation a newcomer meets; `monkeypatch.setattr` on a shared settings
  object; a one-off override per shell.

## Honest limitations
1. **The check proves the address is well formed, not that anything answers there.** The sidebar's
   `/health` call does that, on every run.
2. **A restart is needed after editing `.env`** (measured). There is no hot reload of settings.
3. **The sidebar's "Cannot reach the API" message does not mention `API_URL`.** With a configurable address
   a wrong-but-valid value (`http://localhost:9`) is now a likely cause. Not changed: it was not in the agreed
   build, and the message is pinned by an existing test (exercise 7).
4. **`.env.example` still lacks `QDRANT_URL` and `OLLAMA_URL`**, whose defaults are compose service names
   that do not resolve from the host (docs/architecture.md says so for `OLLAMA_URL`); a host-run API needs
   them in `.env`. Outside this task, so left alone; worth deciding separately.
5. **`http://` to a remote host sends the question in clear text.** Fine for `localhost`, which is what this
   project uses; nothing here asks for `https://` and nothing should have to until the API is remote.
6. **Only the address is configurable.** Timeouts, `k` bounds and the question cap are constants on purpose
   (D-2026-10-04-05).
7. **IPv6 literals are tested once; internationalised host names are accepted but not tested.** httpx
   converts `http://bücher.example` to `xn--bcher-kva.example` (measured in a probe), so they work.

## How this connects
**Previous:** P8-M1 built the page and added `Settings.api_url`; this step makes that setting trustworthy
and documented. **Next (not yet specified):** P8-M2's content is unknown to me; a compose `ui` service
(D-2026-10-04-06) and a "Stop generating" button (P8-M1, D7) are the open candidates.

## How to do this yourself
1. Before choosing where a value lives, **list every place it could come from and measure each** with a
   ten-line probe in a fresh process. Two of the three Streamlit-native options failed in their first line.
2. **Put the check where the assumption lives**, not in the shared settings object, and make the message
   say what is wrong *and* how to fix it.
3. **Test the contract of the sources in temporary folders** (order, current-folder rule), **isolate the
   other tests from your own `.env`**, then plant bugs and watch the tests fail.

## Practice exercises
1. In a temporary folder write `.env` with `API_URL=http://a:1`. Predict, then run
   `python -c "from src.core.config import settings; print(settings.api_url)"` (a) in that folder, (b) one
   folder up, (c) in that folder with `$env:API_URL="http://b:2"` set. Explain each result. Then run (a)
   again: why does it still say `http://b:2`, and what removes it?
2. `streamlit run src/ui/app.py -- --api-url http://x:1` hands the page a command-line flag. Where would a
   flag sit in the order of sources, and why do most programs put it first? Write the failing test first.
3. Put `api_url = "http://x:1"` in `.streamlit/secrets.toml` and read it with `st.secrets["api_url"]`, then
   delete the file. What is raised? Write the `try/except` and say why this project did not use it.
4. Add `[chat] api_url = "…"` to `.streamlit/config.toml` and call `streamlit.config.get_option`. What is
   raised? Run `streamlit config show` and find which options *can* be set.
5. Delete the `"@" in value` guard in `_echo` and find the tests that fail. Then explain why printing the
   rejected address into a log line is a leak even if no page ever shows it.
6. Write a Hypothesis test: for any text, `normalize_api_url` either raises `ConfigError` or returns a string
   for which `httpx.URL(result + "/chat")` has a path ending in `/chat`. Run it and read what it finds.
7. Make the sidebar's down-API message mention `API_URL`. Which existing test pins the old text, and what
   should the new assertion say so that a future edit cannot drop the hint?
8. Make `READ_TIMEOUT_SECONDS` come from an environment variable. Which test must keep passing, and why is
   "must be longer than the API's own timeout" a rule configuration cannot enforce by itself?
9. Draft the compose `ui` service: image, command, `API_URL`, published port. What happens from the host if
   Streamlit listens on `127.0.0.1` *inside* the container? Where does the "local only" promise move to?
10. Start the page from a different folder than the repository root with the API on port 8004 and no `.env`
    there. Fix it three ways (environment variable, start from the root, copy `.env`) and rank them.

## Sources
- [pydantic-settings: field value priority](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#field-value-priority)
  and [dotenv support](https://docs.pydantic.dev/latest/concepts/pydantic_settings/#dotenv-env-support)
- [The Twelve-Factor App: III. Config](https://12factor.net/config)
- [Streamlit: configuration](https://docs.streamlit.io/develop/concepts/configuration) and
  [`config.toml`](https://docs.streamlit.io/develop/api-reference/configuration/config.toml)
- [Streamlit: secrets management](https://docs.streamlit.io/develop/concepts/connections/secrets-management)
  and [`st.secrets`](https://docs.streamlit.io/develop/api-reference/connections/st.secrets)
- [Python: `urllib.parse`](https://docs.python.org/3/library/urllib.parse.html) and
  [RFC 3986: URI generic syntax](https://www.rfc-editor.org/rfc/rfc3986)
- [Docker Compose: setting environment variables](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/)
- [httpx: clients](https://www.python-httpx.org/advanced/clients/)
- Earlier steps this builds on: [notes/step-8.1-streamlit-chat-ui.md](step-8.1-streamlit-chat-ui.md),
  [docs/39](<../docs/39 streamlit_chat_ui_deep_dive.md>), [docs/api-contract.md](../docs/api-contract.md).
