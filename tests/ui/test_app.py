"""Tests for src/ui/app.py (P8-M1): the Streamlit chat page, run by Streamlit's own AppTest harness.

The page is executed for real -- every widget, every rerun -- with only the API replaced:
`src.ui.client.ask` and `check_health` are patched to answer from `httpx.MockTransport`, so the
real SSE reader runs too. AppTest only shows the tree once the script has settled, so the one
transient state worth asserting (the input being locked while an answer is fetched) is observed by
recording the arguments of every `st.chat_input` call.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src.core.config import Settings, settings
from src.ui import app as page
from src.ui import client
from tests.ui.support import Chunks, api_returning, envelope, frame, stream_of

APP_FILE = Path(__file__).resolve().parents[2] / "src" / "ui" / "app.py"
REAL_ASK = client.ask
# captured before any test patches it: the fake below still runs the real SSE reader through it

ASCII_PUNCTUATION = r"[!-/:-@\[-`{-~]"

API_URL = "http://api.test"
# the address every page test runs against; the `api` fixture installs it as the configured one, so
# the tests never depend on (or leak) the developer's own .env, and it differs from the built-in
# default, so a page that ignored the setting would be caught


class FakeApi:
    """Stands in for the API: records every question and answers from a scripted response."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, str, int]] = []
        # (base_url, query, k) of every question the page sent
        self.busy_when_asked: list[bool] = []
        # the page's `busy` flag at the moment each question was sent
        self.response: httpx.Response | Exception = stream_of("Hello")
        # what /chat answers next
        self.healthy = True
        self.health_checked: list[str] = []
        # the base_url of every /health check the sidebar made
        self.clients: list[httpx.Client] = []

    def ask(self, base_url, query, k, **_):
        self.asked.append((base_url, query, k))
        self.busy_when_asked.append(st.session_state.chat.busy)
        http = api_returning(self.response)
        self.clients.append(http)
        return REAL_ASK(base_url, query, k, http_client=http)

    def check_health(self, base_url, **_):
        self.health_checked.append(base_url)
        if self.healthy:
            return client.Health(True, "ok")
        return client.Health(False, "cannot connect (ConnectError)")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "api_url", API_URL)
    # the page reads the configured address from this shared object; undone after each test
    fake = FakeApi()
    monkeypatch.setattr(client, "ask", fake.ask)
    monkeypatch.setattr(client, "check_health", fake.check_health)
    yield fake
    for http in fake.clients:
        http.close()


def open_page() -> AppTest:
    at = AppTest.from_file(str(APP_FILE), default_timeout=20)
    at.run()
    assert not at.exception, [error.value for error in at.exception]
    return at


def ask(at: AppTest, question: str) -> AppTest:
    at.chat_input[0].set_value(question).run()
    assert not at.exception, [error.value for error in at.exception]
    return at


# --- the page opens ----------------------------------------------------------------------------


def test_the_page_opens_empty_with_the_defaults(api):
    at = open_page()
    assert len(at.chat_message) == 0
    slider = at.sidebar.slider[0]
    assert (slider.min, slider.max, slider.value) == (page.K_MIN, page.K_MAX, page.K_DEFAULT)
    assert (page.K_MIN, page.K_MAX, page.K_DEFAULT) == (1, 5, 3)
    assert API_URL in at.sidebar.success[0].value
    # requires the sidebar to say which API the page talks to, and that it answered /health
    assert any("answered on its own" in caption.value for caption in at.caption)
    # requires the page to say it keeps no conversation memory (the API takes one question)
    assert api.asked == []


def test_a_down_api_is_called_out_in_the_sidebar_with_how_to_start_it(api):
    api.healthy = False
    at = open_page()
    message = at.sidebar.error[0].value
    assert API_URL in message
    assert "uvicorn" in message
    assert len(at.sidebar.success) == 0


# --- where the page finds the API (P8-M3) ------------------------------------------------------


