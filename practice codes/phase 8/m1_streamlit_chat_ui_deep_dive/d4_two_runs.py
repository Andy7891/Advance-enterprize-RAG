# Demo of C4: ONE question takes THREE script runs. Runs the REAL page (src/ui/app.py) with a fake API
# and prints the page's state at the start of every run.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d4_two_runs.py"
from pathlib import Path  # to locate src/ui/app.py

import httpx  # to build a fake API with MockTransport (no server, no network)
import streamlit as st  # to wrap st.chat_input so we can watch every run
from streamlit.testing.v1 import AppTest  # runs the page without a browser

from src.ui import client  # the module the page calls; we replace two of its functions below

# What the fake API will "stream": two answer fragments, a source list, then the end marker.
BODY = (  # one long string made of four SSE events
    "data: Avery \n\n"  # event 1: first fragment of the answer
    "data: Lancaster\n\n"  # event 2: second fragment
    "data: \ndata: \ndata: Sources:\ndata: [1] About (company/about.md)\n\n"  # event 3: the source list
    "data: [DONE]\n\n"  # event 4: the normal end
).encode("utf-8")  # text -> bytes, as it would arrive over a socket


def fake_ask(base_url, query, k, **_):  # replaces client.ask: same inputs, canned answer
    handler = lambda request: httpx.Response(  # noqa: E731 - a one-line handler for MockTransport
        200, headers={"content-type": "text/event-stream"}, content=BODY  # always answer with BODY
    )
    http = httpx.Client(transport=httpx.MockTransport(handler))  # an HTTP client wired to that handler
    return real_ask(base_url, query, k, http_client=http)  # run the REAL reader over the canned bytes


real_ask = client.ask  # remember the real function before replacing it
client.ask = fake_ask  # the page now "talks" to the fake API
client.check_health = lambda base_url, **_: client.Health(True, "ok")  # the sidebar thinks the API is up

rows = []  # one row per script run
original_chat_input = st.chat_input  # remember the real input widget


def recording_chat_input(*args, **kwargs):  # called once per run, because main() draws the input once
    chat = st.session_state.chat  # the page's state object (created earlier in the same run)
    rows.append((chat.busy, chat.pending, len(chat.messages), kwargs["disabled"]))  # snapshot at this moment
    return original_chat_input(*args, **kwargs)  # then behave exactly like the real widget


st.chat_input = recording_chat_input  # install the recorder

app_file = Path(__file__).resolve().parents[3] / "src" / "ui" / "app.py"  # the real page
at = AppTest.from_file(str(app_file))  # load it
at.run()  # run 0: the page opens (nobody has asked anything yet)
rows_before_question = len(rows)  # how many rows the opening produced
at.chat_input[0].set_value("Who founded Insurellm?").run()  # the user types and presses Enter

print("run | busy  | pending                 | messages | input drawn disabled")  # table header
print("----+-------+-------------------------+----------+---------------------")  # table rule
for number, (busy, pending, count, disabled) in enumerate(rows):  # one line per run
    marker = "   <- page opened" if number < rows_before_question else ""  # label the opening run
    print(f"{number:3} | {str(busy):5} | {str(pending):23} | {count:8} | {str(disabled):5}{marker}")  # the row

chat = at.session_state.chat  # the final state
print("\nfinal messages:", [(m["role"], m["content"]) for m in chat.messages])  # question + answer
print("final sources :", [m["sources"] for m in chat.messages if m["role"] == "assistant"])  # parsed sources
print("final busy    :", chat.busy)  # unlocked again
