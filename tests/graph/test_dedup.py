"""Tests for entity deduplication.

Most of these are hermetic. The gold sets state, pair by pair, what MUST merge and what must
NOT, because deduplication can fail two ways (a false merge fuses two entities, a false split
leaves one in two pieces) and only a labelled set measures both. The corpus tests run the same
checks on the real data and skip when it is absent; one live test writes the result through
GraphStore.
"""

from __future__ import annotations

import itertools
import random
import re
import unicodedata
import uuid
from pathlib import Path
from typing import cast

import pytest

from src.graph.dedup import (
    ALIASES,
    Ambiguity,
    Conflict,
    deduplicate_entities,
    remap_edges,
    resolve_entities,
)
from src.graph.records import GraphEdge, GraphNode, NodeRef
from src.graph.schema import NODE_TYPES, apply_schema
from src.graph.setup import build_driver
from src.retrieval.graph_store import GraphStore
from tests.support import neo4j_down

DATA = Path(__file__).resolve().parents[2] / "data"


def emp(name: str, **properties) -> GraphNode:
    return GraphNode("Employee", {"name": name}, properties)


def company(name: str, **properties) -> GraphNode:
    return GraphNode("Company", {"name": name}, properties)


def place(name: str) -> GraphNode:
    return GraphNode("Location", {"name": name})


def contact(name: str, organization: str, **properties) -> GraphNode:
    return GraphNode("Contact", {"name": name, "organization": organization}, properties)


def key_names(nodes) -> list[str]:
    return [node.key["name"] for node in nodes]


def aliases_of(node: GraphNode) -> list[str]:
    return cast(list[str], node.properties.get(ALIASES, []))


# --- the task's example ---------------------------------------------------------------------


def test_alice_alice_and_alice_johnson_are_one_person_with_the_other_spellings_as_aliases():
    resolution = resolve_entities([emp("Alice"), emp("alice"), emp("Alice Johnson")])

    (node,) = resolution.nodes
    assert node.key["name"] == "Alice Johnson"
    assert aliases_of(node) == ["Alice", "alice"]
    assert len(resolution.alias_map) == 2
    assert deduplicate_entities([emp("Alice"), emp("alice"), emp("Alice Johnson")]) == [node]


# --- normalisation rules --------------------------------------------------------------------


def test_case_and_spacing_do_not_make_two_entities():
    nodes = deduplicate_entities([emp("alice  smith"), emp("Alice Smith"), emp("ALICE SMITH")])
    assert key_names(nodes) == ["Alice Smith"]


def test_an_underscore_separates_words_like_a_space():
    # File stems and identifiers often join a name with underscores.
    assert key_names(deduplicate_entities([emp("alice_smith"), emp("Alice Smith")])) == [
        "Alice Smith"
    ]


def test_a_name_that_is_only_an_honorific_or_a_legal_suffix_is_kept_not_emptied():
    assert key_names(deduplicate_entities([emp("Dr.")])) == ["Dr."]
    assert key_names(deduplicate_entities([company("Inc.")])) == ["Inc"]


@pytest.mark.parametrize(
    "honorific", ["Dr", "Dr.", "Mr.", "Mrs.", "Ms.", "Miss", "Prof.", "Sir", "Dame", "Prof. Dr."]
)
def test_honorifics_are_not_part_of_a_persons_name(honorific):
    (node,) = deduplicate_entities([emp(f"{honorific} Karen Phillips"), emp("Karen Phillips")])
    assert node.key["name"] == "Karen Phillips"
    assert aliases_of(node) == [f"{honorific} Karen Phillips"]


def test_a_lone_honorified_name_is_still_keyed_without_the_honorific():
    (only,) = deduplicate_entities([emp("Sir Charles Pemberton")])
    assert only.key["name"] == "Charles Pemberton" and aliases_of(only) == ["Sir Charles Pemberton"]


def test_stacked_honorifics_are_all_dropped_so_the_bare_name_is_not_a_second_candidate():
    # Stripping only one would leave "Karen Phillips" fitting two longer names, so it stays apart.
    nodes = deduplicate_entities(
        [emp("Prof. Dr. Karen Phillips"), emp("Karen Phillips"), emp("Karen Phillips Wong")]
    )
    assert key_names(nodes) == ["Karen Phillips Wong"]
    assert aliases_of(nodes[0]) == ["Karen Phillips", "Prof. Dr. Karen Phillips"]


def test_a_middle_initial_joins_the_form_without_it_and_the_fuller_form_is_kept():
    (node,) = deduplicate_entities([emp("Jordan Bishop"), emp("Jordan K. Bishop")])
    assert node.key["name"] == "Jordan K. Bishop" and aliases_of(node) == ["Jordan Bishop"]
    assert len(deduplicate_entities([emp("Jordan K Bishop"), emp("Jordan K. Bishop")])) == 1


def test_a_hyphenated_surname_counts_as_two_words_when_choosing_the_fuller_spelling():
    # Both spellings are two blank-separated parts; only counting words tells them apart.
    (node,) = deduplicate_entities([emp("Anna Smith"), emp("Anna Smith-Jones")])
    assert node.key["name"] == "Anna Smith-Jones" and aliases_of(node) == ["Anna Smith"]


def test_two_different_middle_initials_are_two_people_and_the_bare_form_is_ambiguous():
    resolution = resolve_entities(
        [emp("Jordan K. Bishop"), emp("Jordan L. Bishop"), emp("Jordan Bishop")]
    )
    assert key_names(resolution.nodes) == ["Jordan Bishop", "Jordan K. Bishop", "Jordan L. Bishop"]
    assert resolution.ambiguous == (
        Ambiguity("Employee", "Jordan Bishop", ("Jordan K. Bishop", "Jordan L. Bishop")),
    )


