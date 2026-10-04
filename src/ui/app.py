"""Streamlit chat page for the RAG API (P8-M1).

Run it from the repository root with the API already running:

    streamlit run src/ui/app.py

The API's address is the setting API_URL (default http://localhost:8000), read from the environment
or the `.env` file; src/ui/endpoint.py explains the order and checks the value.

How a Streamlit page works, because the whole design follows from it: Streamlit re-executes THIS
FILE, top to bottom, on every interaction (a submitted question, a slider move, a button click).
Nothing survives between runs except `st.session_state`. So a chat is kept as an object in session
state, and a question takes TWO runs: the run that receives it records it and locks the input, then
reruns; the next run (the "busy" run) fetches and streams the answer, unlocks the input and reruns
once more.

One rule shapes the cleanup code. When Streamlit interrupts a running script (its own Stop button, a
rerun request), every `st.*` call and every `st.session_state` access raises again, even inside a
`finally`. So whatever must happen on interruption is done on the plain `Chat` object, never
through `st`.

All talking to the API happens in src/ui/client.py; this file only draws the page.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

import streamlit as st

from src.ui import client, endpoint

PAGE_TITLE = "Advance RAG"
K_MIN, K_MAX, K_DEFAULT = 1, 5, 3
# k = how many chunks the API retrieves. The default is the API's own; the upper bound is a
# measurement from this CPU-only host, where k=5 with a broad question exceeded the API's 300 s
# timeout
MAX_QUESTION_CHARS = 1000
# the API puts no limit on a question (gap G3 in docs/api-contract.md), so the page does
PLACEHOLDER = "Ask a question about the documents"
INDEPENDENT_NOTE = (
    "Each question is answered on its own: the assistant does not remember earlier turns."
)
WAITING_TEXT = (
    "Retrieving sources and waiting for the model. "
    "The first words can take several minutes on this CPU-only host."
)
K_HELP = (
    "How many document chunks the answer is grounded on. More sources mean a longer prompt and a "
    "later first word; on this host k=5 timed out once."
)

_ASCII_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")
# every ASCII punctuation character: the ranges ! to /, : to @, [ to `, and { to ~


@dataclass
class Chat:
    """Everything the page remembers between runs.

    A plain object on purpose: cleanup code that runs while Streamlit is interrupting the script can
    still change its attributes, whereas any `st.session_state` access or `st.*` call there raises.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    # the chat so far, for display only: dicts with role, content, sources, error
    pending: str | None = None
    # the question the next run must answer
    busy: bool = False
    # True from the moment a question is accepted until its answer is finished


def as_plain_text(text: str) -> str:
    """`text` prepared so that Streamlit's markdown renderer shows it literally.

    Model output and document titles are untrusted, and `st.markdown` (which `st.write_stream`
    also uses) would otherwise act on them: `$10,000 ... $12,000` becomes maths (this corpus is full
    of prices), `![x](http://...)` makes the browser fetch an image, `:red[x]` colours text. Every
    ASCII punctuation character is backslash-escaped, so there is no syntax left to interpret, and a
    newline becomes markdown's hard line break. It works on each streamed fragment separately,
    because every character is escaped on its own.
    """
    return _ASCII_PUNCTUATION.sub(r"\\\1", text).replace("\n", "  \n")


def get_chat() -> Chat:
    if "chat" not in st.session_state:
        st.session_state.chat = Chat()
        # created once per browser session; later runs get the same object back
    return st.session_state.chat


def render_sidebar(chat: Chat, api_url: str) -> None:
    with st.sidebar:
        st.header("Settings")
        health = client.check_health(api_url)
        # asks /health on every run: it answers in milliseconds, even while an answer is streaming
        if health.ok:
            st.success(f"API reachable at {api_url}")
        else:
            st.error(
                f"Cannot reach the API at {api_url} ({health.detail}). "
                "Start it with: uvicorn src.api.main:app"
            )
        st.slider(
            "Sources to retrieve (k)",
            K_MIN,
            K_MAX,
            K_DEFAULT,
            key="k",
            disabled=chat.busy,
            help=K_HELP,
        )
        # the slider's value lives in st.session_state.k; locked while an answer is being fetched
        if st.button("New chat", disabled=chat.busy):
            chat.messages = []
            # forgets the displayed history; the API never had any


