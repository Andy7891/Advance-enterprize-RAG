# C7: choosing the canonical name and merging what each spelling knew. No database needed.
from src.graph.dedup import resolve_entities                                 # the P4-M4 function
from src.graph.records import GraphNode                                      # the record type it works on


def emp(name, **props):                                                      # an Employee node with optional properties
    return GraphNode("Employee", {"name": name}, props)                      # label + key + what this extraction knew


r = resolve_entities([emp("Alice Johnson", job_title="CEO"), emp("Alice", job_title="Chief", city="Austin")])  # two spellings, one disagreement
node = r.nodes[0]                                                            # the single merged node
print("name       :", node.key["name"])                                      # the fullest spelling won
print("properties :", dict(node.properties))                                 # CEO kept, city filled in, aliases added
print("conflict   :", r.conflicts[0])                                        # the losing 'Chief' is reported, not lost silently

people = [emp("Robert"), emp("Robert Chen"), emp("Robert J. Chen")]          # three spellings of one person
print("\nno roster :", resolve_entities(people).nodes[0].key["name"])        # the fullest spelling wins
pinned = resolve_entities(people, known_names={"Employee": ["Robert Chen"]})  # tell it the authoritative spelling
print("with roster:", pinned.nodes[0].key["name"], pinned.nodes[0].properties["aliases"])  # the roster spelling wins; the rest become aliases

company = resolve_entities([GraphNode("Company", {"name": "Roadway Insurance Inc."})]).nodes[0]  # ONE spelling, with a legal suffix
print("\ncompany key:", company.key["name"], "| alias:", company.properties["aliases"])  # the suffix is removed from the key even when alone