def test_an_initial_that_contradicts_the_only_candidate_is_not_a_match():
    # Reading "J. Bishop" as "Bishop" would land it on Alan Bishop.
    assert len(deduplicate_entities([emp("J. Bishop"), emp("Alan Bishop")])) == 2


def test_last_comma_first_is_not_recognised_and_stays_a_second_person():
    # A known limit, pinned so that adding it later is a decision: reordering words would also merge
    # the genuinely different "Taylor Jordan" and "Jordan Taylor", and a false split costs less.
    assert len(deduplicate_entities([emp("Chen, Alex"), emp("Alex Chen")])) == 2


def test_an_apostrophe_is_deleted_not_split_on():
    # Split into "o" and "brien", "Michael Brien" would look like a shortened "Michael O'Brien".
    assert key_names(deduplicate_entities([emp("Michael O'Brien"), emp("Michael OBrien")])) == [
        "Michael O'Brien"
    ]
    assert len(deduplicate_entities([emp("Michael O'Brien"), emp("Michael Brien")])) == 2


def test_a_typographic_apostrophe_is_treated_like_a_plain_one():
    assert len(deduplicate_entities([emp("Michael O’Brien"), emp("Michael O'Brien")])) == 1


def test_accents_typed_as_one_character_or_as_a_base_letter_plus_a_mark_are_one_name():
    # macOS file names are decomposed; the same name typed by a person is composed.
    composed = "José García"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    assert len(deduplicate_entities([emp(composed), emp(decomposed)])) == 1


@pytest.mark.parametrize(
    ("short", "written"),
    [
        ("Continental Commercial Group", "Continental Commercial Group, Inc."),
        ("DriveSmart Insurance", "DriveSmart Insurance Corp."),
        ("Evergreen Life Insurance", "Evergreen Life Insurance Company"),
        ("GlobalRe Partners", "GlobalRe Partners International, Ltd."),
        ("GreenValley Insurance", "GreenValley Insurance, LLC"),
        ("Metropolitan Life Group", "Metropolitan Life Group, Inc."),
        ("Insurellm", "Insurellm, Inc."),
    ],
)
def test_a_companys_short_and_legal_names_are_one_company(short, written):
    # These are real pairs from the contracts: the title says one thing, the Parties block another.
    (node,) = deduplicate_entities([company(written), company(short)])
    assert node.key["name"] == short and aliases_of(node) == [written]


@pytest.mark.parametrize(
    "written",
    [
        "Acme Inc",
        "Acme Incorporated",
        "Acme LLC",
        "Acme LLP",
        "Acme LP",
        "Acme Ltd",
        "Acme Limited",
        "Acme Corp",
        "Acme Corporation",
        "Acme Co",
        "Acme Company",
        "Acme PLC",
        "Acme Co., Ltd.",
        "The Acme",
        "The Acme, Inc.",
    ],
)
def test_every_legal_suffix_and_a_leading_the_is_dropped_from_a_one_word_company(written):
    # One word is too short for the subset rule to rescue a wrong normalisation, so these cases
    # test the suffix table itself; the two-word pairs above would merge either way.
    (node,) = deduplicate_entities([company("Acme"), company(written)])
    assert node.key["name"] == "Acme" and aliases_of(node) == [written]


def test_a_company_name_is_built_without_its_legal_suffix_even_when_it_is_the_only_spelling():
    # Batch one saw only "Roadway Insurance Inc." and batch two only "Roadway Insurance": both must
    # write the same key or the graph gets two companies.
    (node,) = deduplicate_entities([company("Roadway Insurance Inc.")])
    assert node.key["name"] == "Roadway Insurance" and aliases_of(node) == [
        "Roadway Insurance Inc."
    ]
    # The comma that introduced the suffix goes with it.
    (comma,) = deduplicate_entities([company("Metropolitan Life Group, Inc.")])
    assert comma.key["name"] == "Metropolitan Life Group"


@pytest.mark.parametrize(
    ("spellings", "canonical"),
    [
        (
            ["San Francisco, CA", "san francisco, ca", "San Francisco, California"],
            "San Francisco, California",
        ),
        (["New York, NY", "New York, New York"], "New York, New York"),
        (["Portland, OR", "Portland, Oregon"], "Portland, Oregon"),
    ],
)
def test_state_abbreviations_and_case_do_not_make_two_places(spellings, canonical):
    (node,) = deduplicate_entities([place(name) for name in spellings])
    assert node.key["name"] == canonical
    assert aliases_of(node) == sorted(set(spellings) - {canonical})


US_STATES = (
    "AL Alabama|AK Alaska|AZ Arizona|AR Arkansas|CA California|CO Colorado|CT Connecticut|"
    "DE Delaware|DC District of Columbia|FL Florida|GA Georgia|HI Hawaii|ID Idaho|IL Illinois|"
    "IN Indiana|IA Iowa|KS Kansas|KY Kentucky|LA Louisiana|ME Maine|MD Maryland|"
    "MA Massachusetts|MI Michigan|MN Minnesota|MS Mississippi|MO Missouri|MT Montana|"
    "NE Nebraska|NV Nevada|NH New Hampshire|NJ New Jersey|NM New Mexico|NY New York|"
    "NC North Carolina|ND North Dakota|OH Ohio|OK Oklahoma|OR Oregon|PA Pennsylvania|"
    "RI Rhode Island|SC South Carolina|SD South Dakota|TN Tennessee|TX Texas|UT Utah|"
    "VT Vermont|VA Virginia|WA Washington|WV West Virginia|WI Wisconsin|WY Wyoming"
)


