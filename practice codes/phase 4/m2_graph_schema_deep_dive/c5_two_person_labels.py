# C5: why people are split into Employee and Contact. Reads the real corpus; live Neo4j on throwaway labels, self-cleaning.
import re                                                                # regular expressions, to read the Markdown
import uuid                                                              # to make labels no other data can have
from pathlib import Path                                                 # object-style file paths

from src.graph.schema import NodeType, constraint_statement             # the project's model class and DDL generator
from src.graph.setup import build_driver                                 # the project's driver factory (reads .env)

suffix = uuid.uuid4().hex[:8]                                            # one random tag for this run
person_l, emp_l, con_l = f"DemoPerson{suffix}", f"DemoEmployee{suffix}", f"DemoContact{suffix}"  # one-label experiment, then the two-label model
employee = NodeType(emp_l, ("name",), "domain", "demo")                  # like Employee: identified by name
contact = NodeType(con_l, ("name", "organization"), "domain", "demo")    # like Contact: identified by name AND organisation

data = Path("data")                                                      # the corpus folder
roster = {}                                                              # employee name -> job title, from the HR records
for p in (data / "employees").glob("*.md"):                              # every HR record
    roster[p.stem] = re.search(r"\*\*Job Title:?\*\*:?\s*(.+)", p.read_text(encoding="utf-8"))[1].strip()  # the Job Title line

block = re.compile(                                                      # a signature block is three bold lines:
    r"\*\*(?P<name>[^*\n]+?)\*\*[ \t]*\n\*\*Title\*\*:?[ \t]*(?P<title>[^\n]+?)[ \t]*\n\*\*(?P<org>[^*\n]+?)\*\*"  # name, title, organisation
)                                                                        # end of the pattern
signers = []                                                             # (name, title, organisation) for every real signer
for p in sorted((data / "contracts").glob("*.md")):                      # every contract
    text = p.read_text(encoding="utf-8")                                 # its text
    client = re.search(r"^# Contract with (.+?) for ", text, re.M)[1]    # the client's short name, from the title
    for m in block.finditer(text):                                       # each signature block in it
        if m["name"].startswith("["):                                    # skip unfilled "[Name]" placeholders
            continue                                                     # they name nobody
        org = "Insurellm" if m["org"].lower().startswith("insurellm") else client  # the organisation, as one canonical name
        signers.append((m["name"], m["title"].strip(), org))             # keep the signer

with build_driver() as driver, driver.session() as session:              # open a connection pool, then one session
    try:                                                                 # everything below is undone by the finally: block at the end, even if a line raises
        print("1. ONE label, keyed on name (the mistake):")              # first, what goes wrong with a single Person label
        session.run(f"CREATE CONSTRAINT demo_person_{suffix} IF NOT EXISTS FOR (p:{person_l}) REQUIRE p.name IS UNIQUE").consume()  # unique name
        for title, org in [("Senior Full Stack Engineer", "Insurellm"), ("Chief Underwriting Officer", "Fortress")]:  # two DIFFERENT Robert Chens
            session.run(f"MERGE (p:{person_l} {{name: 'Robert Chen'}}) SET p.title = $t, p.org = $o", t=title, o=org).consume()  # MERGE finds the first
        merged = session.run(f"MATCH (p:{person_l}) RETURN p.name AS name, p.title AS title, p.org AS org, count(*) AS people").data()  # what is stored
        print("  ", merged)                                              # ONE person: the engineer's job was overwritten

        print("2. TWO labels: Employee (name) and Contact (name + organisation):")  # now the model P4-M2 chose
        session.run(constraint_statement(employee)).consume()            # the project's DDL: employee names are unique
        session.run(constraint_statement(contact)).consume()             # the project's DDL: (name, organisation) pairs are unique
        for name, title in roster.items():                               # load the 32 employees
            session.run(f"MERGE (e:{emp_l} {{name: $n}}) SET e.job_title = $t", n=name, t=title).consume()  # keyed on name
        for name, title, org in signers:                                 # load every named signer
            session.run(f"MERGE (c:{con_l} {{name: $n, organization: $o}}) SET c.title = $t", n=name, o=org, t=title).consume()  # keyed on the pair
        counts = session.run(f"MATCH (e:{emp_l}) WITH count(e) AS employees MATCH (c:{con_l}) RETURN employees, count(c) AS contacts").single()  # sizes
        print("   nodes:", counts.data())                                # 32 employees; contacts are the distinct (name, org) pairs

        both = session.run(                                              # names that exist as BOTH an employee and a contact
            f"MATCH (e:{emp_l}), (c:{con_l}) WHERE e.name = c.name "     # same name, different labels
            "RETURN e.name AS name, e.job_title AS employee_job, c.organization AS contact_org, c.title AS contact_title ORDER BY name"  # both facts
        ).data()                                                         # rows as dicts
        for row in both:                                                 # each namesake pair
            print("   namesakes kept apart:", row)                      # two people, two nodes, both facts intact

        ceo_emp = session.run(f"MATCH (e:{emp_l}) WHERE e.job_title CONTAINS 'Chief Executive' RETURN e.name AS name").value("name")  # HR record's CEO
        ceo_con = session.run(f"MATCH (c:{con_l}) WHERE c.organization = 'Insurellm' AND c.title CONTAINS 'Chief Executive' RETURN c.name AS name").value("name")  # contracts' CEO
        print("3. CEO by HR record (Employee)  :", ceo_emp)              # Avery Lancaster
        print("   CEO by contracts (Contact)   :", ceo_con, "- both kept, neither overwrites the other")  # Jennifer Rodriguez
    finally:                                                             # always clean up, even after an error above
        for name in (f"demo_person_{suffix}", employee.constraint_name, contact.constraint_name):  # the three demo constraints
            session.run(f"DROP CONSTRAINT {name} IF EXISTS").consume()   # drop each
        removed = session.run(                                           # delete every node with a demo label
            f"MATCH (n) WHERE n:{person_l} OR n:{emp_l} OR n:{con_l} DETACH DELETE n RETURN count(n) AS c"  # DETACH removes relationships too
        ).single()["c"]                                                  # how many were deleted
        print("4. cleanup                      :", removed, "nodes deleted; constraints dropped")  # nothing left behind
