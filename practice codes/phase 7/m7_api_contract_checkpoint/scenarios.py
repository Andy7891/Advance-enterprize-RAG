"""Scripted behaviours for the stub model, shared by everything that needs to agree on them.

stub_server.py PLAYS a scenario; check_api_contract.py and web/frontend_sim.html KNOW what a
correct client must receive for it. Keeping both halves in one table means the stub and the
checks cannot drift apart.

A client picks a scenario by putting its marker, e.g. "[[scenario:framing]]", anywhere in the
question it POSTs to /chat; with no marker the "plain" scenario plays.
"""

from typing import NamedTuple


class Scenario(NamedTuple):
    fragments: tuple[str, ...]
    # exactly the strings the stub model streams, in order -- what the real LLM wrapper would yield
    expected: tuple[str, ...]
    # what a spec-compliant client must reassemble from those fragments: a CR or CRLF inside a
    # fragment arrives as the one newline an SSE data field can carry (docs/api-contract.md, S6)
    delay: float = 0.0
    # seconds of real, BLOCKING sleep before each fragment, like a model generating tokens
    fails_with: str | None = None
    # an error raised after the last fragment, standing in for the model server dying mid-answer
    retrieves_nothing: bool = False
    # makes the stub retriever find no chunks at all, so the pipeline's refusal path runs


FRAMING_FRAGMENTS = (
    " A",  # a leading space, as real model tokens have
    "multi\nline",  # a newline inside one fragment
    "unicode: Réponse ✓ 你好",  # non-ASCII text
    "crlf\r\nfragment",  # a Windows line ending inside one fragment
    "bare\rcarriage",  # a lone carriage return
    "inject\revent: error",  # a lone CR followed by what looks like an SSE field
    "a\r\rdata: forged",  # two lone CRs followed by what looks like a whole new event
    "ends-with-lf\n",  # a trailing newline
    "sep: after",  # U+2028: a line break to Python's splitlines(), but not to SSE
)

FRAMING_EXPECTED = (
    " A",
    "multi\nline",
    "unicode: Réponse ✓ 你好",
    "crlf\nfragment",
    "bare\ncarriage",
    "inject\nevent: error",
    "a\n\ndata: forged",
    "ends-with-lf\n",
    "sep: after",
)

PLAIN_FRAGMENTS = ("Acme ", "was ", "founded ", "by ", "Ada.")
SLOW_FRAGMENTS = tuple(f"t{i} " for i in range(100))

SCENARIOS: dict[str, Scenario] = {
    "plain": Scenario(PLAIN_FRAGMENTS, PLAIN_FRAGMENTS, delay=0.15),
    # five fragments 0.15 s apart: slow enough to see arriving one by one, fast enough to be cheap
    "framing": Scenario(FRAMING_FRAGMENTS, FRAMING_EXPECTED),
    # every awkward line ending a model's text could contain
    "fails-midstream": Scenario(
        ("Partial ", "answer "),
        ("Partial ", "answer "),
        delay=0.1,
        fails_with="the model server went away",
    ),
    "slow": Scenario(SLOW_FRAGMENTS, SLOW_FRAGMENTS, delay=0.2),
    # 100 fragments 0.2 s apart (20 s in all): long enough for a client to walk away from
    "slow-fragments": Scenario(SLOW_FRAGMENTS[:30], SLOW_FRAGMENTS[:30], delay=1.0),
    # a full second between fragments, like a CPU-only model: if the server ran this generator
    # on the event loop, every other request would wait up to a second behind it
    "no-hits": Scenario((), (), retrieves_nothing=True),
}

DEFAULT_SCENARIO = "plain"


def marker(name: str) -> str:
    """The text a client puts in its question to pick scenario `name`."""
    return f"[[scenario:{name}]]"
