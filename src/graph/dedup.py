"""Entity deduplication: decide which extracted nodes are the same real-world entity, keep one
canonical node per entity with every other spelling as an alias, and say how to rewrite the
edges that pointed at the spellings that disappeared.

The strategy, in the order it runs:

1. Scope. A node is only compared with nodes of the same label, and a Contact only with
   Contacts of the same organisation. P4-M2 found two different Robert Chens, an Employee
   and a client's officer, so a name match across labels is never evidence.
2. Normalise each name with its label's rules: case, punctuation and honorifics for people;
   legal suffixes for companies; state abbreviations for locations. Names that normalise to
   the same words are the same entity.
3. Subset match. A shorter form ("Alice", "Ms. Lancaster", "GlobalRe Partners") joins a
   longer one only if EXACTLY ONE longer form contains all its words. If several do, it is
   ambiguous: it stays separate and is reported. This repeats until nothing changes, so
   the result does not depend on input order and running it again changes nothing.
4. Choose the canonical name: a name from the caller's known list if one matches, else the
   most complete person name, or the constructed short company name or "City, FullState".
5. Merge properties: the canonical node's value wins, gaps are filled from the others,
   lists are united, and every disagreement is reported. Other spellings become `aliases`.
6. Return an alias map, so edges written against a spelling that disappeared can be
   rewritten with `remap_edges`.

What it deliberately does not do: fuzzy similarity (Sarah Chen is one word from Alex Chen),
merging across labels, reading "Chen, Alex" as "Alex Chen", or anything across batches.
"Exactly one" means exactly one among the nodes passed in, so a mention can be merged
wrongly when its real match is absent.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from src.graph.records import GraphEdge, GraphNode, NodeRef

ALIASES = "aliases"

_HONORIFICS = frozenset({"dr", "mr", "mrs", "ms", "miss", "prof", "sir", "dame"})
_LEGAL_SUFFIXES = frozenset(
    {"inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation"}
    | {"co", "company", "plc"}
)
_STATES = dict(
    AL="Alabama", AK="Alaska", AZ="Arizona", AR="Arkansas", CA="California", CO="Colorado",
    CT="Connecticut", DE="Delaware", DC="District of Columbia", FL="Florida", GA="Georgia",
    HI="Hawaii", ID="Idaho", IL="Illinois", IN="Indiana", IA="Iowa", KS="Kansas", KY="Kentucky",
    LA="Louisiana", ME="Maine", MD="Maryland", MA="Massachusetts", MI="Michigan", MN="Minnesota",
    MS="Mississippi", MO="Missouri", MT="Montana", NE="Nebraska", NV="Nevada", NH="New Hampshire",
    NJ="New Jersey", NM="New Mexico", NY="New York", NC="North Carolina", ND="North Dakota",
    OH="Ohio", OK="Oklahoma", OR="Oregon", PA="Pennsylvania", RI="Rhode Island",
    SC="South Carolina", SD="South Dakota", TN="Tennessee", TX="Texas", UT="Utah", VT="Vermont",
    VA="Virginia", WA="Washington", WV="West Virginia", WI="Wisconsin", WY="Wyoming",
)  # fmt: skip


# --- what comes back ------------------------------------------------------------------------


@dataclass(frozen=True)
class Ambiguity:
    """A mention that fits several longer names, so it was left unmerged."""

    label: str
    mention: str
    candidates: tuple[str, ...]
    scope: str = ""


@dataclass(frozen=True)
class Conflict:
    """Two nodes for one entity disagreed on a property; `kept` is what the node now holds."""

    label: str
    name: str
    property: str
    kept: Any
    dropped: Any


def _ref_id(ref: NodeRef) -> tuple[str, tuple[tuple[str, str], ...]]:
    # NodeRef holds a read-only mapping and so cannot be a dict key; this is its hashable identity.
    return ref.label, tuple(sorted(ref.key.items()))


@dataclass(frozen=True)
class AliasMap:
    """Links each spelling that was merged away to the node that replaced it."""

    by_id: Mapping[tuple[str, tuple[tuple[str, str], ...]], NodeRef]

    def resolve(self, ref: NodeRef) -> NodeRef:
        # A reference the merge never touched resolves to itself, so callers need no special case.
        return self.by_id.get(_ref_id(ref), ref)

    def __len__(self) -> int:
        return len(self.by_id)

    def pairs(self) -> list[tuple[NodeRef, NodeRef]]:
        # Sorted, so one merge lists its pairs in one order whatever order the nodes arrived in.
        return [
            (NodeRef(label, dict(key)), canonical)
            for (label, key), canonical in sorted(self.by_id.items(), key=lambda item: item[0])
        ]


@dataclass(frozen=True)
class Resolution:
    nodes: list[GraphNode]
    alias_map: AliasMap
    ambiguous: tuple[Ambiguity, ...]
    conflicts: tuple[Conflict, ...]


# --- the rules for each kind of name ---------------------------------------------------------


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", text).casefold()
    # Apostrophes are deleted, not split on: "O'Brien" must stay one word, or the "o" would
    # be thrown away as an initial and "Brien" would match it.
    folded = re.sub(r"['’]", "", folded)
    return re.findall(r"[^\W_]+", folded)


def _person_words(name: str) -> tuple[str, ...]:
    words = _words(name)
    while words and words[0] in _HONORIFICS:
        words = words[1:]
    # An initial stays a word. Dropping it would read "J. Bishop" as "Bishop" and land it on
    # Alan Bishop, and would make "Jordan K. Bishop" and "Jordan L. Bishop" one person. Kept,
    # "Jordan Bishop" still joins "Jordan K. Bishop" through the subset rule, which refuses
    # when both a K. and an L. exist.
    return tuple(words)


def _company_words(name: str) -> tuple[str, ...]:
    words = _words(name)
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words = words[:-1]
    return tuple(words)


def _split_location(name: str) -> tuple[str, str] | None:
    city, comma, state = name.partition(",")
    state = state.strip()
    if not comma or not city.strip() or not state or "," in state:
        return None
    return city.strip(), _STATES.get(state.rstrip(".").upper(), state)


def _location_words(name: str) -> tuple[str, ...]:
    parts = _split_location(name)
    return tuple(_words(f"{parts[0]} {parts[1]}" if parts else name))


def _strip_honorifics(raw: str) -> str:
    words = raw.split()
    while len(words) > 1 and re.sub(r"\W", "", words[0]).casefold() in _HONORIFICS:
        words = words[1:]
    return " ".join(words)


def _strip_legal_suffix(raw: str) -> str:
    words = raw.split()
    while len(words) > 1 and re.sub(r"\W", "", words[-1]).casefold() in _LEGAL_SUFFIXES:
        words = words[:-1]
    return " ".join(words).rstrip(" ,.;")


def _location_display(raw: str) -> str:
    parts = _split_location(raw)
    return f"{parts[0]}, {parts[1]}" if parts else " ".join(raw.split())


@dataclass(frozen=True)
class _Rules:
    words: Callable[[str], tuple[str, ...]]
    display: Callable[[str], str]
    prefer_more_words: bool
    subset_min_words: int | None


# A single word is enough to name a person ("Alice"), but not a company: "Life" or "Partners"
# alone would join whichever longer name happened to contain it.
_PEOPLE = _Rules(_person_words, _strip_honorifics, prefer_more_words=True, subset_min_words=1)
_COMPANY = _Rules(_company_words, _strip_legal_suffix, prefer_more_words=False, subset_min_words=2)
_PLACE = _Rules(_location_words, _location_display, prefer_more_words=False, subset_min_words=None)
# Everything else is identified by a hash or a closed list (doc_id, chunk_id, product names), where
# folding case or matching parts of a value would merge different things.
_EXACT = _Rules(lambda s: (s,), lambda s: s, prefer_more_words=False, subset_min_words=None)
_RULES = {"Employee": _PEOPLE, "Contact": _PEOPLE, "Company": _COMPANY, "Location": _PLACE}


def _rules_for(label: str) -> _Rules:
    return _RULES.get(label, _EXACT)


def _looks_proper(text: str) -> bool:
    words = [w for w in text.replace(",", " ").split() if w[0].isalpha()]
    return not text.isupper() and all(w[0].isupper() for w in words)


def _pick(displays: Iterable[str], rules: _Rules) -> str:
    # Every tie is broken by the text itself, so the choice never depends on input order.
    def rank(text: str) -> tuple[int, bool, str]:
        # Completeness is counted in the words the group was matched on, not in blank-separated
        # parts: "Anna Smith-Jones" holds three words and "Anna Smith" two, yet both are two parts.
        count = len(rules.words(text))
        return (-count if rules.prefer_more_words else count), not _looks_proper(text), text

    return min(set(displays), key=rank)


# --- grouping --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Mention:
    node: GraphNode
    name: str
    words: tuple[str, ...]


def _name_of(node: GraphNode) -> str:
    if node.label in _RULES:
        return node.key["name"]
    return "\x1f".join(node.key[part] for part in sorted(node.key))


def _scope(node: GraphNode) -> tuple[str, tuple[str, ...]]:
    # Two people with one name at different organisations are different people (P4-M2).
    if node.label == "Contact":
        return node.label, _COMPANY.words(node.key["organization"])
    return node.label, ()


def _fullest(group: list[_Mention]) -> tuple[str, ...]:
    return max((m.words for m in group), key=lambda words: (len(words), words))


def _merge_groups(
    groups: list[list[_Mention]], merges: list[tuple[int, int]]
) -> list[list[_Mention]]:
    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for small, big in merges:
        parent[find(small)] = find(big)
    joined: dict[int, list[_Mention]] = {}
    for index, group in enumerate(groups):
        joined.setdefault(find(index), []).extend(group)
    return list(joined.values())


def _cluster(
    mentions: list[_Mention], rules: _Rules
) -> tuple[list[list[_Mention]], list[tuple[int, list[int]]]]:
    by_words: dict[tuple[str, ...], list[_Mention]] = {}
    for mention in mentions:
        by_words.setdefault(mention.words, []).append(mention)
    # The order of the groups never reaches the result: each decision is about one group against the
    # others as a set, and every output is sorted before it is returned.
    groups = list(by_words.values())
    while rules.subset_min_words is not None:
        forms = [_fullest(group) for group in groups]
        word_sets = [set(form) for form in forms]  # built once: the loop below compares every pair
        merges: list[tuple[int, int]] = []
        ambiguous: list[tuple[int, list[int]]] = []
        for i, form in enumerate(forms):
            if len(form) < rules.subset_min_words:
                continue
            supersets = [j for j, other in enumerate(word_sets) if j != i and word_sets[i] < other]
            # INTERVIEW: the two ways to be wrong. Merging on the first match would avoid
            # leftovers but merge the wrong Alex (a false merge, which fuses two people's
            # neighbourhoods in the graph); refusing leaves a duplicate (a false split), which is
            # only a missed connection. Exactly one longer form is the evidence required.
            if len(supersets) == 1:
                merges.append((i, supersets[0]))
            elif supersets:
                ambiguous.append((i, supersets))
        if not merges:
            return groups, ambiguous
        # INTERVIEW: why a fixpoint. Decisions are made against the groups as they stood at the
        # start of the round and the rounds repeat, so "Alice", "Alice Johnson" and "Alice Johnson
        # Smith" end up the same whichever order they arrived in (order-independent), and a second
        # run finds nothing left to merge (idempotent). One pass would leave "Alice" ambiguous.
        groups = _merge_groups(groups, merges)
    return groups, []


# --- one merged entity -----------------------------------------------------------------------


def _canonical(group: list[_Mention], rules: _Rules, known: Sequence[str]) -> str:
    # A name from an authoritative list (the HR roster) is never renamed by a heuristic.
    seen = {m.words for m in group}
    for name in sorted(known):
        if rules.words(name) in seen:
            return name
    # INTERVIEW: a company or place name is CONSTRUCTED ("Roadway Insurance Inc." becomes
    # "Roadway Insurance") rather than chosen among the spellings present, so two batches that
    # saw a suffix, case or state-abbreviation variant still write the same key. A person's full
    # name cannot be constructed, so it depends on which spellings the batch contained.
    return _pick((rules.display(m.name) for m in group), rules)


def _canonical_org(mentions: list[_Mention], known: Sequence[str]) -> str:
    organisations = {m.node.key["organization"] for m in mentions}
    seen = {_COMPANY.words(org) for org in organisations}
    for name in sorted(known):
        if _COMPANY.words(name) in seen:
            return name
    return _pick((_COMPANY.display(org) for org in organisations), _COMPANY)


def _existing_aliases(node: GraphNode) -> list[str]:
    value = node.properties.get(ALIASES, [])
    if not isinstance(value, list) or not all(isinstance(alias, str) for alias in value):
        raise ValueError(f"{node.label} {_name_of(node)!r}: {ALIASES!r} must be a list of strings")
    return cast(list[str], value)


def _merge_properties(
    group: list[_Mention], canonical: str, label: str
) -> tuple[dict[str, Any], list[Conflict]]:
    # INTERVIEW: survivorship, the rule for which value a merged node keeps. The canonical
    # spelling's own node goes first so its values win, and the rest follow in a fixed order so a
    # conflict is always settled the same way, whatever order the extractions arrived in.
    ordered = sorted(
        group,
        key=lambda m: (m.name != canonical, m.name, repr(sorted(m.node.properties.items()))),
    )
    merged: dict[str, Any] = {}
    conflicts: list[Conflict] = []
    for mention in ordered:
        for name, value in mention.node.properties.items():
            if name == ALIASES:
                continue
            if name not in merged or merged[name] == value:
                merged.setdefault(name, value)
                continue
            united = (
                merged[name] + [v for v in value if v not in merged[name]]
                if (isinstance(merged[name], list) and isinstance(value, list))
                else None
            )
            if united is not None and len({type(v) for v in united}) <= 1:
                merged[name] = united
            else:
                conflicts.append(Conflict(label, canonical, name, kept=merged[name], dropped=value))
    return merged, conflicts


def _aliases(group: list[_Mention], canonical: str) -> list[str]:
    names = {m.name for m in group}
    for mention in group:
        names.update(_existing_aliases(mention.node))
    names.discard(canonical)
    return sorted(names)


# --- the public functions --------------------------------------------------------------------


def resolve_entities(
    nodes: Sequence[GraphNode], *, known_names: Mapping[str, Iterable[str]] | None = None
) -> Resolution:
    """Merge nodes that are the same entity, and report what was merged, refused or in conflict.

    `known_names` maps a label to names that are authoritative and pinned as canonical when
    they match (for example the HR roster under "Employee").
    """
    known = {label: tuple(names) for label, names in (known_names or {}).items()}
    scopes: dict[tuple[str, tuple[str, ...]], list[_Mention]] = {}
    for node in nodes:
        rules, name = _rules_for(node.label), _name_of(node)
        # A name with no letters or digits would normalise to nothing and match every other one.
        words = rules.words(name) or (name.casefold(),)
        scopes.setdefault(_scope(node), []).append(_Mention(node, name, words))

    merged_nodes: list[GraphNode] = []
    alias_ids: dict[tuple[str, tuple[tuple[str, str], ...]], NodeRef] = {}
    ambiguities: list[Ambiguity] = []
    conflicts: list[Conflict] = []
    for scope in sorted(scopes):
        label, _ = scope
        rules = _rules_for(label)
        groups, unresolved = _cluster(scopes[scope], rules)
        organisation = (
            _canonical_org(scopes[scope], known.get("Company", ())) if label == "Contact" else ""
        )
        names = [_canonical(group, rules, known.get(label, ())) for group in groups]
        for group, name in zip(groups, names, strict=True):
            properties, found = _merge_properties(group, name, label)
            conflicts.extend(found)
            aliases = _aliases(group, name)
            if aliases:
                properties[ALIASES] = aliases
            if label in _RULES:
                key = {
                    "name": name,
                    **({"organization": organisation} if label == "Contact" else {}),
                }
            else:
                key = dict(group[0].node.key)
            node = GraphNode(label, key, properties)
            merged_nodes.append(node)
            for mention in group:
                if dict(mention.node.key) != key:
                    alias_ids[_ref_id(mention.node.ref)] = node.ref
        for index, supersets in unresolved:
            ambiguities.append(
                Ambiguity(
                    label, names[index], tuple(sorted(names[j] for j in supersets)), organisation
                )
            )

    merged_nodes.sort(key=lambda n: (n.label, tuple(sorted(n.key.items()))))
    return Resolution(
        nodes=merged_nodes,
        alias_map=AliasMap(alias_ids),
        ambiguous=tuple(sorted(ambiguities, key=lambda a: (a.label, a.scope, a.mention))),
        conflicts=tuple(
            sorted(conflicts, key=lambda c: (c.label, c.name, c.property, repr(c.dropped)))
        ),
    )


def deduplicate_entities(
    nodes: Sequence[GraphNode], *, known_names: Mapping[str, Iterable[str]] | None = None
) -> list[GraphNode]:
    """The merged nodes, each with the other spellings of its entity under `aliases`."""
    return resolve_entities(nodes, known_names=known_names).nodes


def remap_edges(edges: Sequence[GraphEdge], alias_map: AliasMap) -> list[GraphEdge]:
    """Point edges at the canonical nodes, collapsing edges that became identical."""
    remapped: dict[tuple[str, Any, Any], GraphEdge] = {}
    for edge in edges:
        source, target = alias_map.resolve(edge.source), alias_map.resolve(edge.target)
        identity = (edge.type, _ref_id(source), _ref_id(target))
        earlier = remapped.get(identity)
        if earlier is None:
            remapped[identity] = GraphEdge(edge.type, source, target, dict(edge.properties))
            continue
        # The store would keep only one such edge anyway; the first one's values win and the
        # later one only fills what it lacks, as when nodes are merged.
        extra = {k: v for k, v in edge.properties.items() if k not in earlier.properties}
        if extra:
            remapped[identity] = GraphEdge(
                edge.type, source, target, {**earlier.properties, **extra}
            )
    return list(remapped.values())
