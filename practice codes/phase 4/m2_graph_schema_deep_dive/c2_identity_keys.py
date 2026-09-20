# C2: identity -- what makes two mentions "the same thing". Reads data/ only; no database needed.
import re                                                                # regular expressions
from collections import Counter                                          # counts how often each value appears
from pathlib import Path                                                 # object-style file paths

from src.ingestion.schemas import compute_doc_id                         # the project's deterministic document id

data = Path("data")                                                      # the corpus folder
path = "contracts/Contract with DriveSmart Insurance for Carllm.md"      # one contract, as a path relative to data/
print("1a. doc_id of that path        :", compute_doc_id(path))          # 16 hex characters, derived from the path only
print("1b. computed again             :", compute_doc_id(path) == compute_doc_id(path))  # same input -> same id, so a re-run finds the same node
print("1c. a different path           :", compute_doc_id("contracts/Contract with Apex Reinsurance for Rellm.md"))  # another file -> another id

contracts = {p.name: p.read_text(encoding="utf-8") for p in sorted((data / "contracts").glob("*.md"))}  # file name -> text
numbers = {name: re.search(r"Contract (?:Number|ID):?\*{0,2}:?\s*([A-Z0-9][A-Z0-9-]+)", text) for name, text in contracts.items()}  # number if present
missing = [n for n, m in numbers.items() if m is None]                   # contracts with no number at all
print(f"2.  contracts without a number : {len(missing)} of {len(contracts)}, e.g.", missing[0])  # the reason a number cannot be the key

print("3.  short name vs legal name (Company key vs property):")         # the title names the client one way, the body another
for name, text in list(contracts.items()):                               # walk the contracts
    title = re.search(r"^# Contract with (.+?) for ", text, re.M)        # the client's short name from the H1 title
    parties = re.search(r"\*\*Parties:?\*\*:?\s*\n((?:[ \t]*-[ \t]*.+\n?)+)", text)  # the bullet list under "Parties:"
    if not (title and parties):                                          # skip contracts without a Parties block
        continue                                                         # (only 12 have one)
    legal = [x.strip("- \t") for x in parties[1].splitlines() if x.strip() and "Insurellm" not in x]  # the client's line
    print(f"    title {title[1]!r:<30} legal {legal[0]!r}")              # show both spellings side by side
    if title[1] == "DriveSmart Insurance":                               # stop after a few examples
        break                                                            # (DriveSmart is the second one printed)

products = sorted(p.stem for p in (data / "products").glob("*.md"))      # the closed list of 8 product names, from file names
resolved = Counter()                                                     # how many contracts resolve to each product
for text in contracts.values():                                          # every contract
    tail = re.search(r"^# Contract with .+? for (.+?)\s*$", text, re.M)[1]  # everything after "for" in the title
    resolved[next(p for p in products if tail.startswith(p))] += 1       # match by the known name, ignoring any tagline
tagged = [re.search(r"for (.+?)\s*$", re.search(r"^# .*$", t, re.M)[0])[1] for t in contracts.values()]  # the raw title tails
print("4.  a title that adds a tagline:", [x for x in tagged if ":" in x or " - " in x][:1])  # why we do not just split the title
print("    resolved against the 8 names:", dict(resolved))               # every contract lands on exactly one product

STATES = {"CA": "California"}                                            # state abbreviations seen in the corpus (only one)


def normalise_location(raw: str) -> tuple[str, bool]:                    # turn a raw HR string into ("City, State", is_remote)
    remote = raw.startswith("Remote")                                    # "Remote (Based in Phoenix, Arizona)" is a remote employee
    inner = re.search(r"\((?:Based in )?([^)]+)\)", raw) if remote else None  # a remote employee's base city is inside brackets
    place = inner[1] if inner else raw                                   # use the bracket text for remote staff, else the whole string
    city, _, state = (part.strip() for part in place.partition(","))     # split "City, State" at the first comma
    return f"{city}, {STATES.get(state, state)}", remote                 # expand an abbreviation, keep anything else


raw = [re.search(r"\*\*Location:?\*\*:?\s*(.+)", p.read_text(encoding="utf-8"))[1].strip()  # each employee's raw location string
       for p in (data / "employees").glob("*.md")]                       # over all the HR records
clean = [normalise_location(x) for x in raw]                             # normalise every one
print(f"5.  raw location strings       : {len(set(raw))}")               # how many spellings there are
print(f"    after normalising          : {len({c for c, _ in clean})} cities, {sum(r for _, r in clean)} remote employees")  # how many places there really are
print("    example                    :", normalise_location("San Francisco, CA"), normalise_location("Remote (Based in Phoenix, Arizona)"))  # both forms
