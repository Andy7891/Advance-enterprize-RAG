"""Scratch app: what can a `finally` do while Streamlit is stopping the script?"""

import builtins

import streamlit as st

TRACE = builtins.__dict__.setdefault("PAGE_TRACE", [])
st.session_state.setdefault("busy", True)
plain = builtins.__dict__.setdefault("PLAIN_HOLDER", {"busy": True})
# an ordinary Python object: reachable without going through st.session_state


def fragments():
    yield "first"
    st.stop()


try:
    st.write_stream(fragments())
finally:
    for label, action in (
        ("read st.session_state", lambda: st.session_state.busy),
        ("write st.session_state", lambda: st.session_state.__setitem__("busy", False)),
        ("call st.write", lambda: st.write("cleanup")),
        ("mutate a plain dict", lambda: plain.__setitem__("busy", False)),
    ):
        try:
            action()
            TRACE.append(f"{label}: worked")
        except BaseException as problem:  # noqa: BLE001 - a probe reports whatever happens
            TRACE.append(f"{label}: RAISED {type(problem).__name__}")
