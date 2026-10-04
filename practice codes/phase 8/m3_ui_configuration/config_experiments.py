"""P8-M3 measurements 1-6: where the chat page's API address can come from, and what happens to bad ones.

Every probe runs in a FRESH process (so nothing is cached) from a chosen folder, which is the whole
point: pydantic-settings looks for `.env` in the *current* folder.

    python "practice codes/phase 8/m3_ui_configuration/config_experiments.py"

Predict each line before you run it. Expected, from the run quoted in notes/step-8.3-ui-configuration.md:
A) `.env` alone -> the .env value; `.env` + environment variable -> the environment variable; neither
   or "run from another folder" -> http://localhost:8000; API_URL set to '' -> ''.
B) a custom key in .streamlit/config.toml -> RuntimeError: Config key "chat.api_url" not defined.
C) st.secrets without a secrets.toml -> StreamlitSecretNotFoundError, even for .get(...).
D) the P8-M1 client given a bad address -> "UnsupportedProtocol" / ChatError kind connection_lost.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# the repository root: practice codes/phase 8/m3_ui_configuration/ is three folders below it


def run(code: str, cwd: Path, env_extra: dict | None = None) -> str:
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONWARNINGS": "ignore"}
    env.pop("API_URL", None)
    # start from a clean slate: a real API_URL in your shell would spoil the measurement
    env.update(env_extra or {})
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=120
    )
    tail = " | STDERR: " + done.stderr.strip()[-300:] if done.returncode else ""
    return (done.stdout.strip() + tail).strip()


work = Path(tempfile.mkdtemp(prefix="cfg_probe_"))
(work / ".env").write_text("API_URL=http://from-dotenv:1\n", encoding="utf-8")
show = "from src.core.config import settings; print(settings.api_url)"

print("A) pydantic-settings precedence (cwd has a .env with API_URL=http://from-dotenv:1)")
print("   .env only                 ->", run(show, work))
print("   .env + environment var    ->", run(show, work, {"API_URL": "http://from-env:2"}))
print("   no .env, no env var       ->", run(show, Path(tempfile.mkdtemp(prefix="cfg_empty_"))))
print("   .env, but run from ANOTHER directory ->", run(show, Path(tempfile.gettempdir())))
print("   API_URL set to the empty string ->", repr(run(show, work, {"API_URL": ""})))

print("\nB) a custom key in .streamlit/config.toml")
(work / ".streamlit").mkdir()
(work / ".streamlit" / "config.toml").write_text(
    '[server]\naddress = "127.0.0.1"\n\n[chat]\napi_url = "http://from-streamlit-config:3"\n',
    encoding="utf-8",
)
print(
    "   get_option('chat.api_url') ->",
    run("import streamlit.config as c; print(repr(c.get_option('chat.api_url')))", work),
)
print(
    "   get_option('server.address') ->",
    run("import streamlit.config as c; print(repr(c.get_option('server.address')))", work),
)

print("\nC) st.secrets")
no_secrets = Path(tempfile.mkdtemp(prefix="cfg_nosec_"))
print(
    "   no secrets file, .get()    ->",
    run("import streamlit as st; print(repr(st.secrets.get('api_url', 'DEFAULT')))", no_secrets),
)
(work / ".streamlit" / "secrets.toml").write_text('api_url = "http://from-secrets:4"\n', encoding="utf-8")
print(
    "   with .streamlit/secrets.toml ->",
    run("import streamlit as st; print(repr(st.secrets.get('api_url', 'DEFAULT')))", work),
)

print("\nD) what the P8-M1 client does with bad values (it is still the client the page uses)")
bad = (
    "import logging; logging.disable(logging.CRITICAL)\n"
    "from src.ui import client\n"
    "for value in ['localhost:8000', '', 'ftp://host', 'http://']:\n"
    "    h = client.check_health(value)\n"
    "    try:\n"
    "        client.ask(value, 'q', 1)\n"
    "        a = 'no error?'\n"
    "    except client.ChatError as e:\n"
    "        a = f'{e.kind}: {e.message[:90]}'\n"
    "    print(f'   {value!r:18} health={h.ok} ({h.detail}) | ask -> {a}')\n"
)
print(run(bad, ROOT))
print("\n(Since P8-M3 the page never gets this far: src/ui/endpoint.py refuses these values first.)")
