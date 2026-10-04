"""Tests for src/ui/endpoint.py (P8-M3): where the chat page finds the API.

Two things are pinned here. First, the check that turns a mistyped address into a clear message
instead of httpx's confusing "UnsupportedProtocol" (measured before this step: `localhost:8000`
without a scheme made the sidebar say "Cannot reach the API" and classified it as a lost
connection). Second, the contract of the value's SOURCES, measured with pydantic-settings:
environment variable, then `.env`, then the default -- and `.env` is looked up in the CURRENT
folder only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config import Settings, settings
from src.ui import endpoint

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT = "http://localhost:8000"
# the default the code is documented to have; test_the_documented_default_is_the_real_default
# keeps this honest against Settings itself


# --- good addresses ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "tidied"),
    [
        pytest.param("http://localhost:8000", "http://localhost:8000", id="plain"),
        pytest.param("http://localhost:8000/", "http://localhost:8000", id="trailing-slash"),
        pytest.param("http://localhost:8000///", "http://localhost:8000", id="many-slashes"),
        pytest.param("  http://localhost:8000  ", "http://localhost:8000", id="padded"),
        pytest.param("https://rag.example.com", "https://rag.example.com", id="https-no-port"),
        pytest.param("HTTP://LocalHost:8000", "http://LocalHost:8000", id="scheme-lowercased"),
        pytest.param("http://127.0.0.1:8000", "http://127.0.0.1:8000", id="ipv4"),
        pytest.param("http://[::1]:8000", "http://[::1]:8000", id="ipv6-literal"),
        pytest.param("http://api:8000", "http://api:8000", id="compose-service-name"),
        pytest.param("http://host/api", "http://host/api", id="path-prefix"),
        pytest.param("http://host/api/", "http://host/api", id="path-prefix-trailing-slash"),
    ],
)
def test_a_usable_address_is_accepted_and_tidied(given, tidied):
    assert endpoint.normalize_api_url(given) == tidied
    # requires the result to be exactly what client.py appends "/chat" and "/health" to


# --- bad addresses -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "says"),
    [
        pytest.param("", "empty", id="empty"),
        pytest.param("   ", "empty", id="blank"),
        pytest.param("localhost:8000", "http:// or https://", id="no-scheme"),
        pytest.param("//localhost:8000", "http:// or https://", id="scheme-relative"),
        pytest.param("ftp://host", "http:// or https://", id="wrong-scheme"),
        pytest.param("http://", "host name", id="no-host"),
        pytest.param("http:///path", "host name", id="empty-host"),
        pytest.param("http://host:abc", "port", id="port-not-a-number"),
        pytest.param("http://host:99999", "port", id="port-out-of-range"),
        pytest.param("http://[::1", "valid address", id="broken-ipv6"),
        pytest.param("http://host name", "spaces", id="inner-space"),
        pytest.param("http://host/?a=1", "query or fragment", id="query"),
        pytest.param("http://host/#top", "query or fragment", id="fragment"),
        pytest.param("http://user:secret@host", "username or password", id="credentials"),
        pytest.param("http://localhost:8000/chat", "/chat", id="the-endpoint-pasted-in"),
        pytest.param("http://localhost:8000/chat/", "/chat", id="the-endpoint-with-slash"),
        pytest.param("http://localhost:8000/health", "/health", id="the-health-endpoint"),
        pytest.param("http://host:0", "port", id="port-zero"),
    ],
)
def test_a_mistyped_address_is_refused_with_a_message_that_says_what_is_wrong(given, says):
    with pytest.raises(endpoint.ConfigError) as problem:
        endpoint.normalize_api_url(given)
    assert says in problem.value.message
    assert "API_URL" in problem.value.message
    assert ".env" in problem.value.message
    # requires every message to say HOW to fix it, not only what is wrong
    assert "restart" in problem.value.message
    # requires the reminder that settings are read once: reloading the browser does not re-read them


@pytest.mark.parametrize(
    "given",
    [
        "http://user:secret@host",
        "user:secret@host:8000",
        "http://host/?token=secret",
        "http://host/#secret",
    ],
)
def test_a_message_never_repeats_a_password_or_a_token(given):
    with pytest.raises(endpoint.ConfigError) as problem:
        endpoint.normalize_api_url(given)
    assert "secret" not in problem.value.message
    # requires the page, which displays this message, never to print a credential back


def test_a_config_error_is_a_value_error_with_its_message_as_text():
    with pytest.raises(ValueError, match="empty"):
        endpoint.normalize_api_url("")


# --- reading the configured value --------------------------------------------------------------


def test_the_configured_address_is_read_at_call_time_and_tidied(monkeypatch):
    monkeypatch.setattr(settings, "api_url", "http://example.test:9000/")
    assert endpoint.configured_api_url() == "http://example.test:9000"
    monkeypatch.setattr(settings, "api_url", "example.test")
    with pytest.raises(endpoint.ConfigError):
        endpoint.configured_api_url()
    # requires the check to run on the CURRENT setting every time, since Streamlit re-runs the page


# --- where the value comes from ----------------------------------------------------------------


def test_environment_beats_dotenv_beats_the_default(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("API_URL=http://from-dotenv:1\n", encoding="utf-8")
    monkeypatch.delenv("API_URL", raising=False)
    assert Settings(_env_file=dotenv).api_url == "http://from-dotenv:1"
    # the .env file alone

    monkeypatch.setenv("API_URL", "http://from-env:2")
    assert Settings(_env_file=dotenv).api_url == "http://from-env:2"
    # an environment variable of the same name wins over the file

    monkeypatch.delenv("API_URL")
    assert Settings(_env_file=tmp_path / "missing.env").api_url == DEFAULT
    # neither: the default in the code


def test_the_dotenv_file_is_looked_up_in_the_current_folder_only(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("API_URL=http://from-project:5\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.delenv("API_URL", raising=False)

    monkeypatch.chdir(project)
    assert Settings().api_url == "http://from-project:5"
    monkeypatch.chdir(elsewhere)
    assert Settings().api_url == DEFAULT
    # requires the documented pitfall to stay true (and visible): started from another folder, the
    # page silently ignores `.env`. README.md says "start Streamlit from the repository root"


def test_settings_itself_accepts_an_empty_value_so_the_check_must_live_in_the_page(monkeypatch):
    monkeypatch.setenv("API_URL", "")
    assert Settings(_env_file=None).api_url == ""
    # requires the boundary decided in D-2026-10-04-02: Settings stays unvalidated, so a typo in a
    # UI-only setting cannot stop the API server or a script from starting


# --- the documentation agrees with the code ----------------------------------------------------


def test_the_documented_default_is_the_real_default():
    assert Settings.model_fields["api_url"].default == DEFAULT


def test_env_example_documents_api_url_with_the_real_default():
    text = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"API_URL={DEFAULT}" in text
    # requires the template to show the variable and the value the code really falls back to


def test_the_readme_configuration_table_names_the_variable_and_its_default():
    text = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Configuration" in text
    assert "API_URL" in text and DEFAULT in text
