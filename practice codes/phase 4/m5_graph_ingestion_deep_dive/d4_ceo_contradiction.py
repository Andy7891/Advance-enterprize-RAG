# Minimal demo of C5: `_ceo_note` is called on the REAL function from ingest.py, with a
# small, made-up set of nodes/edges standing in for the real corpus. It shows the note
# RECORDS the disagreement -- it never edits either fact to make them agree.
from src.graph.ingest import _ceo_note  # the exact function this project ships, imported unmodified
from src.graph.records import GraphEdge, GraphNode, NodeRef  # the same record types the real job uses

hr_record = GraphNode(  # one Employee node, as if extracted from an HR file
    "Employee",  # the label the extractor gives every roster member
    {"name": "Avery Lancaster"},  # the key: roster members are identified by name alone
    {"job_title": "Co-Founder & Chief Executive Officer (CEO)"},  # the fact this source states
)

jennifer = NodeRef("Contact", {"name": "Jennifer Rodriguez", "organization": "Insurellm"})  # a signatory
other_signatory = NodeRef("Contact", {"name": "Priya Nair", "organization": "Insurellm"})  # a DIFFERENT one

edges = [  # a small set of SIGNED_BY edges, standing in for the 46 the real corpus produces
    GraphEdge(  # first contract Jennifer signs as CEO
        "SIGNED_BY",
        NodeRef("Contract", {"doc_id": "doc-1"}),  # the contract's own identity (its doc_id)
        jennifer,  # who signed it
        {"title": "Chief Executive Officer"},  # the title as THAT contract writes it
    ),
    GraphEdge(  # second contract, same signatory, same conflicting title
        "SIGNED_BY", NodeRef("Contract", {"doc_id": "doc-2"}), jennifer, {"title": "Chief Executive Officer"}
    ),
    GraphEdge(  # a third contract, a DIFFERENT person, a DIFFERENT title -- should not be counted
        "SIGNED_BY", NodeRef("Contract", {"doc_id": "doc-3"}), other_signatory, {"title": "VP Sales"}
    ),
]

note = _ceo_note([hr_record], edges)  # call the REAL function exactly as run_ingestion does
print(note)  # print the sentence that goes into data/graph_ingestion_log.json's "notes" field

print("\nBoth facts, unedited, are still what would be WRITTEN to the graph:")
print(" ", hr_record.label, hr_record.key, "->", dict(hr_record.properties))  # the HR fact, untouched
for edge in edges:  # every SIGNED_BY edge, untouched
    print(" ", edge.type, edge.source.key, "->", edge.target.key, dict(edge.properties))