def test_the_address_the_tests_use_differs_from_the_built_in_default():
    assert API_URL != Settings.model_fields["api_url"].default
    # requires the tests above, which compare against API_URL, to notice a page that ignored the
    # setting and fell back on the default


@pytest.mark.parametrize("bad", ["localhost:8000", "", "ftp://host", "http://user:secret@host"])
def test_an_unusable_address_stops_the_page_before_any_widget_or_request(api, monkeypatch, bad):
    monkeypatch.setattr(settings, "api_url", bad)
    at = open_page()
    assert len(at.error) == 1
    assert page.as_plain_text("API_URL") in at.error[0].value
    # requires the error to name the setting to change (escaped, like all text from outside)
    assert "secret" not in at.error[0].value
    # requires a password inside the address never to be printed back
    assert len(at.chat_input) == 0
    assert len(at.sidebar.slider) == 0
    # requires no chat widgets: a question typed into them could only fail
    assert api.health_checked == []
    assert api.asked == []
    # requires the API never to be contacted with an address known to be unusable


def test_the_address_in_an_error_is_shown_as_plain_text(api, monkeypatch):
    monkeypatch.setattr(settings, "api_url", "ftp://a_b*c")
    at = open_page()
    assert page.as_plain_text("ftp://a_b*c") in at.error[0].value
    # requires the address, which a person typed, to be escaped like any other untrusted text:
    # unescaped, the underscore and the asterisk would turn into emphasis


def test_a_trailing_slash_is_tidied_before_the_address_is_used(api, monkeypatch):
    monkeypatch.setattr(settings, "api_url", API_URL + "/")
    at = ask(open_page(), "q")
    assert at.sidebar.success[0].value == f"API reachable at {API_URL}"
    assert {url for url in api.health_checked} == {API_URL}
    assert [url for url, _, _ in api.asked] == [API_URL]
    # requires every request to get the tidied address, or "<base>//chat" would reach the API


# --- asking a question-------------------------------------------------------------------------


def test_a_question_is_answered_with_its_text_and_a_sources_expander(api):
    api.response = stream_of(
        "Avery Lancaster ",
        "founded Insurellm",
        "\n\nSources:\n[1] About (company/about.md)\n[2] Avery (employees/avery.md)",
    )
    at = ask(open_page(), "Who founded Insurellm?")
    assert api.asked == [(API_URL, "Who founded Insurellm?", page.K_DEFAULT)]
    assert [message.name for message in at.chat_message] == ["user", "assistant"]
    assert at.chat_message[0].markdown[0].value == page.as_plain_text("Who founded Insurellm?")
    assert at.chat_message[1].markdown[0].value == page.as_plain_text(
        "Avery Lancaster founded Insurellm"
    )
    # requires the answer text WITHOUT the source list, which is held apart by the client
    assert [expander.label for expander in at.expander] == ["Sources (2)"]
    listed = at.expander[0].markdown[0].value
    assert page.as_plain_text("[1] About (company/about.md)") in listed
    assert page.as_plain_text("[2] Avery (employees/avery.md)") in listed
    assert len(at.error) == 0


def test_the_slider_value_is_the_k_that_is_sent(api):
    at = open_page()
    at.sidebar.slider[0].set_value(2).run()
    ask(at, "q")
    assert api.asked[0][2] == 2


def test_a_blank_question_is_ignored(api):
    at = ask(open_page(), "   ")
    assert api.asked == []
    assert len(at.chat_message) == 0


def test_the_question_is_trimmed_before_it_is_sent(api):
    ask(open_page(), "  padded question  ")
    assert api.asked[0][1] == "padded question"


