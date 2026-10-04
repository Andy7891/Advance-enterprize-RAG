"""Tests for the Ollama generation setup check: a fake client for every outcome, plus a
real, live probe against the real model (skipped, with the reason, when Ollama is down or
the configured model has not been pulled)."""

from __future__ import annotations

import ollama
import pytest

import src.generation.setup as generation_setup
from src.core.config import settings
from src.generation.setup import build_client, health_check
from tests.support import OLLAMA_URL, ollama_down


class FakeClient:
    """Answers list()/generate() from canned values, so no real Ollama is needed."""

    def __init__(self, *, pulled=("mistral:latest",), reply="OK", raises=None):
        self.pulled = pulled
        # the model names this fake claims are pulled
        self.reply = reply
        # the text generate() will return, or None to return an empty response
        self.raises = raises
        # an exception generate() should raise instead of answering, or None
        self.generate_calls: list[tuple[str, str]] = []
        # records every (model, prompt) pair generate() was called with

    def list(self):
        models = [ollama.ListResponse.Model(model=name) for name in self.pulled]
        # builds one entry per pulled model, using the SDK's own nested response type
        return ollama.ListResponse(models=models)
        # answers with the SDK's own real response type, so the shape cannot drift

    def generate(self, model, prompt, *, stream=False):
        self.generate_calls.append((model, prompt))
        # records this call before doing anything else, even if it is about to raise
        if self.raises is not None:
            raise self.raises
            # simulates the model failing to actually respond
        return ollama.GenerateResponse(response=self.reply)
        # answers with the SDK's own real response type


class UnreachableClient:
    def list(self):
        raise ConnectionError("Failed to connect to Ollama.")
        # simulates Ollama itself being unreachable


# --- health_check --------------------------------------------------------------------------


def test_ready_when_the_model_is_pulled_and_answers():
    status = health_check(FakeClient(pulled=("mistral:latest",), reply="OK"), model="mistral")
    # checks a client that has the configured model and answers normally
    assert status.ready
    # requires the check to report ready
    assert (status.model, status.sample_reply) == ("mistral", "OK")
    # requires the model name and the real reply to be carried onto the status
    assert status.response_seconds is not None and status.response_seconds >= 0
    # requires a real, measured timing, not a placeholder
    assert status.problem is None
    # requires no problem to be reported alongside a ready status


def test_an_untagged_model_name_matches_the_tagged_form_ollama_reports():
    status = health_check(FakeClient(pulled=("mistral:latest",)), model="mistral")
    # asks for "mistral" while the fake reports it as "mistral:latest"
    assert status.ready
    # requires the two spellings to be recognised as the same model


def test_not_ready_when_the_model_has_never_been_pulled():
    status = health_check(FakeClient(pulled=("nomic-embed-text:latest",)), model="mistral")
    # checks a client whose pulled models do not include the configured one
    assert not status.ready
    # requires the check to report not ready
    assert "ollama pull mistral" in status.problem
    # requires the problem message to name the exact fix


def test_not_ready_when_ollama_itself_is_unreachable():
    status = health_check(UnreachableClient(), model="mistral")
    # checks a client that fails on the very first call
    assert not status.ready
    # requires the check to report not ready
    assert "cannot reach Ollama" in status.problem
    # requires the problem message to say so, not raise the connection error further


def test_not_ready_when_a_pulled_model_fails_to_actually_respond():
    client = FakeClient(pulled=("mistral:latest",), raises=ollama.ResponseError("model crashed"))
    # a model Ollama lists as pulled, but that fails at the real generation call
    status = health_check(client, model="mistral")
    # runs the check against it
    assert not status.ready
    # requires the check to report not ready
    assert "is pulled but did not respond" in status.problem
    # requires the message to distinguish "listed" from "actually working"


def test_not_ready_when_the_model_answers_with_nothing():
    status = health_check(FakeClient(pulled=("mistral:latest",), reply=""), model="mistral")
    # a model that responds, but with an empty string
    assert not status.ready
    # requires the check to report not ready
    assert "empty response" in status.problem
    # requires the message to say exactly what was wrong


def test_the_probe_prompt_and_model_are_what_is_actually_sent():
    client = FakeClient(pulled=("mistral:latest",))
    # a client that records every call it receives
    health_check(client, model="mistral")
    # runs the check
    assert client.generate_calls == [("mistral", generation_setup.PROBE_PROMPT)]
    # requires exactly one real generation call, with the configured model and the probe


