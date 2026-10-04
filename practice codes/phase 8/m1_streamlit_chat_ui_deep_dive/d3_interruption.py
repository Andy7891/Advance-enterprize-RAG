# Demo of C3: while Streamlit is stopping a script, a `finally` block cannot use st.* or st.session_state.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d3_interruption.py"
import builtins  # to read the notes the two apps leave behind
from pathlib import Path  # to find the two app files next to this script

from streamlit.testing.v1 import AppTest  # Streamlit's harness: runs a page without a browser

here = Path(__file__).parent  # the folder holding this script and the two apps

print("A) WRONG: cleanup through st.session_state")  # section title
bad = AppTest.from_file(str(here / "d3_bad_app.py"), default_timeout=30)  # load the bad page
bad.run()  # run it: it streams, gets stopped, and tries to clean up
print("   notes          :", builtins.__dict__["D3_NOTES"])  # what the finally block experienced
print("   busy afterwards:", bad.session_state.busy, " <- still True: the page would stay locked")  # the damage

builtins.__dict__["D3_NOTES"].clear()  # empty the shared list before the second experiment

print("\nB) RIGHT: cleanup on a plain object")  # section title
good = AppTest.from_file(str(here / "d3_good_app.py"), default_timeout=30)  # load the good page
good.run()  # run it: same stream, same stop
print("   notes          :", builtins.__dict__["D3_NOTES"])  # what the finally block experienced
chat = good.session_state.chat  # the plain object the page kept in session state
print("   busy afterwards:", chat.busy, " <- unlocked")  # the cleanup took effect
print("   partial answer :", chat.saved, " <- kept")  # and the partial answer was saved