def test_every_state_abbreviation_becomes_its_full_name():
    # Typed again here, apart from the table in the code, so that a slip in either shows up.
    pairs = [entry.split(" ", 1) for entry in US_STATES.split("|")]
    assert len(pairs) == 51  # fifty states and the District of Columbia
    wrong = []
    for abbreviation, full in pairs:
        nodes = deduplicate_entities(
            [place(f"Springfield, {abbreviation}"), place(f"Springfield, {full}")]
        )
        if key_names(nodes) != [f"Springfield, {full}"]:
            wrong.append((abbreviation, full, key_names(nodes)))
    assert wrong == []


def test_a_trailing_full_stop_after_a_state_abbreviation_is_ignored():
    (node,) = deduplicate_entities([place("Portland, OR."), place("Portland, Oregon")])
    assert node.key["name"] == "Portland, Oregon" and aliases_of(node) == ["Portland, OR."]


def test_two_states_with_a_city_of_the_same_name_stay_two_places():
    assert len(deduplicate_entities([place("Portland, OR"), place("Portland, ME")])) == 2


def test_a_city_without_a_state_is_not_merged_into_one_that_has_a_state():
    # "Portland" alone could be either; places never use the subset rule.
    assert len(deduplicate_entities([place("Portland"), place("Portland, Oregon")])) == 2


def test_text_that_is_not_city_and_state_is_compared_as_written():
    remote = "Remote (Based in Phoenix, Arizona)"
    assert key_names(deduplicate_entities([place(remote)])) == [remote]
    assert key_names(deduplicate_entities([place("Remote   (Phoenix)")])) == ["Remote (Phoenix)"]


# --- the subset rule: exactly one, or it stays apart ------------------------------------------


def test_a_first_name_joins_the_one_person_who_has_it_and_leaves_everyone_else_alone():
    nodes = deduplicate_entities([emp("Avery"), emp("Avery Lancaster"), emp("Rachel Martinez")])
    assert key_names(nodes) == ["Avery Lancaster", "Rachel Martinez"]
    assert aliases_of(nodes[0]) == ["Avery"] and aliases_of(nodes[1]) == []


def test_a_surname_with_an_honorific_joins_the_one_person_who_has_it():
    (node,) = deduplicate_entities([emp("Ms. Lancaster"), emp("Avery Lancaster")])
    assert node.key["name"] == "Avery Lancaster" and aliases_of(node) == ["Ms. Lancaster"]


def test_a_mention_that_fits_two_people_is_left_alone_and_reported():
    resolution = resolve_entities([emp("Alice"), emp("Alice Johnson"), emp("Alice Smith")])

    assert key_names(resolution.nodes) == ["Alice", "Alice Johnson", "Alice Smith"]
    assert resolution.ambiguous == (
        Ambiguity("Employee", "Alice", ("Alice Johnson", "Alice Smith")),
    )
    assert len(resolution.alias_map) == 0


@pytest.mark.parametrize(
    ("mention", "candidates"),
    [
        ("Alex", ["Alex Chen", "Alex Harper", "Alex Thomson"]),
        ("Emily", ["Emily Carter", "Emily Tran"]),
        ("Jordan", ["Jordan Blake", "Jordan K. Bishop"]),
        ("Chen", ["Alex Chen", "Robert Chen"]),
    ],
)
def test_the_real_roster_names_that_several_people_share_are_never_merged(mention, candidates):
    roster = [emp(name) for name in ("Alex Chen", "Alex Harper", "Alex Thomson", "Emily Carter")]
    roster += [emp(n) for n in ("Emily Tran", "Jordan Blake", "Jordan K. Bishop", "Robert Chen")]

    resolution = resolve_entities([*roster, emp(mention)])

    assert mention in key_names(resolution.nodes)
    assert resolution.ambiguous == (Ambiguity("Employee", mention, tuple(candidates)),)


def test_nested_names_settle_to_one_person_whatever_the_order():
    names = ["Alice", "Alice Johnson", "Alice Johnson Smith"]
    for order in ([0, 1, 2], [2, 1, 0], [1, 0, 2]):
        (node,) = deduplicate_entities([emp(names[i]) for i in order])
        assert node.key["name"] == "Alice Johnson Smith"
        assert aliases_of(node) == ["Alice", "Alice Johnson"]


def test_a_merged_group_is_matched_by_its_fullest_spelling():
    # "Maria" joins "Maria Elena Lopez", and that group must still count as a candidate for "Lopez".
    resolution = resolve_entities(
        [emp("Maria Elena Lopez"), emp("Maria"), emp("Lopez"), emp("Carlos Lopez")]
    )
    assert key_names(resolution.nodes) == ["Carlos Lopez", "Lopez", "Maria Elena Lopez"]
    assert resolution.ambiguous == (
        Ambiguity("Employee", "Lopez", ("Carlos Lopez", "Maria Elena Lopez")),
    )


def test_one_word_alone_never_joins_a_company():
    # "Roadway" or "Partners" would join whichever longer name happened to hold it.
    resolution = resolve_entities([company("Roadway"), company("Roadway Insurance")])
    assert key_names(resolution.nodes) == ["Roadway", "Roadway Insurance"]
    assert resolution.ambiguous == ()


def test_a_company_short_name_of_two_words_joins_the_one_longer_name():
    (node,) = deduplicate_entities(
        [company("GlobalRe Partners"), company("GlobalRe Partners International")]
    )
    assert node.key["name"] == "GlobalRe Partners"


# --- scope: what is never compared ------------------------------------------------------------


def test_an_employee_and_a_client_officer_with_one_name_are_two_people():
    # The two real Robert Chens found in P4-M2.
    nodes = deduplicate_entities(
        [emp("Robert Chen"), contact("Robert Chen", "Fortress Business Underwriters")]
    )
    assert sorted(node.label for node in nodes) == ["Contact", "Employee"]