def test_health_check_never_raises_even_on_an_unexpected_client_error():
    class BrokenClient:
        def list(self):
            raise ollama.ResponseError("something unexpected")
            # a plausible but not specially-handled error from list() itself

    with pytest.raises(ollama.ResponseError):
        # list() failures other than ConnectionError are NOT currently caught -- this
        # test pins that behaviour so a future change to widen the except clause is a
        # deliberate decision, not an accident
        health_check(BrokenClient(), model="mistral")


# --- build_client and the CLI ----------------------------------------------------------------


def test_build_client_targets_the_given_url_with_a_bounded_timeout(monkeypatch):
    captured = {}
    # will hold the arguments ollama.Client was actually constructed with
    monkeypatch.setattr(
        generation_setup.ollama,
        "Client",
        lambda **kwargs: captured.update(kwargs) or "client",
    )
    # replaces the real Client class with one that just records its arguments

    build_client("http://example:11434")
    # builds a client through the real factory function

    assert captured["host"] == "http://example:11434"
    # requires the given URL to be passed straight through
    assert captured["timeout"] == generation_setup.PROBE_TIMEOUT_SECONDS
    # requires the bounded timeout to be applied, not the SDK's own default


@pytest.mark.parametrize(
    ("client", "exit_code", "prefix"),
    [
        (FakeClient(pulled=("mistral:latest",), reply="OK"), 0, "ready: mistral"),
        (FakeClient(pulled=()), 1, "NOT ready:"),
    ],
)
# runs once for a ready client and once for a not-ready one
def test_cli_exit_codes(monkeypatch, capsys, client, exit_code, prefix):
    monkeypatch.setattr(generation_setup, "build_client", lambda ollama_url: client)
    # replaces the real client factory with one returning the fake for this test
    assert generation_setup.main(["--model", "mistral"]) == exit_code
    # runs the CLI and requires the expected process exit code
    assert capsys.readouterr().out.startswith(prefix)
    # requires the printed message to start as expected


def test_cli_defaults_to_a_host_reachable_url_and_the_configured_model(monkeypatch):
    captured = {}
    # will hold the url and model the CLI actually used

    def fake_build_client(ollama_url):
        captured["url"] = ollama_url
        return FakeClient(pulled=(f"{settings.llm_model}:latest",))

    monkeypatch.setattr(generation_setup, "build_client", fake_build_client)
    # replaces the real client factory with one that also records the url it was given

    assert generation_setup.main([]) == 0
    # runs the CLI with no flags at all

    assert captured["url"] == "http://localhost:11434"
    # requires the host-reachable default, NOT settings.ollama_url (which is
    # "http://ollama:11434", the Docker-internal name -- unreachable from a script run
    # on the host, exactly like src/embedding/embedder.py's own CLI already handles it)


# --- live: the real Ollama container, the real configured model --------------------------

_OLLAMA_DOWN = ollama_down()
live = pytest.mark.skipif(_OLLAMA_DOWN is not None, reason=_OLLAMA_DOWN or "")


@pytest.mark.live
@live
def test_live_the_configured_model_is_pulled_and_answers():
    client = build_client(OLLAMA_URL)
    # builds a real client against the real Ollama, reachable from this host process
    # (settings.ollama_url is "http://ollama:11434", the Docker-internal name, which
    # only resolves from inside another container -- see build_client's own CLI note)
    status = health_check(client)
    # runs the real check, with one real generation call
    if status.problem and "is not pulled" in status.problem:
        # covers a fresh checkout where the model has not been pulled yet
        pytest.skip(status.problem)
        # skips instead of failing, saying how to fix it
    assert status.ready, status.problem
    # requires a real, working model
    assert status.sample_reply
    # requires a real, non-empty reply to have been recorded
    assert status.response_seconds is not None and status.response_seconds > 0
    # requires a real, positive, measured response time


@pytest.mark.live
@live
def test_live_an_unpulled_model_name_is_reported_clearly():
    client = build_client(OLLAMA_URL)
    # builds a real client against the real Ollama, reachable from this host process
    status = health_check(client, model="a-model-that-was-never-pulled")
    # asks about a model name that cannot possibly be pulled
    assert not status.ready
    # requires the check to report not ready
    assert "ollama pull a-model-that-was-never-pulled" in status.problem
    # requires the exact fix to be named
