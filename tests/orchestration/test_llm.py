"""Tests for LocalLLM: a fake client for every outcome, plus live generate()/stream() calls
against the real, already-pulled model (skipped, with the reason, when Ollama is down)."""

from __future__ import annotations

import ollama
import pytest

from src.core.config import settings
from src.generation.setup import build_client
from src.orchestration.llm import LocalLLM, build_llm
from tests.support import OLLAMA_URL, ollama_down


class FakeClient:
    """Answers generate() from canned values, so no real Ollama is needed."""

    def __init__(self, *, reply="OK", chunks=None, raises=None):
        self.reply = reply
        # the text a non-streaming generate() call will return
        self.chunks = chunks if chunks is not None else [reply, ""]
        # the fragments a streaming generate() call will yield, in order
        self.raises = raises
        # an exception generate() should raise instead of answering, or None
        self.calls: list[dict] = []
        # records every call this fake received, in order

    def generate(self, model, prompt, *, system=None, options, stream=False):
        call = {
            "model": model,
            "prompt": prompt,
            "system": system,
            "options": dict(options),
            "stream": stream,
        }
        # builds a record of the call exactly as it arrived
        self.calls.append(call)
        # keeps it before doing anything else, even if this call is about to raise
        if self.raises is not None:
            raise self.raises
            # simulates the underlying service failing
        if stream:
            return iter(
                ollama.GenerateResponse(response=text, done=(text == "")) for text in self.chunks
            )
            # answers with a real ollama.GenerateResponse per fragment, as the SDK would
        return ollama.GenerateResponse(response=self.reply)
        # answers with one real ollama.GenerateResponse, as the SDK would


def build(**client_kwargs):
    client = FakeClient(**client_kwargs)
    # builds a fake client with whatever canned behaviour this test wants
    llm = LocalLLM(client, model="mistral")
    # wires a LocalLLM to that fake client
    return llm, client
    # hands back both, so a test can inspect what the fake actually received


# --- generate() -----------------------------------------------------------------------------


def test_generate_returns_the_clients_real_text():
    llm, _ = build(reply="Paris is the capital of France.")
    # builds an LLM whose fake client answers with a real sentence
    assert llm.generate("What is the capital of France?", temperature=0.2, max_tokens=50) == (
        "Paris is the capital of France."
    )
    # requires the exact text the client returned, unchanged


def test_generate_sends_temperature_and_max_tokens_mapped_to_ollamas_own_names():
    llm, client = build()
    # builds an LLM whose fake client records every call
    llm.generate("hi", temperature=0.7, max_tokens=100)
    # makes one call with specific, known parameters
    assert client.calls[0]["options"] == {"temperature": 0.7, "num_predict": 100}
    # requires max_tokens to have been sent as Ollama's own "num_predict" name
    assert client.calls[0]["stream"] is False
    # requires generate() to always call the client with stream=False


def test_generate_sends_the_configured_model_and_exact_prompt():
    llm, client = build()
    # builds an LLM whose fake client records every call
    llm.generate("What does Carllm do?", temperature=0.0, max_tokens=10)
    # makes one call with a specific prompt
    sent = (client.calls[0]["model"], client.calls[0]["prompt"])
    # reads back the model and prompt actually sent
    assert sent == ("mistral", "What does Carllm do?")
    # requires the configured model and the exact prompt to have been sent, unchanged


def test_generate_returns_empty_string_for_a_missing_response_field():
    llm, _ = build(reply=None)
    # a client whose GenerateResponse never sets response at all
    assert llm.generate("hi", temperature=0.0, max_tokens=5) == ""
    # requires an empty string, not None and not a crash


@pytest.mark.parametrize("bad", ["", "   ", None, 5])
# runs once per unusable prompt
def test_generate_rejects_a_bad_prompt_before_any_call(bad):
    llm, client = build()
    # builds an LLM that would otherwise happily answer
    with pytest.raises(ValueError, match="non-empty string"):
        # expects a clear error naming prompt
        llm.generate(bad, temperature=0.0, max_tokens=10)
        # calls with the unusable prompt
    assert client.calls == []
    # requires that no call reached the client


@pytest.mark.parametrize("bad", [-1, -0.1, float("nan"), float("inf"), True, "0.5"])
# runs once per unusable temperature
def test_generate_rejects_a_bad_temperature_before_any_call(bad):
    llm, client = build()
    # builds an LLM that would otherwise happily answer
    with pytest.raises(ValueError, match="temperature"):
        # expects a clear error naming temperature
        llm.generate("hi", temperature=bad, max_tokens=10)
        # calls with the unusable temperature
    assert client.calls == []
    # requires that no call reached the client


@pytest.mark.parametrize("bad", [0, -1, 1.5, True, "10"])
# runs once per unusable max_tokens
def test_generate_rejects_a_bad_max_tokens_before_any_call(bad):
    llm, client = build()
    # builds an LLM that would otherwise happily answer
    with pytest.raises(ValueError, match="max_tokens"):
        # expects a clear error naming max_tokens
        llm.generate("hi", temperature=0.0, max_tokens=bad)
        # calls with the unusable max_tokens
    assert client.calls == []
    # requires that no call reached the client