def test_one_name_at_two_organisations_is_two_contacts():
    nodes = deduplicate_entities(
        [contact("Robert Chen", "Fortress"), contact("Robert Chen", "Insurellm")]
    )
    assert len(nodes) == 2


def test_one_contact_written_against_two_spellings_of_the_organisation_is_one_contact():
    (node,) = deduplicate_entities(
        [
            contact("Robert Chen", "Fortress Business Underwriters, Inc."),
            contact("Robert Chen", "Fortress Business Underwriters"),
        ]
    )
    assert dict(node.key) == {
        "name": "Robert Chen",
        "organization": "Fortress Business Underwriters",
    }
    assert ALIASES not in node.properties


def test_a_lone_contacts_organisation_is_built_without_its_legal_suffix():
    # The Company node is keyed without a suffix, so the contact's organisation has to be as well.
    (node,) = deduplicate_entities([contact("Robert Chen", "Fortress Business Underwriters, Inc.")])
    assert node.key["organization"] == "Fortress Business Underwriters"


def test_a_contacts_surname_only_joins_within_its_own_organisation():
    resolution = resolve_entities(
        [
            contact("Chen", "Fortress"),
            contact("Robert Chen", "Fortress"),
            contact("Sarah Chen", "Insurellm"),
        ]
    )
    assert len(resolution.nodes) == 2
    (merged,) = [n for n in resolution.nodes if n.key["organization"] == "Fortress"]
    assert merged.key["name"] == "Robert Chen" and aliases_of(merged) == ["Chen"]


def test_an_ambiguous_contact_is_reported_with_the_organisation_it_was_ambiguous_in():
    resolution = resolve_entities(
        [
            contact("Chen", "Fortress"),
            contact("Robert Chen", "Fortress"),
            contact("Sarah Chen", "Fortress"),
        ]
    )
    assert resolution.ambiguous == (
        Ambiguity("Contact", "Chen", ("Robert Chen", "Sarah Chen"), scope="Fortress"),
    )


def test_labels_identified_by_a_hash_or_a_closed_list_are_never_folded_or_matched_in_part():
    nodes = [
        GraphNode("Product", {"name": "Carllm"}),
        GraphNode("Product", {"name": "carllm"}),
        GraphNode("Chunk", {"chunk_id": "about.md_1"}),
        GraphNode("Chunk", {"chunk_id": "about.md_10"}),
        GraphNode("Chunk", {"chunk_id": "About.md_1"}),
    ]
    assert len(deduplicate_entities(nodes)) == 5


def test_the_same_node_extracted_twice_is_one_node_with_the_union_of_what_each_knew():
    (node,) = deduplicate_entities(
        [
            GraphNode("Product", {"name": "Carllm"}, {"line": "auto"}),
            GraphNode("Product", {"name": "Carllm"}, {"tier": "Pro"}),
        ]
    )
    assert dict(node.properties) == {"line": "auto", "tier": "Pro"}


# --- choosing the canonical name --------------------------------------------------------------


def test_a_known_name_is_never_renamed_by_a_heuristic():
    nodes = [emp("Robert"), emp("Robert Chen"), emp("Robert J. Chen")]
    # Left alone, the fuller "Robert J. Chen" would win; the roster spelling overrides that.
    assert key_names(deduplicate_entities(nodes)) == ["Robert J. Chen"]
    (node,) = deduplicate_entities(nodes, known_names={"Employee": ["Robert Chen"]})
    assert node.key["name"] == "Robert Chen"
    assert aliases_of(node) == ["Robert", "Robert J. Chen"]


def test_a_known_company_name_may_keep_its_legal_suffix():
    # The contract title as written is the Company key from P4-M2, suffix included when it has one.
    known = {"Company": ["Pinnacle Insurance Co."]}
    (node,) = deduplicate_entities([company("Pinnacle Insurance")], known_names=known)
    assert node.key["name"] == "Pinnacle Insurance Co."
    assert aliases_of(node) == ["Pinnacle Insurance"]


def test_when_two_known_names_match_one_entity_the_choice_does_not_depend_on_their_order():
    nodes = [emp("Alice"), emp("Alice Johnson")]
    names = ["Alice Johnson", "Alice"]
    assert deduplicate_entities(nodes, known_names={"Employee": names}) == deduplicate_entities(
        nodes, known_names={"Employee": names[::-1]}
    )


def test_a_known_name_that_matches_nobody_changes_nothing():
    known = {"Employee": ["Zed Zimmer"], "Location": ["Nowhere, Ohio"]}
    assert deduplicate_entities([emp("Alice")], known_names=known) == deduplicate_entities(
        [emp("Alice")]
    )


def test_known_company_names_also_pin_the_organisation_of_a_contact():
    (node,) = deduplicate_entities(
        [contact("Robert Chen", "Fortress Business Underwriters Inc")],
        known_names={"Company": ["Fortress Business Underwriters, Inc."]},
    )
    assert node.key["organization"] == "Fortress Business Underwriters, Inc."


# --- merging properties -----------------------------------------------------------------------


def test_the_canonical_nodes_values_win_and_the_others_only_fill_gaps():
    resolution = resolve_entities(
        [emp("Alice Johnson", job_title="CEO"), emp("Alice", job_title="Chief", city="Austin")]
    )

    (node,) = resolution.nodes
    assert dict(node.properties) == {"job_title": "CEO", "city": "Austin", ALIASES: ["Alice"]}
    assert resolution.conflicts == (
        Conflict("Employee", "Alice Johnson", "job_title", kept="CEO", dropped="Chief"),
    )


