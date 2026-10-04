# The RIGHT design for C3: the cleanup changes a plain Python object. Driven by d3_interruption.py.
import builtins  # lets this script leave a note that the driver can read after the run
from dataclasses import dataclass, field  # a tiny way to declare a plain data object

import streamlit as st  # the Streamlit library

NOTES = builtins.__dict__.setdefault("D3_NOTES", [])  # a shared list for messages to the driver


@dataclass  # generates __init__ for us; the class is ordinary Python, nothing Streamlit-specific
class Chat:
    busy: bool = True  # pretend a question is in flight: the page is "locked"
    saved: list = field(default_factory=list)  # where the partial answer is kept


if "chat" not in st.session_state:  # first run of this browser tab only
    st.session_state.chat = Chat()  # store ONE Chat object; later runs get the same object back
chat = st.session_state.chat  # grab a direct reference to the plain object, before anything can interrupt


def fragments():  # stands in for the answer stream
    yield "first words"  # one fragment arrives normally
    st.stop()  # the user presses Streamlit's Stop button while the answer is still being written


try:  # the same shape as the real page
    st.write_stream(fragments())  # draws the fragments; the stop interrupts it half way
finally:  # runs even though the script is being stopped
    chat.busy = False  # plain attribute assignment: no st call, so nothing can interrupt it
    chat.saved.append("first words")  # keep the partial answer, again as plain data
    NOTES.append("cleanup on a plain object: worked")  # tell the driver it worked
