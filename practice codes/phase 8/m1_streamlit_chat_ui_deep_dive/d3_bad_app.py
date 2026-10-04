# The WRONG design for C3: the cleanup lives in st.session_state. Driven by d3_interruption.py.
import builtins  # lets this script leave a note that the driver can read after the run

import streamlit as st  # the Streamlit library

NOTES = builtins.__dict__.setdefault("D3_NOTES", [])  # a shared list for messages to the driver
st.session_state.setdefault("busy", True)  # pretend a question is in flight: the page is "locked"


def fragments():  # stands in for the answer stream
    yield "first words"  # one fragment arrives normally
    st.stop()  # the user presses Streamlit's Stop button while the answer is still being written


try:  # the same shape as the real page: stream inside try, clean up in finally
    st.write_stream(fragments())  # draws the fragments; the stop interrupts it half way
finally:  # runs even though the script is being stopped
    try:  # wrapped only so we can SEE what happens instead of crashing the demo
        st.session_state.busy = False  # the cleanup: unlock the page via session_state
        NOTES.append("cleanup through st.session_state: worked")  # reached only if the line above worked
    except BaseException as problem:  # Streamlit's stop signal is a BaseException, not an Exception
        NOTES.append(f"cleanup through st.session_state: RAISED {type(problem).__name__}")  # record what happened