def test_a_conflict_is_settled_the_same_way_whatever_the_order():
    a, b = emp("Alice", team="x"), emp("Alice", team="y")
    assert deduplicate_entities([a, b]) == deduplicate_entities([b, a])


def test_lists_are_united_and_existing_aliases_are_kept():
    (node,) = deduplicate_entities(
        [emp("Alice Johnson", tags=["a", "b"], aliases=["AJ"]), emp("Alice", tags=["b", "c"])]
    )
    assert list(node.properties["tags"]) == ["a", "b", "c"]
    assert aliases_of(node) == ["AJ", "Alice"]


def test_lists_of_different_types_are_a_conflict_not_a_mixed_list():
    resolution = resolve_entities([emp("Alice", scores=[1, 2]), emp("Alice", scores=["a"])])
    assert [c.property for c in resolution.conflicts] == ["scores"]


def test_a_list_and_a_scalar_are_a_conflict_not_a_longer_list():
    resolution = resolve_entities([emp("Alice", team=["x"]), emp("Alice", team="y")])
    assert [c.property for c in resolution.conflicts] == ["team"]
    assert resolution.nodes[0].properties["team"] in (["x"], "y")  # one of them, never both


def test_an_existing_alias_equal_to_the_canonical_name_is_not_kept_and_none_leaves_no_property():
    (node,) = deduplicate_entities([emp("Alice Johnson", aliases=["Alice Johnson"])])
    assert ALIASES not in node.properties


def test_equally_good_spellings_are_settled_by_their_text_not_by_which_came_first():
    spellings = [f"Alice Johnson{'.' * n}" for n in range(8)]
    for order in (spellings, spellings[::-1], spellings[3:] + spellings[:3]):
        (node,) = deduplicate_entities([emp(name) for name in order])
        assert node.key["name"] == "Alice Johnson"


def test_aliases_come_out_sorted_whatever_order_they_were_seen_in():
    spellings = [f"{' ' * n}alice johnson{'.' * n}" for n in range(1, 8)]
    (node,) = deduplicate_entities([emp("Alice Johnson"), *[emp(s.strip()) for s in spellings]])
    assert len(aliases_of(node)) == 7 and aliases_of(node) == sorted(aliases_of(node))


def test_a_malformed_aliases_property_is_refused():
    with pytest.raises(ValueError, match="'aliases' must be a list of strings"):
        deduplicate_entities([emp("Alice", aliases=[1, 2])])
    with pytest.raises(ValueError, match="'aliases' must be a list of strings"):
        deduplicate_entities([emp("Alice", aliases="AJ")])


# --- the alias map and the edges --------------------------------------------------------------


def test_the_alias_map_sends_a_merged_away_spelling_to_its_canonical_node_and_nothing_else():
    resolution = resolve_entities([emp("Alice"), emp("Alice Johnson")])
    alice, johnson = (
        NodeRef("Employee", {"name": "Alice"}),
        NodeRef("Employee", {"name": "Alice Johnson"}),
    )
    stranger = NodeRef("Employee", {"name": "Zed"})

    assert resolution.alias_map.resolve(alice) == johnson
    assert resolution.alias_map.resolve(johnson) == johnson
    assert resolution.alias_map.resolve(stranger) is stranger
    assert resolution.alias_map.pairs() == [(alice, johnson)]


def test_edges_follow_their_nodes_and_edges_that_became_identical_collapse():
    carllm = NodeRef("Product", {"name": "Carllm"})
    alice = NodeRef("Employee", {"name": "Alice"})
    johnson = NodeRef("Employee", {"name": "Alice Johnson"})
    zed = NodeRef("Employee", {"name": "Zed"})
    resolution = resolve_entities(
        [emp("Alice"), emp("Alice Johnson"), GraphNode("Product", {"name": "Carllm"})]
    )

    remapped = remap_edges(
        [
            GraphEdge("WORKS_ON", alice, carllm),
            GraphEdge("WORKS_ON", johnson, carllm),
            GraphEdge("WORKS_ON", zed, carllm),
        ],
        resolution.alias_map,
    )

    assert remapped == [GraphEdge("WORKS_ON", johnson, carllm), GraphEdge("WORKS_ON", zed, carllm)]


def test_a_collapsed_edge_keeps_the_first_values_and_fills_what_they_lack():
    contract = NodeRef("Contract", {"doc_id": "k1"})
    resolution = resolve_entities(
        [contact("Robert", "Fortress"), contact("Robert Chen", "Fortress")]
    )
    short = NodeRef("Contact", {"name": "Robert", "organization": "Fortress"})
    full = NodeRef("Contact", {"name": "Robert Chen", "organization": "Fortress"})
    remap = resolution.alias_map

    (filled,) = remap_edges(
        [
            GraphEdge("SIGNED_BY", contract, short),
            GraphEdge("SIGNED_BY", contract, full, {"title": "CUO"}),
        ],
        remap,
    )
    (first_wins,) = remap_edges(
        [
            GraphEdge("SIGNED_BY", contract, short, {"title": "Chief Underwriting Officer"}),
            GraphEdge("SIGNED_BY", contract, full, {"title": "CUO"}),
        ],
        remap,
    )

    assert filled.target == full and dict(filled.properties) == {"title": "CUO"}
    assert dict(first_wins.properties) == {"title": "Chief Underwriting Officer"}


def test_edges_find_a_contact_whatever_order_the_parts_of_its_key_are_written_in():
    resolution = resolve_entities(
        [contact("Robert", "Fortress"), contact("Robert Chen", "Fortress")]
    )
    contract = NodeRef("Contract", {"doc_id": "k1"})
    reversed_key = NodeRef("Contact", {"organization": "Fortress", "name": "Robert"})

    (edge,) = remap_edges([GraphEdge("SIGNED_BY", contract, reversed_key)], resolution.alias_map)

    assert edge.target == NodeRef("Contact", {"name": "Robert Chen", "organization": "Fortress"})