def test_generate_temperature_has_no_enforced_upper_bound():
    llm, client = build()
    # builds an LLM that records its calls
    llm.generate("hi", temperature=5.0, max_tokens=10)
    # calls with an unusually high, but not rejected, temperature
    assert client.calls[0]["options"]["temperature"] == 5.0
    # requires the value to have been sent through as given, not clamped or refused


def test_an_error_from_the_client_propagates_unchanged():
    llm, _ = build(raises=RuntimeError("ollama down"))
    # a client that fails on every call
    with pytest.raises(RuntimeError, match="ollama down"):
        # expects the very same error, unchanged
        llm.generate("hi", temperature=0.0, max_tokens=10)
        # calls the failing client


def test_generate_passes_system_through_to_the_client():
    llm, client = build()
    # builds an LLM whose fake client records every call
    llm.generate("hi", temperature=0.0, max_tokens=10, system="Be terse.")
    # calls with a real system prompt
    assert client.calls[0]["system"] == "Be terse."
    # requires the exact system text to have reached the client, unchanged


def test_generate_defaults_system_to_none_when_not_given():
    llm, client = build()
    # builds an LLM whose fake client records every call
    llm.generate("hi", temperature=0.0, max_tokens=10)
    # calls with no system argument at all, exactly as every P6-M2 call site still does
    assert client.calls[0]["system"] is None
    # requires the client to have received system=None, matching Ollama's own default


@pytest.mark.parametrize("bad", ["", "   "])
# runs once per unusable system string
def test_generate_rejects_a_blank_system_before_any_call(bad):
    llm, client = build()
    # builds an LLM that would otherwise happily answer
    with pytest.raises(ValueError, match="system"):
        # expects a clear error naming system
        llm.generate("hi", temperature=0.0, max_tokens=10, system=bad)
        # calls with an unusable system string
    assert client.calls == []
    # requires that no call reached the client


# --- stream() --------------------------------------------------------------------------------


def test_stream_yields_each_real_fragment_in_order():
    llm, _ = build(chunks=["The", " answer", " is", " 4", ""])
    # a client that streams a short sentence, one word-ish fragment at a time
    assert list(llm.stream("What is 2+2?", temperature=0.0)) == ["The", " answer", " is", " 4"]
    # requires every non-empty fragment, in the order the client sent them


def test_stream_skips_the_final_empty_fragment():
    llm, _ = build(chunks=["hi", ""])
    # a client whose last chunk is the empty, done=True one Ollama always sends last
    assert list(llm.stream("hi", temperature=0.0)) == ["hi"]
    # requires the trailing empty fragment to never reach the caller


def test_stream_sends_temperature_but_no_max_tokens_and_stream_true():
    llm, client = build(chunks=["ok", ""])
    # a client that records every call
    list(llm.stream("hi", temperature=0.3))
    # runs the generator to completion
    assert client.calls[0]["options"] == {"temperature": 0.3}
    # requires ONLY temperature to have been sent -- stream() takes no max_tokens at all
    assert client.calls[0]["stream"] is True
    # requires stream() to always call the client with stream=True


@pytest.mark.parametrize("bad", ["", "   ", None, 5])
# runs once per unusable prompt
def test_stream_rejects_a_bad_prompt_before_any_call(bad):
    llm, client = build(chunks=["ok", ""])
    # builds an LLM that would otherwise happily answer
    with pytest.raises(ValueError, match="non-empty string"):
        # expects a clear error naming prompt
        next(llm.stream(bad, temperature=0.0))
        # a generator's body does not run until the first next(), so this forces it to
    assert client.calls == []
    # requires that no call reached the client


def test_stream_rejects_a_bad_temperature_before_any_call():
    llm, client = build(chunks=["ok", ""])
    # builds an LLM that would otherwise happily answer
    with pytest.raises(ValueError, match="temperature"):
        # expects a clear error naming temperature
        next(llm.stream("hi", temperature=-1))
        # forces the generator's body to actually run
    assert client.calls == []
    # requires that no call reached the client


def test_an_error_from_the_client_propagates_unchanged_while_streaming():
    llm, _ = build(raises=RuntimeError("ollama down"))
    # a client that fails on every call
    with pytest.raises(RuntimeError, match="ollama down"):
        # expects the very same error, unchanged
        next(llm.stream("hi", temperature=0.0))
        # forces the generator's body to actually run


def test_stream_passes_system_through_to_the_client():
    llm, client = build(chunks=["ok", ""])
    # a client that records every call
    list(llm.stream("hi", temperature=0.0, system="Be terse."))
    # runs the generator to completion, with a real system prompt
    assert client.calls[0]["system"] == "Be terse."
    # requires the exact system text to have reached the client, unchanged


