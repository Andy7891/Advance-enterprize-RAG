"""The P7-M7 stub API, plus two scripted answers that show off what the chat page does (P8-M1).

The stub is the REAL FastAPI app with only its model and retriever replaced (see
practice codes/phase 7/m7_api_contract_checkpoint/stub_server.py), so the chat page sees the same
routes, SSE framing and error envelopes it would see from the real stack -- instantly, and with the
model's words under our control. A question picks a scenario by containing its marker, e.g.
"[[scenario:prices]] what does it cost?"; the scenarios the P7-M7 table already has (plain, framing,
fails-midstream, no-hits, slow, slow-fragments) work here too.

Run (from the repo root; PYTHONPATH entries are separated by ";" on Windows, ":" elsewhere):

    PYTHONPATH="practice codes/phase 7/m7_api_contract_checkpoint;practice codes/phase 8/m1_streamlit_chat_ui;." \
        python -m uvicorn ui_stub:app --port 8004
"""

from scenarios import SCENARIOS, Scenario

PRICES = (
    "Bizllm costs $10,000 per month, or $12,000/month for the Professional tier. ",
    "![pixel](http://evil.example/p.png) :red[not red] **not bold** <b>not html</b>",
)

SCENARIOS["prices"] = Scenario(PRICES, PRICES, delay=0.4)
# text a markdown renderer would mangle or act on: dollar amounts (maths), an image the browser
# would fetch, a colour directive, emphasis and HTML -- the page must show all of it literally

SLOW_START = ("The ", "first ", "words ", "took ", "a ", "while ", "to ", "arrive.")

SCENARIOS["slow-start"] = Scenario(SLOW_START, SLOW_START, delay=5.0)
# five seconds before the first fragment, like a model still reading its prompt: long enough to
# watch the "waiting for the model" clock before any text appears

from stub_server import app  # noqa: E402,F401 - importing it swaps the app's lifespan for the stub