def test_remapping_does_not_change_the_edges_it_was_given():
    resolution = resolve_entities([emp("Alice"), emp("Alice Johnson")])
    carllm = NodeRef("Product", {"name": "Carllm"})
    edge = GraphEdge("WORKS_ON", NodeRef("Employee", {"name": "Alice"}), carllm)

    remap_edges([edge], resolution.alias_map)

    assert edge.source == NodeRef("Employee", {"name": "Alice"})


# --- properties of the whole function ---------------------------------------------------------

MIXED = [
    emp("Alice", city="Austin"),
    emp("alice"),
    emp("Alice Johnson", job_title="CEO"),
    emp("Alice Johnson Smith"),
    emp("Alex"),
    emp("Alex Chen"),
    emp("Alex Harper"),
    emp("Dr. Karen Phillips"),
    emp("Karen Phillips", job_title="CFO"),
    emp("Jordan K. Bishop"),
    emp("Jordan Bishop", job_title="Engineer"),
    company("Acme Inc.", size=10),
    company("Acme", size=12),
    company("Roadway Insurance Inc."),
    company("Roadway"),
    place("San Francisco, CA"),
    place("San Francisco, California"),
    contact("Robert Chen", "Fortress Business Underwriters"),
    contact("Robert Chen", "Fortress Business Underwriters, Inc."),
    contact("Robert Chen", "Other Corp"),
    GraphNode("Product", {"name": "Carllm"}),
    GraphNode("Product", {"name": "carllm"}),
]


def snapshot(resolution):
    return (
        resolution.nodes,
        resolution.alias_map.pairs(),
        resolution.ambiguous,
        resolution.conflicts,
    )


def test_the_result_does_not_depend_on_the_order_of_the_input():
    expected = snapshot(resolve_entities(MIXED))
    for seed in range(30):
        shuffled = list(MIXED)
        random.Random(seed).shuffle(shuffled)
        assert snapshot(resolve_entities(shuffled)) == expected


def test_running_it_on_its_own_output_changes_nothing():
    once = deduplicate_entities(MIXED)
    assert deduplicate_entities(once) == once


def test_the_input_is_not_changed_and_the_output_is_in_a_fixed_order():
    before = list(MIXED)
    nodes = deduplicate_entities(MIXED)
    assert MIXED == before
    ordered = sorted(nodes, key=lambda n: (n.label, tuple(sorted(n.key.items()))))
    assert nodes == ordered


def test_nodes_come_out_sorted_by_label_then_key_not_in_the_order_they_were_grouped():
    nodes = deduplicate_entities([emp("Zed"), emp("Alice Johnson"), company("Acme")])
    assert [(n.label, n.key["name"]) for n in nodes] == [
        ("Company", "Acme"),
        ("Employee", "Alice Johnson"),
        ("Employee", "Zed"),
    ]


def test_ambiguities_and_their_candidates_are_listed_in_sorted_order():
    # Capitals sort before lower case, so this order differs from the order the names arrived in.
    resolution = resolve_entities(
        [emp("amy"), emp("amy ali"), emp("Amy Bo"), emp("Zed"), emp("Zed Ali"), emp("Zed Bo")]
    )
    assert resolution.ambiguous == (
        Ambiguity("Employee", "Zed", ("Zed Ali", "Zed Bo")),
        Ambiguity("Employee", "amy", ("Amy Bo", "amy ali")),
    )


def test_nothing_in_gives_nothing_out():
    resolution = resolve_entities([])
    assert resolution.nodes == [] and len(resolution.alias_map) == 0
    assert resolution.ambiguous == () and resolution.conflicts == ()


def test_a_name_with_no_letters_is_kept_apart_from_other_such_names():
    assert len(deduplicate_entities([emp("--"), emp("**")])) == 2


_FIRST = ["Alice", "Bob", "alice", "Dr. Alice", "Prof. Dr. Bob", "J.", "Jo"]
_LAST = ["Chen", "Diaz", "O'Brien", "OBrien", "Lee", "Chen-Lee"]
_MIDDLE = ["", "K.", "L.", "K", "Kim"]
_PEOPLE = sorted(
    {*_FIRST, *_LAST}
    | {" ".join(p for p in parts if p) for parts in itertools.product(_FIRST, _MIDDLE, _LAST)}
)
_ORGS = ["Acme", "Acme Inc.", "The Acme", "Zed Group", "Zed Group LLC", "Zed"]
_CITIES = ["Portland, OR", "Portland, Oregon", "portland, or", "Portland, ME", "Portland"]


def random_batch(rng: random.Random) -> list[GraphNode]:
    batch = []
    for _ in range(rng.randint(2, 8)):
        properties = {"n": rng.choice([1, 2, 3])} if rng.random() < 0.3 else {}
        kind = rng.choice(["Employee", "Employee", "Employee", "Company", "Location", "Contact"])
        if kind == "Employee":
            batch.append(emp(rng.choice(_PEOPLE), **properties))
        elif kind == "Company":
            batch.append(company(rng.choice(_ORGS), **properties))
        elif kind == "Location":
            batch.append(place(rng.choice(_CITIES)))
        else:
            batch.append(contact(rng.choice(_PEOPLE), rng.choice(_ORGS), **properties))
    return batch