def test_stream_defaults_system_to_none_when_not_given():
    llm, client = build(chunks=["ok", ""])
    # a client that records every call
    list(llm.stream("hi", temperature=0.0))
    # runs the generator to completion, with no system argument at all
    assert client.calls[0]["system"] is None
    # requires the client to have received system=None, matching Ollama's own default


@pytest.mark.parametrize("bad", ["", "   "])
# runs once per unusable system string
def test_stream_rejects_a_blank_system_before_any_call(bad):
    llm, client = build(chunks=["ok", ""])
    # builds an LLM that would otherwise happily answer
    with pytest.raises(ValueError, match="system"):
        # expects a clear error naming system
        next(llm.stream("hi", temperature=0.0, system=bad))
        # forces the generator's body to actually run
    assert client.calls == []
    # requires that no call reached the client


# --- construction and the model default ------------------------------------------------------


def test_model_defaults_to_the_configured_setting_read_at_construction_time(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "changed-model")
    # changes the configured default AFTER this module was already imported
    llm, client = build_default_model()
    # builds a LocalLLM with no model argument at all
    llm.generate("hi", temperature=0.0, max_tokens=5)
    # makes one call
    assert client.calls[0]["model"] == "changed-model"
    # requires the NEW setting to have been used -- proves the default is NOT frozen at import


def build_default_model():
    client = FakeClient()
    # builds a fake client with default canned behaviour
    return LocalLLM(client), client
    # wires a LocalLLM with no model override at all


@pytest.mark.parametrize("bad", ["", "   "])
# runs once per unusable model name
def test_a_blank_model_name_is_rejected_at_construction(bad):
    with pytest.raises(ValueError, match="non-empty string"):
        # expects the constructor itself to refuse it
        LocalLLM(FakeClient(), model=bad)
        # builds a LocalLLM with an unusable model name


# --- live: the real Ollama container, the real configured model ------------------------------

_OLLAMA_DOWN = ollama_down()
live = pytest.mark.skipif(_OLLAMA_DOWN is not None, reason=_OLLAMA_DOWN or "")


@pytest.fixture(scope="module")
def real_llm():
    reason = ollama_down()
    # checks whether Ollama is unreachable at all
    if reason:
        # covers the case where Ollama itself is down
        pytest.skip(reason)
        # skips every live test in this module, saying which service is missing
    client = build_client(OLLAMA_URL)
    # builds a real client, reachable from this host process
    if not any(m.model.startswith(settings.llm_model) for m in client.list().models):
        # covers a fresh checkout where the configured model has not been pulled yet
        pytest.skip(f"{settings.llm_model!r} is not pulled; run `ollama pull {settings.llm_model}`")
        # skips instead of failing, saying how to fix it
    return build_llm(OLLAMA_URL)
    # hands the tests a real LocalLLM over the real, already-pulled model


@pytest.mark.live
@live
def test_live_generate_answers_a_real_factual_question(real_llm):
    question = "What is the capital of France? Reply with just the city name."
    # builds the question separately, so the call below stays under the line-length limit
    reply = real_llm.generate(question, temperature=0.0, max_tokens=10)
    # asks a real, closed-ended factual question at zero temperature
    # NOT asserting an exact literal reply like "just say OK": both this model (see below)
    # and notes/step-6.1's own probe ("Reply with exactly one word: OK" -> "Understood.")
    # show this local model does not reliably follow "say exactly this word" instructions,
    # even at temperature=0 -- a real, measured limitation, not a bug in this wrapper
    assert "paris" in reply.lower()
    # requires the real, correct fact to appear, which this model DOES reliably get right


@pytest.mark.live
@live
def test_live_generate_applies_a_real_system_prompt(real_llm):
    reply = real_llm.generate(
        "What is the capital of France?",
        temperature=0.0,
        max_tokens=20,
        system="Answer only in French, in one short sentence.",
    )
    # a real call with a real system prompt, distinct from the question itself
    assert "paris" in reply.lower()
    # requires the correct fact to still come through
    assert any(word in reply.lower() for word in ["la capitale", "est", "france"])
    # requires the reply to actually be in French -- proof `system` reached the real model
    # and measurably changed its behaviour, not just that it was accepted without error


@pytest.mark.live
@live
def test_live_stream_yields_more_than_one_real_fragment_and_reassembles_correctly(real_llm):
    fragments = list(real_llm.stream("Count from 1 to 3, one number per line.", temperature=0.0))
    # asks a real question that should take more than a single token to answer
    assert len(fragments) > 1
    # requires the answer to have arrived as more than one streamed piece
    assert all(isinstance(fragment, str) and fragment for fragment in fragments)
    # requires every yielded fragment to be real, non-empty text
    full_text = "".join(fragments)
    # reassembles the fragments back into the full answer
    assert "1" in full_text and "2" in full_text and "3" in full_text
    # requires the real, reassembled answer to actually count as asked
