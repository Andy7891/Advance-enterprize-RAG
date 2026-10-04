"""Scratch: run stop_pattern_app.py under AppTest and print what its finally block could do."""

import builtins
from pathlib import Path

from streamlit.testing.v1 import AppTest

at = AppTest.from_file(str(Path(__file__).with_name("stop_pattern_app.py")), default_timeout=20)
at.run()
for line in builtins.__dict__.get("PAGE_TRACE", []):
    print(" ", line)
print("  plain dict afterwards:", builtins.__dict__.get("PLAIN_HOLDER"))
