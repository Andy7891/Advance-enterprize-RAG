# C1: audit the corpus BEFORE choosing a graph model. Read-only: touches data/ and the model, never the database.
import re                                                                # regular expressions, to pull fields out of Markdown text
from collections import Counter                                          # a dict that counts how often each value appears
from pathlib import Path                                                 # object-style file paths

from src.graph.schema import NODE_TYPES, RELATIONSHIP_TYPES             # the model this audit led to (built in P4-M2)

data = Path("data")                                                      # the corpus folder, relative to the project root
docs = {                                                                 # dictionary: relative path -> full text of that file
    p.relative_to(data).as_posix(): p.read_text(encoding="utf-8")        # key like "contracts/x.md", value = the file's text
    for p in sorted(data.rglob("*.md"))                                  # every Markdown file under data/, in a stable order
    if ".ipynb_checkpoints" not in p.parts                               # skip Jupyter's autosave copies (they double-count)
}                                                                        # end of the dictionary
print("1. documents per category      :", dict(Counter(path.split("/")[0] for path in docs)))  # count files per top-level folder

contracts = {p: t for p, t in docs.items() if p.startswith("contracts/")}   # only the contract documents
with_number = [p for p, t in contracts.items() if re.search(r"Contract (Number|ID)\b", t)]  # contracts that name a number or an ID
print(f"2. contracts with a number     : {len(with_number)} of {len(contracts)}  (so a number cannot be a contract's identity)")  # how many lack one

placeholder = re.compile(r"\[(?:Insert )?(?:Start |Effective )?Date\]|\[Name\]")  # unfilled template fields such as [Date] or [Name]
print("3. with an unfilled [field]    :", sum(1 for t in contracts.values() if placeholder.search(t)), "contracts")  # count contracts containing one

locations = Counter(                                                     # count each distinct raw location string
    m[1].strip()                                                         # the text after "Location:", trimmed
    for p, t in docs.items() if p.startswith("employees/")               # only the HR records
    if (m := re.search(r"\*\*Location:?\*\*:?\s*(.+)", t))               # the Location line, with "**Location:**" or "**Location**:"
)                                                                        # end of the counter
print(f"4. employee location strings   : {len(locations)} different, e.g.", list(locations)[:3])  # spellings, not cities

roster = {Path(p).stem for p in docs if p.startswith("employees/")}      # the 32 employee names (file names without .md)
block = re.compile(                                                      # a signature block is three bold lines:
    r"\*\*(?P<name>[^*\n]+?)\*\*[ \t]*\n"                                # the signer's name
    r"\*\*Title\*\*:?[ \t]*(?P<title>[^\n]+?)[ \t]*\n"                   # their title
    r"\*\*(?P<org>[^*\n]+?)\*\*"                                         # the organisation they sign for
)                                                                        # end of the pattern
signers = [m.groupdict() for t in contracts.values() for m in block.finditer(t)]  # every signature block in every contract
ours = [s for s in signers if s["org"].lower().startswith("insurellm")]  # the blocks signed for Insurellm
on_roster = sum(s["name"] in roster for s in ours)                       # how many of those names are real employees
print(f"5. Insurellm-side signers      : {len(ours)} blocks, {on_roster} of them on the HR roster")  # the surprise: none
ceos = Counter(s["name"] for s in ours if "Chief Executive" in s["title"])  # who signs as Insurellm's CEO, and how often
hr_title = re.search(r"\*\*Job Title:?\*\*:?\s*(.+)", docs["employees/Avery Lancaster.md"])[1]  # what Avery's HR record says
print("6. signs as Insurellm CEO      :", dict(ceos), "| HR record says:", hr_title.strip())  # the corpus contradicts itself
clients = [s for s in signers if s not in ours]                          # the blocks signed for the client companies
print("7. client signers named like an employee:", [(s["name"], s["org"]) for s in clients if s["name"] in roster])  # namesakes

print("8. the model this led to:")                                       # now show the result of P4-M2
for n in NODE_TYPES:                                                     # every node type in the registry
    print(f"   {n.layer:<10} {n.label:<9} identified by {n.key}")        # its layer, label and identity key
for r in RELATIONSHIP_TYPES:                                             # every relationship type in the registry
    print(f"   {r.name:<12} {', '.join(r.sources)} -> {', '.join(r.targets)}")  # its allowed start and end labels