def test_random_batches_are_order_independent_idempotent_lossless_and_never_share_a_key():
    # A seeded sample rather than a library: the same 2000 batches run every time. The hyphenated
    # surname above was found this way, in one batch of twenty thousand.
    rng = random.Random(2026)
    for _ in range(2000):
        batch = random_batch(rng)
        first = resolve_entities(batch)
        shuffled = list(batch)
        rng.shuffle(shuffled)
        assert snapshot(resolve_entities(shuffled)) == snapshot(first), batch
        assert deduplicate_entities(first.nodes) == first.nodes, batch
        by_key = {(n.label, tuple(sorted(n.key.items()))): n for n in first.nodes}
        assert len(by_key) == len(first.nodes), batch
        for node in batch:
            landed = first.alias_map.resolve(node.ref)
            merged = by_key[(landed.label, tuple(sorted(landed.key.items())))]
            # Nothing is lost: the name survives as the key or an alias, and so does every property.
            assert node.key["name"] in {merged.key["name"], *aliases_of(merged)}, node
            assert set(node.properties) <= set(merged.properties), node
        for ambiguity in first.ambiguous:
            names = {n.key["name"] for n in first.nodes if n.label == ambiguity.label}
            assert {ambiguity.mention, *ambiguity.candidates} <= names, ambiguity


# --- the gold sets: what must merge and what must not -------------------------------------------


def judge(label: str, others: list[str], must_merge, must_not_merge):
    """Deduplicate every name in the pairs plus `others` (bystanders that decide whether a short
    form is ambiguous) and return the labelled pairs it got wrong: (false splits, false merges)."""
    names = sorted({*others, *(name for pair in [*must_merge, *must_not_merge] for name in pair)})
    alias_map = resolve_entities([GraphNode(label, {"name": n}) for n in names]).alias_map

    def same(a: str, b: str) -> bool:
        return alias_map.resolve(NodeRef(label, {"name": a})) == alias_map.resolve(
            NodeRef(label, {"name": b})
        )

    false_splits = [pair for pair in must_merge if not same(*pair)]
    false_merges = [pair for pair in must_not_merge if same(*pair)]
    return false_splits, false_merges


def test_gold_people():
    must_merge = [
        ("Avery", "Avery Lancaster"),
        ("Lancaster", "Avery Lancaster"),
        ("Ms. Lancaster", "Avery Lancaster"),
        ("Rachel", "Rachel Martinez"),
        ("Jordan Bishop", "Jordan K. Bishop"),
        ("Dr. Karen Phillips", "Karen Phillips"),
    ]
    must_not_merge = [
        ("Alex", "Alex Chen"),  # three Alexes
        ("Alex Chen", "Alex Harper"),
        ("Sarah Chen", "Alex Chen"),  # one word apart: the case fuzzy matching gets wrong
        ("Jennifer Rodriguez", "Carlos Rodriguez"),
        ("Jennifer Rodriguez", "Jennifer Adams"),
        ("Emily", "Emily Carter"),  # two Emilys
        ("Michael Torres", "Michael O'Brien"),
        ("Robert Chen", "Alex Chen"),
    ]
    roster = ["Alex Harper", "Alex Thomson", "Emily Tran", "Sarah Chen", "Jennifer Adams"]
    assert judge("Employee", roster, must_merge, must_not_merge) == ([], [])


def test_gold_companies():
    must_merge = [
        ("Continental Commercial Group", "Continental Commercial Group, Inc."),
        ("DriveSmart Insurance", "DriveSmart Insurance Corp."),
        ("Evergreen Life Insurance", "Evergreen Life Insurance Company"),
        ("GlobalRe Partners", "GlobalRe Partners International, Ltd."),
        ("GreenValley Insurance", "GreenValley Insurance, LLC"),
        ("Metropolitan Life Group", "Metropolitan Life Group, Inc."),
        ("Pinnacle Insurance Co.", "Pinnacle Insurance"),
    ]
    near_names = [
        ("Greenstone Insurance", "GreenValley Insurance"),
        ("GreenField Holdings", "GreenValley Insurance"),
        ("Guardian Life Partners", "GlobalRe Partners"),
        ("Evergreen Life Insurance", "Metropolitan Life Group"),
        ("Heritage Life Assurance", "Evergreen Life Insurance"),
        ("Harmony Health Plans", "WellCare Insurance Co."),
        ("Roadway Insurance Inc.", "Pinnacle Insurance Co."),
    ]
    assert judge("Company", [], must_merge, near_names) == ([], [])


def test_gold_places():
    must_merge = [
        ("San Francisco, California", "San Francisco, CA"),
        ("San Francisco, CA", "san francisco, ca"),
        ("New York, New York", "New York, NY"),
    ]
    must_not_merge = [
        ("Portland, Oregon", "Portland, Maine"),
        ("Denver, Colorado", "Austin, Texas"),
    ]
    assert judge("Location", [], must_merge, must_not_merge) == ([], [])


# --- the same checks on the real corpus (skipped when the data is not there) -------------------

needs_data = pytest.mark.skipif(not (DATA / "contracts").is_dir(), reason="data/ is not present")
TITLE = re.compile(r"^# Contract with (.+?) for ", re.M)
PARTIES = re.compile(r"\*\*Parties:?\*\*:?\s*\n((?:[ \t]*-[ \t]*.+\n?)+)")
SIGNATURE = re.compile(
    r"\*\*(?P<name>[^*\n]+?)\*\*[ \t]*\n"
    r"\*\*Title\*\*:?[ \t]*(?P<title>[^\n]+?)[ \t]*\n"
    r"\*\*(?P<org>[^*\n]+?)\*\*"
)