def render_sources(sources: list[client.Source]) -> None:
    if not sources:
        return
    with st.expander(f"Sources ({len(sources)})"):
        st.markdown("\n".join(f"- {as_plain_text(source.label)}" for source in sources))
        # one list item per source, each shown literally


def render_message(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        if message["content"]:
            st.markdown(as_plain_text(message["content"]))
        render_sources(message["sources"])
        if message["error"]:
            st.error(as_plain_text(message["error"]))


def stream_into_page(api_url: str, question: str, k: int, reply: dict[str, Any]) -> None:
    """Asks the API and draws the answer as it arrives; records what happened in `reply`."""
    try:
        stream = client.ask(api_url, question, k)
        # sends the question now: an unreachable API or an HTTP error is raised here
    except client.ChatError as problem:
        reply["error"] = problem.message
        return
    try:
        fragments = iter(stream)
        with st.spinner(WAITING_TEXT, show_time=True):
            first = next(fragments, None)
            # blocks until the first words exist (minutes on this host), with a running clock
        if first is not None:
            st.write_stream(
                as_plain_text(fragment) for fragment in itertools.chain([first], fragments)
            )
            # types the answer into the chat bubble fragment by fragment
    except client.ChatError as problem:
        reply["error"] = problem.message
    finally:
        stream.close()
        # releases the connection NOW -- also when Streamlit interrupts this run with an exception
        reply["content"] = stream.text
        reply["sources"] = stream.sources
        # whatever arrived is kept, even after a failure or an interruption (plain data only here)
    if stream.error is not None:
        reply["error"] = client.explain_stream_error(stream.error)
        # sources, errors and the final text are drawn by render_message on the redraw that follows


def answer_pending_question(chat: Chat, api_url: str) -> None:
    question = chat.pending
    chat.pending = None
    # taken out first, so no later rerun can ask the same question again
    if question is None:
        chat.busy = False
        return
        # locked with nothing to answer (a stale state): unlock instead of asking about nothing
    k = st.session_state.k
    # read now, before any streaming: a session_state access, which is off limits in `finally`
    reply: dict[str, Any] = {"role": "assistant", "content": "", "sources": [], "error": None}
    try:
        with st.chat_message("assistant"):
            stream_into_page(api_url, question, k, reply)
    finally:
        chat.messages.append(reply)
        chat.busy = False
        # plain attribute changes only: they run even when Streamlit stops the script, whereas an
        # st.session_state access here would raise again and leave the input locked for good


def main() -> None:
    st.set_page_config(page_title=PAGE_TITLE, page_icon="\N{SPEECH BALLOON}")
    st.title("Advance RAG chat")
    try:
        api_url = endpoint.configured_api_url()
    except endpoint.ConfigError as problem:
        st.error(as_plain_text(problem.message))
        st.stop()
        # a mistyped address shows its fix and nothing else: no input to type a doomed question into
    st.caption(INDEPENDENT_NOTE)
    chat = get_chat()
    render_sidebar(chat, api_url)
    for message in chat.messages:
        render_message(message)
    question = st.chat_input(PLACEHOLDER, max_chars=MAX_QUESTION_CHARS, disabled=chat.busy)
    # locked while an answer is being fetched: a second question would abandon the first, whose
    # model work keeps running (gaps G1 and G8 in docs/api-contract.md) and block the new one
    if question and question.strip() and not chat.busy:
        chat.messages.append(
            {"role": "user", "content": question.strip(), "sources": [], "error": None}
        )
        chat.pending = question.strip()
        chat.busy = True
        st.rerun()
        # first run of two: the question is recorded and the page redraws with the input locked
    if chat.busy:
        answer_pending_question(chat, api_url)
        st.rerun()
        # second run: the answer is streamed, then one more redraw unlocks the input


if __name__ == "__main__":
    main()
