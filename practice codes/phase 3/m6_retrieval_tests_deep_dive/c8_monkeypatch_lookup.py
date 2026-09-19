# Patch a name WHERE IT IS LOOKED UP. Three lookup styles, three different outcomes.
import sys
import types

import pytest

# A tiny "library" module, like src.embedding.embedder
lib = types.ModuleType("lib")
exec("def build_client():\n    return 'REAL client'\n", lib.__dict__)
sys.modules["lib"] = lib

# A "caller" module that uses it in three different ways, like our CLIs do
caller = types.ModuleType("caller")
exec(
    "from lib import build_client                         # 1. copied into caller at import\n"
    "def copied():\n"
    "    return build_client()\n"
    "def lazy():\n"
    "    from lib import build_client as b                # 2. looked up in lib at CALL time\n"
    "    return b()\n"
    "def default_arg(factory=build_client):               # 3. bound when `def` ran\n"
    "    return factory()\n",
    caller.__dict__,
)

fake = lambda: "FAKE client"

with pytest.MonkeyPatch.context() as mp:
    mp.setattr(lib, "build_client", fake)                  # patch the DEFINITION site
    print("patched lib.build_client:")
    print("   copied()      ->", caller.copied())
    print("   lazy()        ->", caller.lazy())
    print("   default_arg() ->", caller.default_arg())

with pytest.MonkeyPatch.context() as mp:
    mp.setattr(caller, "build_client", fake)               # patch the LOOKUP site
    print("patched caller.build_client:")
    print("   copied()      ->", caller.copied())
    print("   lazy()        ->", caller.lazy())
    print("   default_arg() ->", caller.default_arg())

print("after the with-blocks (monkeypatch undone):", caller.copied(), "|", caller.lazy())
