"""Where the chat page finds the API (P8-M3).

The page does not hard-code the API's address. It reads `settings.api_url` from src/core/config.py,
a pydantic-settings field filled from the first of these that exists:

    1. an environment variable named API_URL,
    2. a line `API_URL=...` in the file `.env`, looked up in the CURRENT folder (so start the page
       from the repository root; from anywhere else the file is silently not found),
    3. the default in the code, `http://localhost:8000`, where `uvicorn src.api.main:app` listens.

The settings are read once, when the process starts, so changing `.env` needs a restart of
`streamlit run`. Streamlit's own `config.toml` cannot hold a custom key and `st.secrets` is for
secrets, which is why neither is used (notes/step-8.3-ui-configuration.md has the measurements).

`Settings` is shared by the API, the ingestion scripts and this page, so it deliberately does not
validate `api_url`: a typo in a setting only the UI uses must not stop the API from starting. The
check lives here, and the page shows the problem instead of a chat that could only fail.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from src.core.config import settings

HOW_TO_FIX = (
    "Set API_URL in .env or in the environment, e.g. API_URL=http://localhost:8000, "
    "then restart streamlit run (settings are read once, when it starts)."
)
# appended to every message: where the setting lives, and that reloading the browser is not enough
PATHS_THE_CLIENT_ADDS = ("/chat", "/health")
# src/ui/client.py appends these itself; an address that already ends in one would get it twice
MAX_PORT = 65535


class ConfigError(ValueError):
    """The configured API address cannot be used; `message` says why and how to fix it."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        # kept apart from str(self) so the page can show it without any exception wrapping


def _echo(value: str) -> str:
    """The value for a message, or a note that it is withheld.

    An address can carry a secret: a password in `user:password@host`, or a token after a `?` or
    `#`. The message ends up on a web page (and in anything pasted from it), so such a value is
    never repeated.
    """
    if any(mark in value for mark in "@?#"):
        return " (the value is not shown because it may hold a password or a token)"
    return f" (got {value!r})"


def _problem(what: str, value: str | None = None) -> ConfigError:
    shown = "" if value is None else _echo(value)
    return ConfigError(f"API_URL {what}{shown}. {HOW_TO_FIX}")


def normalize_api_url(value: str) -> str:
    """The API's base address in the exact form src/ui/client.py appends "/chat" to.

    It catches the mistakes people actually make (no scheme, a stray space, the endpoint pasted in,
    a password in the address) and tidies two harmless ones (padding and trailing slashes). It does
    not prove the API is there: the sidebar's /health check does that on every run.
    """
    text = value.strip()
    if not text:
        raise _problem("is empty")
    if any(character.isspace() or not character.isprintable() for character in text):
        raise _problem("must not contain spaces or control characters", text)
        # checked first because urlsplit silently deletes tabs and newlines instead of failing
    if "?" in text or "#" in text:
        raise _problem("must not contain a query or fragment (nothing after a ? or #)", text)
    try:
        parts = urlsplit(text)
    except ValueError:
        raise _problem("is not a valid address", text) from None
        # urlsplit rejects, for example, an IPv6 address with a missing closing bracket
    if parts.scheme not in ("http", "https"):
        raise _problem("must start with http:// or https://", text)
        # "localhost:8000" parses as scheme "localhost", which is how this mistake lands here
    if "@" in parts.netloc:
        raise _problem("must not contain a username or password", text)
    if not parts.hostname:
        raise _problem("needs a host name after the scheme, such as localhost", text)
    try:
        port = parts.port
    except ValueError:
        port = -1
        # a port that is not a number, or above 65535, raises when it is read
    if port is not None and not 1 <= port <= MAX_PORT:
        raise _problem(f"has an invalid port: use a whole number from 1 to {MAX_PORT}", text)
    path = parts.path.rstrip("/")
    last_segment = "/" + path.rsplit("/", 1)[-1]
    if last_segment in PATHS_THE_CLIENT_ADDS:
        raise _problem(
            f"must be the address of the API itself, without {last_segment}, which the page adds",
            text,
        )
    return f"{parts.scheme}://{parts.netloc}{path}"
    # scheme in lower case, host exactly as given, no trailing slash: "<this>/chat" is then right


def configured_api_url() -> str:
    """The usable, tidied address from the settings; raises `ConfigError` if there is none."""
    return normalize_api_url(settings.api_url)
    # read from the settings object on every call: Streamlit re-runs the page after every click
