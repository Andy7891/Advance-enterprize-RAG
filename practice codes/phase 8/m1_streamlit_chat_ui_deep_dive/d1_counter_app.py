# The tiny Streamlit "page" that d1_rerun_model.py drives. It is NOT started with a server here:
# Streamlit's own test harness (AppTest) runs this file the same way the real server would.
import streamlit as st  # the Streamlit library; every st.something(...) call draws or reads the page

local_runs = 0  # an ordinary Python variable: it is created from scratch every time this file runs
local_runs += 1  # so after this line it is ALWAYS 1, no matter how many times the page has run
st.session_state.setdefault("runs", 0)  # session_state is Streamlit's memory for ONE browser tab; start it at 0
st.session_state.runs += 1  # this one survives between runs, so it counts how many runs there have been
pressed = st.button("Click me")  # draws a button; returns True ONLY in the run caused by clicking it
if pressed:  # only the run that the click triggered gets in here
    st.session_state.setdefault("clicks", 0)  # create the click counter the first time it is needed
    st.session_state.clicks += 1  # remember the click for all later runs
st.write(  # draws one line of text so the driver can read what this run saw
    f"local_runs={local_runs} session_runs={st.session_state.runs} "  # the two counters side by side
    f"clicks={st.session_state.get('clicks', 0)} button_is_true={pressed}"  # clicks so far, and the button's value
)
