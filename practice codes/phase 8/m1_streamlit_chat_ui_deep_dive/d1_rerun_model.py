# Demo of C1: Streamlit re-runs the WHOLE file on every interaction, and only session_state remembers.
# Run from the project root:  .venv/Scripts/python "practice codes/phase 8/m1_streamlit_chat_ui_deep_dive/d1_rerun_model.py"
from pathlib import Path  # builds the path to the app file that sits next to this script

from streamlit.testing.v1 import AppTest  # Streamlit's own harness: runs a page's script without a browser

app_file = Path(__file__).with_name("d1_counter_app.py")  # the page to drive: the file next to this script
at = AppTest.from_file(str(app_file))  # load it (nothing has run yet)

at.run()  # run 1: what a browser tab does when it first opens the page
print("after opening      :", at.markdown[0].value)  # the one line of text the page drew in that run

at.run()  # run 2: a plain rerun with NO interaction (like pressing the R key in the browser)
print("after a plain rerun:", at.markdown[0].value)  # watch which counter moved

at.button[0].click().run()  # run 3: click the button; the click is delivered together with this run
print("after one click    :", at.markdown[0].value)  # button_is_true should be True in THIS run only

at.run()  # run 4: another plain rerun; the click was a one-time event, not a state
print("after another rerun:", at.markdown[0].value)  # button_is_true is False again; clicks is still 1
