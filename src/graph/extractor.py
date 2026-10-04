"""Rule-based extraction: one chunk of text in, graph nodes, edges and mentions out.

The corpus is templated (P4-M2 audit: 32 contracts, 32 HR records, 8 product pages, 4 company
pages), so the facts the graph model has room for sit in a few fixed shapes: a contract title,
a Parties list, a signature block, an HR summary, one sentence about offices. Rules read those
shapes exactly, run in seconds, give the same answer every time and can be checked against the
audit's counts. What they cannot do is read prose they were not written for, so every rule is
tied to a wording that was observed, and the tests compare what was extracted with what the
audit counted.

Nothing here talks to Neo4j and nothing here merges spellings: several chunks emit the same
node, and P4-M4's `resolve_entities` turns them into one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from src.graph.records import GraphEdge, GraphNode, NodeRef
from src.ingestion.schemas import DocumentChunk

INSURELLM = "Insurellm"

# INTERVIEW: chunk boundaries cut facts. P2's chunker split one signature block in the middle of
# "**Dr. Karen Phillips**", so a chunk alone cannot see it. Each chunk therefore gets a short
# glimpse of the next one, and owns only what STARTS inside its own text.
LOOKAHEAD_CHARS = 400

_PLACEHOLDER = re.compile(r"[\[\]_]")
_PARTIES = re.compile(r"\*\*Parties:?\*\*:?[ \t]*\n(?P<body>(?:[ \t]*-[^\n]*(?:\n|$))+)")
# The chunker joins the two bullets of a Parties list onto one line ("- Insurellm, Inc. - Acme
# Corp."), so the list is split on a dash between spaces, which a hyphenated name never has.
_BULLET = re.compile(r"(?:^|\s)-\s+")
_REMOTE = re.compile(r"^Remote \(Based in (?P<place>.+)\)$")
# The offices are described twice, in different words: "offices in San Francisco (HQ), New York,
# ..." (overview) and "its San Francisco headquarters along with satellite offices in key
# markets including New York, ..." (about). Both must give the same edges and flags.
_OFFICES = re.compile(r"offices in (?:key markets including )?(?P<cities>[^.]+)\.")
_HEADQUARTERS = re.compile(r"\b(?P<city>[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*) headquarters\b")
_FOUNDED_BY = re.compile(r"founded by (?P<who>[A-Z][\w.'-]*(?: [A-Z][\w.'-]*)*)")
_CO_FOUNDED = re.compile(rf"\bco-?founded {INSURELLM}\b", re.IGNORECASE)

# The signature blocks come in three layouts. Each pattern is anchored to the line start so a
# bold phrase in the middle of a sentence cannot be taken for a name, and each allows a line
# break inside the name because the chunker can cut there.
_SIGNATURE_A = re.compile(
    r"^\*\*(?P<name>[^*]+?)\*\*[ \t]*\n\*\*Title\*\*:?[ \t]*(?P<title>[^\n]+?)[ \t]*\n"
    r"\*\*(?P<org>[^*\n]+?)\*\*",
    re.M,
)
_SIGNATURE_B = re.compile(
    r"^\*\*(?P<org>[^*\n]+?)\*\*[ \t]*\n(?P<name>[^\n,]+?), (?P<title>[^\n]+?)[ \t]*\nDate:",
    re.M,
)
_SIGNATURE_C = re.compile(
    r"^\*\*(?P<heading>[^*\n]+?)\*\*[ \t]*\n(?:[ \t]*Signature:[^\n]*\n)?"
    r"[ \t]*Name:[ \t]*(?P<name>[^\n\[]+?)[ \t]*\n[ \t]*Title:[ \t]*(?P<title>[^\n\[]+?)[ \t]*$",
    re.M,
)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _filled(text: str) -> bool:
    # An unfilled template ("[Name]", "______") is the absence of a signatory, not a signatory.
    return bool(text.strip()) and not _PLACEHOLDER.search(text)


def _field(text: str, name: str) -> str | None:
    # The HR summary writes the colon inside the bold in 29 records and outside it in 3.
    match = re.search(rf"^[ \t]*-[ \t]*\*\*{name}:?\*\*:?[ \t]*(?P<value>.+?)[ \t]*$", text, re.M)
    return match["value"] if match else None


def _place(value: str) -> tuple[str, bool]:
    """The place a location string names, and whether the person works remotely from it."""
    remote = _REMOTE.match(value.strip())
    return (remote["place"], True) if remote else (value.strip(), False)


def _identity(ref: NodeRef) -> tuple[str, tuple[tuple[str, str], ...]]:
    return ref.label, tuple(sorted(ref.key.items()))


@dataclass(frozen=True)
class Extraction:
    """What one chunk yielded. Every edge's endpoints are among its nodes, so it stands alone."""

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    # Entities whose name appears in the chunk's own text: the bridge from a graph hit back to
    # a passage (the MENTIONS edges of P4-M5). A Contract is never named in text, so never here.
    mentions: tuple[NodeRef, ...] = ()
    warnings: tuple[str, ...] = ()


