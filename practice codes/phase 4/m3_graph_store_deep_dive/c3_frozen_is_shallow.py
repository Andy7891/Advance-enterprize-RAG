# C3: frozen=True is shallow, and how the record closes the gap. No database needed.
from dataclasses import FrozenInstanceError, dataclass                       # the error frozen raises, and the decorator

from src.graph.records import GraphNode                                      # the project's record


@dataclass(frozen=True)                                                      # a plain frozen dataclass, as many people write it
class Naive:                                                                 # holds one dict
    tags: dict                                                               # the dict inside is still an ordinary, editable dict


naive = Naive({"a": 1})                                                      # build one
try:                                                                         # re-assigning the field is blocked...
    naive.tags = {}                                                          # ...so this must fail
except FrozenInstanceError:                                                  # the error frozen=True raises
    print("re-assigning the field: blocked")                                 # the part frozen=True does
naive.tags["a"] = 99                                                         # ...but editing the dict inside works
print("editing the dict inside:", naive.tags)                                # the "immutable" object changed

mine = {"line": "auto"}                                                      # the caller's own dict
node = GraphNode("Product", {"name": "Carllm"}, mine)                        # the project's record copies it into a read-only view
mine["line"] = "changed later"                                               # the caller edits their dict after construction
print("caller edit after building:", dict(node.properties))                  # the record is unaffected
try:                                                                         # writing through the record itself...
    node.properties["line"] = "home"                                         # ...is refused
except TypeError:                                                            # a read-only mapping has no item assignment
    print("editing through the record: blocked")                            # the gap the copy closes