def test_each_question_is_sent_on_its_own(api):
    at = open_page()
    ask(at, "first question")
    ask(at, "second question")
    assert [question for _, question, _ in api.asked] == ["first question", "second question"]
    # requires no earlier turn to be mixed into a later question: the API has no memory
    assert [message.name for message in at.chat_message] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_a_rerun_after_an_answer_does_not_ask_again(api):
    at = open_page()
    ask(at, "only once")
    at.sidebar.slider[0].set_value(4).run()
    assert len(api.asked) == 1
    # requires the pending question to be consumed, so any later rerun leaves the API alone


# --- the input is locked while an answer is fetched -----------------------------------------------


def test_the_input_is_locked_only_while_the_answer_is_being_fetched(api, monkeypatch):
    calls: list[dict] = []
    original = st.chat_input

    def recording(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(st, "chat_input", recording)
    at = open_page()
    calls.clear()
    ask(at, "q")
    assert [call["disabled"] for call in calls] == [False, True, False]
    # requires: the run that receives the question (idle), the run that fetches the answer
    # (locked), and the run after it (idle again)
    assert api.busy_when_asked == [True]
    assert all(call["max_chars"] == page.MAX_QUESTION_CHARS for call in calls)
    assert at.session_state.chat.busy is False


# --- failures ----------------------------------------------------------------------------------


def test_an_in_band_error_keeps_the_partial_text_and_explains_itself(api):
    api.response = stream_of("Partial ", ending="[ERROR] timed out")
    at = ask(open_page(), "q")
    assert at.chat_message[1].markdown[0].value == page.as_plain_text("Partial")
    # (Streamlit strips surrounding whitespace from markdown, so the trailing space is not shown)
    assert "smaller k" in at.error[0].value
    assert at.session_state.chat.messages[-1]["error"] is not None
    assert at.session_state.chat.busy is False


def test_an_http_error_is_shown_and_the_page_stays_usable(api):
    api.response = envelope(422, "validation_error", "body.k: Input should be greater than 0")
    at = ask(open_page(), "q")
    assert page.as_plain_text("body.k") in at.error[0].value
    assert at.chat_message[1].markdown == []
    assert at.session_state.chat.busy is False
    api.response = stream_of("Fine now")
    ask(at, "again")
    assert at.chat_message[3].markdown[0].value == page.as_plain_text("Fine now")


def test_an_unreachable_api_is_reported_where_the_answer_would_be(api):
    api.response = httpx.ConnectError("refused")
    at = ask(open_page(), "q")
    assert page.as_plain_text(API_URL) in at.error[0].value
    assert at.session_state.chat.busy is False


def test_an_interruption_mid_answer_closes_the_connection_and_unlocks_the_page(api):
    class Interrupted(Chunks):
        def __iter__(self):
            yield from self.pieces
            st.stop()
            # what Streamlit does to a running script when the user presses its own Stop button

    body = Interrupted([frame("Partial ")])
    api.response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    at = AppTest.from_file(str(APP_FILE), default_timeout=20)
    at.run()
    at.chat_input[0].set_value("q").run()
    assert body.closed is True
    # requires the connection to be released at once, not whenever a garbage collection runs
    assert at.session_state.chat.busy is False
    # requires the lock to be released, or the input would stay disabled until a manual rerun
    assert at.session_state.chat.messages[-1]["content"] == "Partial "
    # requires the part of the answer that did arrive to be kept
    at.run()
    assert at.chat_input[0].disabled is False
    assert len(api.asked) == 1
    assert [message.name for message in at.chat_message] == ["user", "assistant"]
    # requires the next run (Streamlit's "Rerun") to find an unlocked input and the partial answer
    # in the history, and NOT to ask the question again


def test_an_interruption_between_fragments_still_closes_the_connection_at_once(api, monkeypatch):
    kept = []

    def interrupting(fragments):
        kept.append(fragments)
        # a strong reference, so the suspended generator cannot be finalised by chance
        next(iter(fragments))
        st.stop()
        # the interrupt lands in Streamlit's code, BETWEEN fragments, while the client's generator
        # is merely suspended and its own `finally` has not run

    monkeypatch.setattr(st, "write_stream", interrupting)
    body = Chunks([frame("Partial ", "more")])
    api.response = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)
    at = open_page()
    at.chat_input[0].set_value("q").run()
    assert body.closed is True
    # requires the PAGE to release the connection itself: nothing else closes it until a garbage
    # collection finalises the generator (gap G1 in docs/api-contract.md, on the client side)
    assert at.session_state.chat.busy is False