class _Draft:
    """Collects nodes and edges for one chunk, so a node emitted twice is emitted once."""

    def __init__(self) -> None:
        self._nodes: dict[tuple, GraphNode] = {}
        self._edges: dict[tuple, GraphEdge] = {}
        self.warnings: list[str] = []

    def node(self, label: str, key: dict[str, str], **properties: str | bool) -> NodeRef:
        ref = NodeRef(label, key)
        earlier = self._nodes.get(_identity(ref))
        merged = {**(dict(earlier.properties) if earlier else {}), **properties}
        self._nodes[_identity(ref)] = GraphNode(label, key, merged)
        return ref

    def edge(self, type_: str, source: NodeRef, target: NodeRef, **properties: str | bool) -> None:
        edge = GraphEdge(type_, source, target, properties)
        self._edges[(type_, _identity(source), _identity(target))] = edge

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    @property
    def nodes(self) -> list[GraphNode]:
        return list(self._nodes.values())

    @property
    def edges(self) -> list[GraphEdge]:
        return list(self._edges.values())


class GraphExtractor:
    """Reads chunks with rules. Closed lists (products, staff, cities) come from the corpus itself.

    INTERVIEW: rules versus a language model. Rules are exact, repeatable and testable against a
    count, and this corpus is templated enough for them; the price is that they read only the
    wordings they were written for, so a new template yields nothing, silently, unless the
    counts are checked. A model reads prose and paraphrase; the price is speed on CPU, answers
    that differ between runs, and entities that were never in the text.
    """

    def __init__(
        self, *, products: Iterable[str], employees: Iterable[str], cities: Mapping[str, str]
    ) -> None:
        self._products = tuple(sorted(products))
        self._employees = frozenset(employees)
        self._cities = {city.casefold(): place for city, place in cities.items()}
        alternatives = "|".join(re.escape(product) for product in self._products)
        # Matching the product against the closed list, not "whatever follows for", is what keeps
        # the two titles that append a tagline ("Rellm: AI-Powered ...") from inventing a product.
        self._title = re.compile(
            rf"^Contract with (?P<client>.+?) for (?P<product>{alternatives})\b"
        )
        self._product_mention = re.compile(rf"\b(?P<product>{alternatives})\b")

    @classmethod
    def from_chunks(cls, chunks: Iterable[DocumentChunk]) -> GraphExtractor:
        # INTERVIEW: the closed lists are read from the corpus, not typed in, so adding a product
        # page or an employee file extends the extractor without a code change.
        products: set[str] = set()
        employees: set[str] = set()
        cities: dict[str, str] = {}
        for chunk in chunks:
            stem = PurePosixPath(chunk.source_path).stem
            if chunk.category == "products":
                products.add(stem)
            elif chunk.category == "employees":
                employees.add(stem)
                value = _field(chunk.text, "Location")
                if value:
                    place, _ = _place(value)
                    city = place.split(",")[0].strip()
                    # A city written with its state spelled out and as a code is one city; the
                    # longer spelling is kept and P4-M4 folds the other into it.
                    if len(place) > len(cities.get(city, "")):
                        cities[city] = place
        return cls(products=products, employees=employees, cities=cities)

    def extract(self, chunk: DocumentChunk, *, lookahead: str = "") -> Extraction:
        draft = _Draft()
        own = len(chunk.text)
        window = chunk.text + ("\n" + lookahead if lookahead else "")
        rules = {
            "contracts": self._contract,
            "employees": self._employee,
            "products": self._product,
            "company": self._company_page,
        }
        rule = rules.get(chunk.category)
        if rule is None:
            draft.warn(f"{chunk.chunk_id}: no rules for category {chunk.category!r}")
        else:
            rule(chunk, window, own, draft)
        return Extraction(
            nodes=tuple(draft.nodes),
            edges=tuple(draft.edges),
            mentions=tuple(self._mentions(draft.nodes, window, own)),
            warnings=tuple(draft.warnings),
        )

    def extract_all(self, chunks: Sequence[DocumentChunk]) -> list[Extraction]:
        """Extract every chunk, giving each the start of its successor in the same document."""
        ordered = sorted(chunks, key=lambda c: (c.doc_id, c.chunk_index))
        following = {
            earlier.chunk_id: later
            for earlier, later in zip(ordered, ordered[1:], strict=False)
            if earlier.doc_id == later.doc_id
        }
        return [
            self.extract(
                chunk,
                lookahead=following[chunk.chunk_id].text[:LOOKAHEAD_CHARS]
                if chunk.chunk_id in following
                else "",
            )
            for chunk in chunks
        ]

    # --- one rule set per category -------------------------------------------------------------

    def _product(self, chunk: DocumentChunk, window: str, own: int, draft: _Draft) -> None:
        name = PurePosixPath(chunk.source_path).stem
        if name not in self._products:
            draft.warn(f"{chunk.chunk_id}: {name!r} is not a known product")
            return
        # A product's page names the product in its own title, which is in the first chunk only.
        if chunk.chunk_index == 0:
            draft.node("Product", {"name": name})

    def _company_page(self, chunk: DocumentChunk, window: str, own: int, draft: _Draft) -> None:
        # The company pages are the only place Insurellm is named as itself, and the model has no
        # DESCRIBED_IN for a Company, so a mention is the only way back to these passages.
        if not self._starts_in(re.compile(rf"\b{INSURELLM}\b"), window, own):
            return
        insurellm = draft.node("Company", {"name": INSURELLM})
        founder = _FOUNDED_BY.search(chunk.text)
        if founder:
            if founder["who"] in self._employees:
                person = draft.node("Employee", {"name": founder["who"]})
                draft.edge("FOUNDED", person, insurellm)
            else:
                draft.warn(f"{chunk.chunk_id}: founder {founder['who']!r} is not on the roster")
        flags: dict[str, bool] = {}
        offices = _OFFICES.search(chunk.text)
        if offices:
            for item in re.split(r",\s*(?:and\s+)?|\s+and\s+", offices["cities"]):
                flags[item.replace("(HQ)", "").strip()] = "(HQ)" in item
        headquarters = _HEADQUARTERS.search(chunk.text)
        if headquarters:
            flags[headquarters["city"]] = True
        for city, is_headquarters in flags.items():
            place = self._cities.get(city.casefold())
            if place is None:
                draft.warn(f"{chunk.chunk_id}: office city {city!r} has no known state")
                continue
            # Both flags are always written: a missing flag on one chunk's edge and True on
            # another's would let whichever edge P4-M4 sees first decide the answer.
            office = draft.node("Location", {"name": place})
            draft.edge("HAS_OFFICE", insurellm, office, headquarters=is_headquarters)

    def _employee(self, chunk: DocumentChunk, window: str, own: int, draft: _Draft) -> None:
        # The record's own H1 is "HR Record", so the person is named by the file, which is also
        # the roster spelling that P4-M4 is told to prefer.
        name = PurePosixPath(chunk.source_path).stem
        if name not in self._employees:
            draft.warn(f"{chunk.chunk_id}: {name!r} is not on the roster")
            return
        job_title = _field(chunk.text, "Job Title")
        value = _field(chunk.text, "Location")
        products = sorted(
            {m["product"] for m in self._product_mention.finditer(window) if m.start() < own}
        )
        founded = _CO_FOUNDED.search(chunk.text)
        if not (job_title or value or products or founded):
            return
        person = draft.node(
            "Employee", {"name": name}, **({"job_title": job_title} if job_title else {})
        )
        if value:
            place, remote = _place(value)
            draft.edge("BASED_IN", person, draft.node("Location", {"name": place}), remote=remote)
        for product in products:
            draft.edge("WORKS_ON", person, draft.node("Product", {"name": product}))
        if founded:
            draft.edge("FOUNDED", person, draft.node("Company", {"name": INSURELLM}))

    def _contract(self, chunk: DocumentChunk, window: str, own: int, draft: _Draft) -> None:
        title = self._title.match(chunk.title)
        if title is None:
            draft.warn(f"{chunk.chunk_id}: title {chunk.title!r} names no client and known product")
            return
        client = title["client"].strip()
        contract = NodeRef("Contract", {"doc_id": chunk.doc_id})

        if chunk.chunk_index == 0:
            # The H1 that carries the client and the product is in the first chunk's text.
            draft.node("Contract", {"doc_id": chunk.doc_id}, title=chunk.title)
            draft.edge("WITH_CLIENT", contract, draft.node("Company", {"name": client}))
            draft.edge("FOR_PRODUCT", contract, draft.node("Product", {"name": title["product"]}))

        parties = _PARTIES.search(chunk.text)
        if parties:
            legal = [
                item
                for item in map(_clean, _BULLET.split(parties["body"]))
                if item and not item.startswith(INSURELLM)
            ]
            if legal:
                draft.node("Company", {"name": client}, legal_name=legal[0])

        for name, role, organisation in self._signatories(chunk, window, own, client, draft):
            person = draft.node("Contact", {"name": name, "organization": organisation})
            draft.node("Contract", {"doc_id": chunk.doc_id}, title=chunk.title)
            draft.edge("SIGNED_BY", contract, person, title=role)

    # --- helpers -------------------------------------------------------------------------------

    def _signatories(
        self, chunk: DocumentChunk, window: str, own: int, client: str, draft: _Draft
    ) -> list[tuple[str, str, str]]:
        found: list[tuple[str, str, str]] = []
        for pattern in (_SIGNATURE_A, _SIGNATURE_B, _SIGNATURE_C):
            for match in pattern.finditer(window):
                # A block that begins in the lookahead belongs to the next chunk, which will see
                # all of it; counting it here too would write the same signatory twice.
                if match.start() >= own:
                    continue
                name, role = _clean(match["name"]), _clean(match["title"])
                if not (_filled(name) and _filled(role)):
                    continue
                side = match["org"] if "org" in pattern.groupindex else match["heading"]
                organisation = self._party(_clean(side), client)
                if organisation is None:
                    draft.warn(f"{chunk.chunk_id}: {side!r} is neither Insurellm nor {client!r}")
                    organisation = _clean(side)
                found.append((name, role, organisation))
        return found

    @staticmethod
    def _party(side: str, client: str) -> str | None:
        # INTERVIEW: a contract has exactly two parties, so a block that is not Insurellm's is
        # the client's, whatever it calls itself ("GlobalRe Partners International, Ltd." signs
        # for the client titled "GlobalRe Partners"). Keying the contact on the client's own key
        # keeps one organisation one string; a side matching neither party is kept and warned.
        if INSURELLM.lower() in side.lower():
            return INSURELLM
        words, client_words = (
            set(re.findall(r"\w+", side.casefold())),
            set(re.findall(r"\w+", client.casefold())),
        )
        return client if words <= client_words or client_words <= words else None

    @staticmethod
    def _starts_in(pattern: re.Pattern[str], window: str, own: int) -> bool:
        return any(match.start() < own for match in pattern.finditer(window))

    def _mentions(self, nodes: list[GraphNode], window: str, own: int) -> list[NodeRef]:
        found: list[NodeRef] = []
        for node in nodes:
            if node.label == "Contract":
                continue
            surfaces = {node.key["name"], str(node.properties.get("legal_name", ""))}
            if node.label == "Location":
                surfaces.add(node.key["name"].split(",")[0])
            for surface in filter(None, surfaces):
                # Words joined by \s+ so a name the chunker cut across a line still matches.
                words = r"\s+".join(re.escape(word) for word in surface.split())
                if self._starts_in(
                    re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE), window, own
                ):
                    found.append(node.ref)
                    break
        return found
