# FAILURE LAB: break the code on purpose, in memory, and see WHICH check notices. No database needed, no file is edited.
import pathlib                                                               # to read the real source file
import sys                                                                   # to register the temporary modules by name
import types                                                                 # to build a module from text
import unicodedata                                                           # to make an accent stored the other way
from src.graph.records import GraphNode                                      # the record type the functions work on

SOURCE = pathlib.Path("src/graph/dedup.py").read_text(encoding="utf-8")     # the real, untouched source


def emp(name):                                                               # an Employee node called `name`
    return GraphNode("Employee", {"name": name}, {})                         # no properties: only the names matter here


def build(old, new, tag):                                                    # a copy of the module with ONE line changed
    assert SOURCE.count(old) == 1, old                                       # the line must exist exactly once, or the experiment is void
    module = types.ModuleType(tag)                                           # an empty module...
    sys.modules[tag] = module                                                # ...registered so dataclasses can find it
    exec(compile(SOURCE.replace(old, new), tag, "exec"), module.__dict__)    # run the changed source inside it
    return module                                                            # ready to call


probes = {                                                                   # name -> the batch that should expose the mistake
    "case": [emp("Alice"), emp("alice"), emp("Alice Johnson")],              # different capitals, one person
    "accent": [emp("José"), emp(unicodedata.normalize("NFD", "José"))],      # one accent stored two ways
    "quote": [emp("Michael O'Brien"), emp("Michael Brien")],                 # two DIFFERENT people
    "crowd": [emp("Alex"), emp("Alex Chen"), emp("Alex Harper")],            # a short name that fits two people
    "chain": [emp(n) for n in ["Alice", "Alice Johnson", "Alice Johnson Smith"]],  # doubt that a later merge settles
}
mutants = {                                                                  # a plausible mistake -> (line to change, replacement)
    "no case folding": ('unicodedata.normalize("NFKC", text).casefold()', 'unicodedata.normalize("NFKC", text)'),  # forget to fold case
    "no Unicode NFKC": ('unicodedata.normalize("NFKC", text).casefold()', 'text.casefold()'),  # forget to normalise Unicode
    "apostrophe splits": ('folded = re.sub(r"[\'’]", "", folded)', 'folded = re.sub(r"[\'’]", " ", folded)'),  # split O'Brien in two
    "merge on 1st match": ("if len(supersets) == 1:", "if len(supersets) >= 1:"),  # drop the "exactly one" safety
    "one round only": ("groups = _merge_groups(groups, merges)", "return _merge_groups(groups, merges), []"),  # stop after one pass
}


def summary(module, batch):                                                  # what a module made of a batch, as a comparable string
    result = module.resolve_entities(batch)                                  # run it
    return str([(n.key["name"], n.properties.get("aliases")) for n in result.nodes]) + f" reported={len(result.ambiguous)}"  # names, aliases, doubts


import src.graph.dedup as real                                               # the real module, for the baseline answers
base = {name: summary(real, batch) for name, batch in probes.items()}        # what the correct code answers for every probe
print(f"{'mistake':<20}" + "".join(f"{p:<8}" for p in probes))               # table header: one column per probe
for label, (old, new) in mutants.items():                                    # each sabotage in turn
    broken = build(old, new, "lab_" + label.replace(" ", "_"))               # the sabotaged copy
    row = "".join(f"{'CAUGHT' if summary(broken, b) != base[p] else '-':<8}" for p, b in probes.items())  # which probes notice a difference
    print(f"{label:<20}{row}")                                               # one row per mistake
