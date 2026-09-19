# Testing a command-line program without starting a new process: sys.argv in, SystemExit + stdout out.
import argparse
import contextlib
import io
import sys


def main():
    parser = argparse.ArgumentParser(prog="store")
    parser.add_argument("command", choices=["health", "search"])
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()                 # reads sys.argv[1:]
    if args.command == "health":
        print("ready")
        raise SystemExit(0)
    print(f"searching top {args.k}")           # falls off the end -> exit code 0


def run_main(*argv):
    """The same pattern as tests/test_retrieval.py::run_main, plus stdout capture."""
    old_argv = sys.argv
    sys.argv = ["prog", *argv]
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main()
        code = 0
    except SystemExit as exit_:
        code = exit_.code
    finally:
        sys.argv = old_argv
    return code, out.getvalue().strip()


for argv in (["health"], ["search", "-k", "3"], ["search", "-k", "three"], ["delete"]):
    code, printed = run_main(*argv)
    print(f"argv={argv!s:<24} -> exit code {code} | printed {printed!r}")
