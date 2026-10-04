# C4: scope -- what is never compared. No database needed.
from src.graph.dedup import resolve_entities                                 # the P4-M4 function
from src.graph.records import GraphNode                                      # the record type it works on

nodes = [                                                                    # four "Robert Chen"s, all from the real corpus's situation
    GraphNode("Employee", {"name": "Robert Chen"}),                          # Insurellm's engineer
    GraphNode("Contact", {"name": "Robert Chen", "organization": "Fortress Business Underwriters"}),        # a client's officer
    GraphNode("Contact", {"name": "Robert Chen", "organization": "Fortress Business Underwriters, Inc."}),  # the same officer, organisation written with a suffix
    GraphNode("Contact", {"name": "Robert Chen", "organization": "Insurellm"}),                            # a different organisation
]
for node in resolve_entities(nodes).nodes:                                   # the merged result, in a fixed order
    print(f"{node.label:<9}", dict(node.key))                                # label first: it decides who may be compared with whom