def test_the_fragments_handed_to_the_typewriter_are_already_escaped(api, monkeypatch):
    seen: list[str] = []
    original = st.write_stream

    def recording(fragments):
        listed = list(fragments)
        seen.extend(listed)
        return original(iter(listed))

    monkeypatch.setattr(st, "write_stream", recording)
    api.response = stream_of("Costs $10", ",000 and ![x](u)")
    ask(open_page(), "q")
    assert seen == [page.as_plain_text("Costs $10"), page.as_plain_text(",000 and ![x](u)")]
    # requires the text to be safe WHILE it is typed, not only after the redraw that follows


def test_a_locked_page_with_nothing_to_answer_unlocks_itself(api):
    at = AppTest.from_file(str(APP_FILE), default_timeout=20)
    at.session_state["chat"] = page.Chat(busy=True, pending=None)
    at.run()
    assert not at.exception
    assert api.asked == []
    assert at.session_state.chat.busy is False
    # requires the stale state (locked, nothing pending) to unlock rather than send a None question


# --- history -----------------------------------------------------------------------------------


def test_new_chat_clears_the_history(api):
    at = ask(open_page(), "q")
    assert len(at.chat_message) == 2
    at.sidebar.button[0].click().run()
    assert len(at.chat_message) == 0
    assert at.session_state.chat.messages == []


# --- safe rendering ----------------------------------------------------------------------------


def test_prices_and_markdown_in_an_answer_are_shown_literally(api):
    nasty = (
        "Pricing is $10,000 per month or $12,000/month. "
        "![pixel](http://evil.example/p.png) :red[x] **bold** <b>tag</b>"
    )
    api.response = stream_of(nasty)
    at = ask(open_page(), "q")
    shown = at.chat_message[1].markdown[0].value
    assert "\\$10" in shown and "\\$12" in shown
    # requires the dollar signs to be escaped, or Streamlit would read $10,000 ... $12 as maths
    for markup in ("![", "](", ":red[", "**", "<b>"):
        assert markup not in shown
    # requires every piece of markdown a model could emit to be inert: no image to load, no
    # link, no colour directive, no emphasis, no HTML


@pytest.mark.parametrize(
    "text",
    [
        "plain words",
        "$10,000 and $12,000/month",
        "![x](http://evil.example/p.png)",
        "[a link](http://example.com) and :smile: and :red[colour]",
        "# heading\n- bullet\n1. numbered\n> quote\n`code` **bold** _italic_ ~~strike~~",
        "back\\slash and <html> & entities &amp;",
        "unicode: Réponse ✓ 你好 — – …",
        "".join(chr(code) for code in range(33, 127)),
        "",
    ],
)
def test_as_plain_text_leaves_markdown_nothing_to_interpret(text):
    shown = page.as_plain_text(text)
    without_escaped_pairs = re.sub(r"\\" + ASCII_PUNCTUATION, "", shown)
    assert re.search(ASCII_PUNCTUATION, without_escaped_pairs) is None
    # requires every ASCII punctuation character to be backslash-escaped, so no markdown syntax
    # (and none of Streamlit's own :directives: or $maths$) survives


def test_as_plain_text_keeps_line_breaks_and_non_ascii_text():
    assert page.as_plain_text("line one\nline two") == "line one  \nline two"
    # requires a single newline to stay a line break (two spaces + newline is markdown's hard break)
    assert page.as_plain_text("Réponse ✓ 你好") == "Réponse ✓ 你好"
    assert page.as_plain_text("") == ""