@needs_data
def test_corpus_every_client_written_every_way_is_exactly_one_company():
    forms, owner = [], {}
    for path in sorted((DATA / "contracts").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = TITLE.search(text)[1].strip()
        written = {title}
        if parties := PARTIES.search(text):
            written |= {
                x.strip("- \t")
                for x in parties[1].splitlines()
                if x.strip() and "Insurellm" not in x
            }
        written |= {
            m["org"].strip().rstrip(":")
            for m in SIGNATURE.finditer(text)
            if not m["org"].lower().startswith("insurellm")
        }
        for name in written:
            forms.append(company(name))
            owner[name] = title

    resolution = resolve_entities(forms)

    clients = set(owner.values())
    assert len(clients) == 32, "the corpus changed: update the expected client count"
    assert len(resolution.nodes) == len(clients)  # fewer would be a false merge, more a false split
    for node in resolution.nodes:
        written = {node.key["name"], *aliases_of(node)}
        assert len({owner[name] for name in written if name in owner}) == 1
    assert resolution.ambiguous == ()


@needs_data
def test_corpus_the_roster_is_never_merged_and_a_name_that_only_one_person_has_finds_them():
    roster = sorted(p.stem for p in (DATA / "employees").glob("*.md"))
    words = lambda n: re.findall(r"[a-z]+", n.lower().replace("'", ""))  # noqa: E731
    mentions = sorted(
        {w.capitalize() for n in roster for w in (words(n)[0], words(n)[-1]) if len(w) > 1}
    )
    # The oracle is deliberately crude and independent of the code under test: a word belongs to
    # a person if it is one of the words of their name, and to nobody in particular if it is shared.
    owners = {m: [n for n in roster if m.lower() in words(n)] for m in mentions}

    resolution = resolve_entities([emp(n) for n in [*roster, *mentions]])

    def lands_on(name: str) -> str:
        return resolution.alias_map.resolve(NodeRef("Employee", {"name": name})).key["name"]

    assert len({lands_on(n) for n in roster}) == len(roster), "two people were merged"
    for mention, people in owners.items():
        # One owner: the mention joins that person. Several: it stays a node of its own.
        assert lands_on(mention) == (people[0] if len(people) == 1 else mention), mention
    assert len(resolution.ambiguous) == sum(1 for people in owners.values() if len(people) > 1)


@needs_data
def test_corpus_the_signers_titles_are_not_part_of_their_names():
    signers = []
    for path in sorted((DATA / "contracts").glob("*.md")):
        for m in SIGNATURE.finditer(path.read_text(encoding="utf-8")):
            if not m["name"].startswith("["):
                signers.append(contact(m["name"].strip(), m["org"].strip().rstrip(":")))

    nodes = deduplicate_entities(signers)

    renamed = {(n.key["name"], tuple(aliases_of(n))) for n in nodes if aliases_of(n)}
    assert ("Karen Phillips", ("Dr. Karen Phillips",)) in renamed
    assert ("Charles Pemberton", ("Sir Charles Pemberton",)) in renamed


# --- live: through GraphStore -----------------------------------------------------------------

TEST_PREFIX = "__test_"
_NEO4J_DOWN = neo4j_down()
live = pytest.mark.skipif(_NEO4J_DOWN is not None, reason=_NEO4J_DOWN or "")


def _sweep(driver) -> None:
    with driver.session() as session:
        for node in NODE_TYPES:
            session.run(
                f"MATCH (n:{node.label}) WHERE n.{node.key[0]} STARTS WITH $prefix DETACH DELETE n",
                prefix=TEST_PREFIX,
            ).consume()


@pytest.fixture
def live_store():
    with build_driver() as driver:
        apply_schema(driver)
        _sweep(driver)
        try:
            yield GraphStore(driver), driver, f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}_"
        finally:
            _sweep(driver)


@pytest.mark.live
@live
def test_live_a_merged_entity_is_one_node_and_edges_written_to_a_spelling_reach_it(live_store):
    store, driver, p = live_store
    carllm = GraphNode("Product", {"name": f"{p}Carllm"})
    extracted = [
        emp(f"{p}Alice"),
        emp(f"{p}alice"),
        emp(f"{p}Alice Johnson", job_title="CEO"),
        carllm,
    ]
    edges = [
        GraphEdge("WORKS_ON", NodeRef("Employee", {"name": f"{p}Alice"}), carllm.ref),
        GraphEdge("WORKS_ON", NodeRef("Employee", {"name": f"{p}Alice Johnson"}), carllm.ref),
    ]

    resolution = resolve_entities(extracted)
    for node in resolution.nodes:
        store.upsert_node(node)
    for edge in remap_edges(edges, resolution.alias_map):
        store.upsert_edge(edge)

    with driver.session() as session:
        stored = session.run(
            "MATCH (e:Employee) WHERE e.name STARTS WITH $p "
            "RETURN e.name AS name, e.aliases AS aliases",
            p=p,
        ).data()
    assert stored == [{"name": f"{p}Alice Johnson", "aliases": [f"{p}Alice", f"{p}alice"]}]
    (edge,) = store.search_related(f"{p}Alice Johnson")
    assert edge.type == "WORKS_ON" and edge.target == carllm.ref


@pytest.mark.live
@live
def test_live_a_later_upsert_replaces_the_alias_list_instead_of_adding_to_it(live_store):
    # Pins a known limit rather than a wish: SET += replaces a list property wholesale, so a
    # second batch that knows a different spelling erases the first batch's aliases.
    store, driver, p = live_store
    store.upsert_node(emp(f"{p}Alice Johnson", aliases=[f"{p}Alice"]))
    store.upsert_node(emp(f"{p}Alice Johnson", aliases=[f"{p}AJ"]))

    with driver.session() as session:
        aliases = session.run(
            "MATCH (e:Employee {name: $n}) RETURN e.aliases AS a", n=f"{p}Alice Johnson"
        ).single()["a"]
    assert aliases == [f"{p}AJ"]
