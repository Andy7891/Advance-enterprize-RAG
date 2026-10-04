"""Tests for the rule-based graph extractor.

The hermetic tests state, wording by wording, what each rule reads and what it must leave alone.
The corpus tests are the ones that matter most for rules: they compare what was extracted with
the P4-M2 audit's counts and skip when the data is absent, because a rule that reads nothing
passes every hermetic test.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path, PurePosixPath

import pytest

from src.graph.dedup import remap_edges, resolve_entities
from src.graph.extractor import LOOKAHEAD_CHARS, Extraction, GraphExtractor
from src.ingestion.pipeline import read_chunks
from src.ingestion.schemas import DocumentChunk, compute_chunk_id, compute_doc_id

ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ["Carllm", "Homellm", "Markellm", "Rellm"]
EX = GraphExtractor(
    products=PRODUCTS,
    employees=["Avery Lancaster", "Alex Chen", "Jessica Liu"],
    cities={
        "San Francisco": "San Francisco, California",
        "New York": "New York, New York",
        "Austin": "Austin, Texas",
        "Seattle": "Seattle, Washington",
    },
)


def make(category: str, path: str, text: str, *, index: int = 0, title: str | None = None):
    return DocumentChunk(
        chunk_id=compute_chunk_id(path, index),
        doc_id=compute_doc_id(path),
        source_path=path,
        category=category,
        title=title or PurePosixPath(path).stem,
        chunk_index=index,
        text=text,
        char_count=len(text),
        approx_token_count=0,
    )


def contract(title: str, text: str, *, index: int = 0):
    return make("contracts", f"contracts/{title}.md", text, index=index)


def employee(name: str, text: str, *, index: int = 0):
    return make(
        "employees", f"employees/{name}.md", f"# HR Record\n\n# {name}\n\n{text}", index=index
    )


def company_page(name: str, text: str, *, index: int = 0):
    return make("company", f"company/{name}.md", text, index=index)


def nodes_of(extraction: Extraction, label: str):
    found = [(dict(n.key), dict(n.properties)) for n in extraction.nodes if n.label == label]
    return sorted(found, key=repr)


def edges_of(extraction: Extraction, type_: str):
    found = [
        (tuple(e.source.key.values()), tuple(e.target.key.values()), dict(e.properties))
        for e in extraction.edges
        if e.type == type_
    ]
    return sorted(found, key=repr)


def mentioned(extraction: Extraction) -> set[str]:
    return {next(iter(ref.key.values())) for ref in extraction.mentions}


SIGNED_A = (
    "**Signatures:**\n\n_____________\n**Michael Torres**\n**Title**: Chief Revenue Officer\n"
    "**Insurellm, Inc.**\n**Date**: January 15, 2025\n\n_____________\n**Diana Marquez**\n"
    "**Title**: Senior Vice President, Commercial Lines\n**Atlantic Risk Solutions**\n"
    "**Date**: January 15, 2025\n"
)


# --- contracts: the title ---------------------------------------------------------------------


def test_the_first_chunk_of_a_contract_gives_it_a_client_and_a_product():
    title = "Contract with Belvedere Insurance for Markellm"
    chunk = contract(title, f"# {title}\n\n## Terms\nText.")
    extraction = EX.extract(chunk)

    assert nodes_of(extraction, "Contract") == [({"doc_id": chunk.doc_id}, {"title": title})]
    assert nodes_of(extraction, "Company") == [({"name": "Belvedere Insurance"}, {})]
    assert nodes_of(extraction, "Product") == [({"name": "Markellm"}, {})]
    assert edges_of(extraction, "WITH_CLIENT") == [((chunk.doc_id,), ("Belvedere Insurance",), {})]
    assert edges_of(extraction, "FOR_PRODUCT") == [((chunk.doc_id,), ("Markellm",), {})]


def test_a_client_title_with_a_legal_ending_is_kept_as_written():
    extraction = EX.extract(contract("Contract with Pinnacle Insurance Co. for Homellm", "# x"))
    assert nodes_of(extraction, "Company") == [({"name": "Pinnacle Insurance Co."}, {})]


@pytest.mark.parametrize(
    "tail", [": AI-Powered Enterprise Reinsurance", " - AI-Powered Reinsurance"]
)
def test_a_product_followed_by_a_tagline_is_still_the_product(tail):
    extraction = EX.extract(contract(f"Contract with Apex Reinsurance for Rellm{tail}", "# x"))
    assert nodes_of(extraction, "Product") == [({"name": "Rellm"}, {})]


def test_a_later_chunk_does_not_repeat_the_facts_of_the_title():
    title = "Contract with Belvedere Insurance for Markellm"
    assert EX.extract(contract(title, "More terms.", index=2)) == Extraction()


def test_a_title_that_names_no_known_product_is_a_warning_and_no_guess():
    extraction = EX.extract(contract("Contract with Acme for Widgetllm", "# x"))
    assert extraction.nodes == () and len(extraction.warnings) == 1


# --- contracts: the Parties block -------------------------------------------------------------


@pytest.mark.parametrize(
    "bullets",
    [
        "- Insurellm, Inc.\n- DriveSmart Insurance Corp.\n",  # as the source file writes it
        "- Insurellm, Inc. - DriveSmart Insurance Corp.",  # as the chunker leaves it
        "- DriveSmart Insurance Corp.  \n- Insurellm, Inc.  \n",  # either order, trailing spaces
    ],
)
def test_the_parties_block_gives_the_clients_legal_name(bullets):
    title = "Contract with DriveSmart Insurance for Carllm"
    text = f"# {title}\n\n**Parties:**\n{bullets}\n## Terms\n"
    extraction = EX.extract(contract(title, text))
    assert nodes_of(extraction, "Company") == [
        ({"name": "DriveSmart Insurance"}, {"legal_name": "DriveSmart Insurance Corp."})
    ]


def test_a_contract_without_a_parties_block_gets_no_legal_name():
    title = "Contract with DriveSmart Insurance for Carllm"
    extraction = EX.extract(contract(title, f"# {title}\n\nbetween Insurellm, Inc. and DriveSmart"))
    assert nodes_of(extraction, "Company") == [({"name": "DriveSmart Insurance"}, {})]


# --- contracts: signature blocks --------------------------------------------------------------

ATLANTIC = "Contract with Atlantic Risk Solutions for Rellm"


def test_layout_a_gives_a_contact_and_the_title_as_written_in_that_contract():
    extraction = EX.extract(contract(ATLANTIC, SIGNED_A, index=3))
    assert nodes_of(extraction, "Contact") == [
        ({"name": "Diana Marquez", "organization": "Atlantic Risk Solutions"}, {}),
        ({"name": "Michael Torres", "organization": "Insurellm"}, {}),
    ]
    assert [props for _, _, props in edges_of(extraction, "SIGNED_BY")] == [
        {"title": "Senior Vice President, Commercial Lines"},
        {"title": "Chief Revenue Officer"},
    ]


def test_layout_b_puts_the_organisation_first_and_name_and_title_on_one_line():
    text = (
        "**Signatures:**\n\n**Insurellm, Inc.**\nSarah Chen, VP of Sales\nDate: April 18, 2025\n\n"
        "**Advantage Medical Coverage**\nDr. Michael Ramirez, Chief Medical Officer\nDate: x\n"
    )
    extraction = EX.extract(contract("Contract with Advantage Medical Coverage for Carllm", text))
    assert nodes_of(extraction, "Contact") == [
        ({"name": "Dr. Michael Ramirez", "organization": "Advantage Medical Coverage"}, {}),
        ({"name": "Sarah Chen", "organization": "Insurellm"}, {}),
    ]
    assert [props["title"] for _, _, props in edges_of(extraction, "SIGNED_BY")] == [
        "Chief Medical Officer",
        "VP of Sales",
    ]


def test_layout_c_names_the_side_and_the_side_decides_the_organisation():
    text = (
        "**Signatures:**\n\n**Insurellm Representative:**  \nName: John Smith  \n"
        "Title: Account Manager  \nDate: ____\n\n"
        "**TechDrive Insurance Representative:**  \nName: Sarah Johnson  \n"
        "Title: Operations Director  \nDate: ____\n"
    )
    extraction = EX.extract(contract("Contract with TechDrive Insurance for Carllm", text))
    assert nodes_of(extraction, "Contact") == [
        ({"name": "John Smith", "organization": "Insurellm"}, {}),
        ({"name": "Sarah Johnson", "organization": "TechDrive Insurance"}, {}),
    ]


def test_a_signature_organisation_is_the_clients_key_even_when_it_is_written_as_a_legal_name():
    text = (
        "**Signatures:**\n\n**Sir Charles Pemberton**\n**Title**: CEO\n"
        "**GlobalRe Partners International, Ltd.**\n"
    )
    extraction = EX.extract(contract("Contract with GlobalRe Partners for Rellm", text))
    assert nodes_of(extraction, "Contact") == [
        ({"name": "Sir Charles Pemberton", "organization": "GlobalRe Partners"}, {})
    ]


def test_a_signature_side_that_is_neither_party_is_kept_and_warned_about():
    text = "**Signatures:**\n\n**Someone Else**\n**Title**: CEO\n**Unrelated Holdings**\n"
    extraction = EX.extract(contract("Contract with TechDrive Insurance for Carllm", text))
    assert nodes_of(extraction, "Contact") == [
        ({"name": "Someone Else", "organization": "Unrelated Holdings"}, {})
    ]
    assert "neither Insurellm nor" in extraction.warnings[0]


@pytest.mark.parametrize(
    "text",
    [
        "**[Name], [Title]**  \n**Insurellm**  \nDate: ____",
        "**[Name]**\n**Title**: [Title]\n**Insurellm**\n",
        "**Insurellm**  \nSignature: ____  \nName: [Authorized Signatory]  \nTitle: [Title]  \n",
        "_________\nAuthorized Signature\nDate: ____\n",
    ],
)
def test_an_unfilled_template_is_not_a_signatory(text):
    extraction = EX.extract(contract("Contract with TechDrive Insurance for Carllm", text, index=3))
    assert extraction.nodes == () and extraction.edges == ()


def test_a_name_with_an_unfilled_title_is_not_a_signatory():
    text = "**Jane Roe**\n**Title**: [Title]\n**Insurellm**\n"
    chunk = contract("Contract with TechDrive Insurance for Carllm", text, index=3)
    assert EX.extract(chunk).nodes == ()


def test_a_bold_phrase_inside_a_sentence_is_not_a_name():
    text = "The **Basic Plan** costs $199. See **Title**: pricing\n**Insurellm** applies."
    chunk = contract("Contract with TechDrive Insurance for Carllm", text, index=3)
    assert EX.extract(chunk).nodes == ()


# --- contracts: a signature cut in half by the chunker ---------------------------------------

HEAD = (
    "**Signatures:**\n\n_____\n**Sarah Chen**\n**Title**: Vice President of Sales\n"
    "**Insurellm, Inc.**\n**Date**: x\n\n_____\n**Dr."
)
TAIL = (
    "Karen Phillips**\n**Title**: President & Chief Executive Officer\n"
    "**Harmony Health Plans**\n**Date**: x\n"
)
HARMONY = "Contract with Harmony Health Plans for Homellm"


def test_a_signature_cut_in_half_is_read_by_the_chunk_where_it_starts():
    first, second = contract(HARMONY, HEAD, index=2), contract(HARMONY, TAIL, index=3)

    with_lookahead = EX.extract(first, lookahead=second.text)
    assert {n.key["name"] for n in with_lookahead.nodes if n.label == "Contact"} == {
        "Sarah Chen",
        "Dr. Karen Phillips",
    }
    # The second chunk holds only the rest of the block, which it cannot read on its own.
    assert EX.extract(second).nodes == ()


def test_without_a_lookahead_the_cut_signature_is_lost_and_that_is_the_failure_mode():
    extraction = EX.extract(contract(HARMONY, HEAD, index=2))
    assert {n.key["name"] for n in extraction.nodes if n.label == "Contact"} == {"Sarah Chen"}


def test_a_block_that_starts_in_the_lookahead_belongs_to_the_next_chunk():
    extraction = EX.extract(contract(ATLANTIC, "Nothing here.", index=2), lookahead=SIGNED_A)
    assert extraction.nodes == ()


def test_extract_all_gives_a_chunk_the_start_of_its_successor_only_from_its_own_document():
    first, second = contract(HARMONY, HEAD, index=2), contract(HARMONY, TAIL, index=3)
    together = EX.extract_all([first, second])
    assert {n.key["name"] for n in together[0].nodes if n.label == "Contact"} == {
        "Sarah Chen",
        "Dr. Karen Phillips",
    }
    assert together[1].nodes == ()


def test_the_last_chunk_of_a_document_gets_no_lookahead_from_another_document():
    # Both are last chunks of different documents: whichever the list puts next, neither
    # may borrow the other's text, or Karen Phillips would appear in a contract she did not sign.
    lonely, other = contract(HARMONY, HEAD, index=2), contract(ATLANTIC, TAIL, index=3)
    for order in ([lonely, other], [other, lonely]):
        extraction = EX.extract_all(order)[order.index(lonely)]
        assert {n.key["name"] for n in extraction.nodes if n.label == "Contact"} == {"Sarah Chen"}


def test_the_lookahead_is_bounded():
    long_tail = TAIL + "x" * 5000
    first = contract(HARMONY, HEAD, index=2)
    second = contract(HARMONY, long_tail, index=3)
    extraction = EX.extract_all([first, second])[0]
    assert len(long_tail) > LOOKAHEAD_CHARS
    assert {n.key["name"] for n in extraction.nodes if n.label == "Contact"} == {
        "Sarah Chen",
        "Dr. Karen Phillips",
    }


def test_a_fact_longer_than_the_lookahead_is_still_cut_and_that_is_the_known_limit():
    first = contract(HARMONY, HEAD, index=2)
    second = contract(HARMONY, " " * (LOOKAHEAD_CHARS + 100) + TAIL, index=3)
    extraction = EX.extract_all([first, second])[0]
    assert {n.key["name"] for n in extraction.nodes if n.label == "Contact"} == {"Sarah Chen"}


# --- employees --------------------------------------------------------------------------------


def test_an_hr_summary_gives_a_job_title_and_a_place_with_the_colon_in_either_position():
    fields = "Backend Software Engineer", "San Francisco, California"
    inside = "## Summary\n- **Job Title:** {0}\n- **Location:** {1}\n".format(*fields)
    outside = "## Summary\n- **Job Title**: {0}\n- **Location**: {1}\n".format(*fields)
    for text in (inside, outside):
        extraction = EX.extract(employee("Alex Chen", text))
        assert nodes_of(extraction, "Employee") == [
            ({"name": "Alex Chen"}, {"job_title": "Backend Software Engineer"})
        ]
        assert edges_of(extraction, "BASED_IN") == [
            (("Alex Chen",), ("San Francisco, California",), {"remote": False})
        ]


def test_a_remote_employee_is_based_in_the_city_named_and_is_marked_remote():
    text = (
        "- **Job Title:** Frontend Developer\n"
        "- **Location:** Remote (Based in Seattle, Washington)\n"
    )
    extraction = EX.extract(employee("Jessica Liu", text))
    assert edges_of(extraction, "BASED_IN") == [
        (("Jessica Liu",), ("Seattle, Washington",), {"remote": True})
    ]


def test_an_employee_works_on_the_products_her_role_names_and_only_those():
    text = (
        "## Career\n- Develops interfaces for Rellm and reviews Carllm-style pages.\n"
        "- Not Homellmx.\n"
    )
    extraction = EX.extract(employee("Jessica Liu", text, index=1))
    assert [t for _, t, _ in edges_of(extraction, "WORKS_ON")] == [("Carllm",), ("Rellm",)]


def test_a_chunk_of_an_hr_record_with_no_facts_yields_nothing():
    assert EX.extract(employee("Alex Chen", "## Feedback\nGood work.", index=2)) == Extraction()


def test_the_person_is_named_by_the_file_not_by_the_records_own_heading():
    extraction = EX.extract(employee("Alex Chen", "- **Job Title:** Engineer\n"))
    assert nodes_of(extraction, "Employee")[0][0] == {"name": "Alex Chen"}


def test_someone_not_on_the_roster_is_a_warning_and_no_node():
    extraction = EX.extract(employee("Zed Nobody", "- **Job Title:** Engineer\n"))
    assert extraction.nodes == () and "not on the roster" in extraction.warnings[0]


def test_an_hr_record_that_says_the_person_co_founded_insurellm_gives_a_founded_edge():
    text = "- 2015: Co-Founder\n  Avery Lancaster co-founded Insurellm in 2015.\n"
    extraction = EX.extract(employee("Avery Lancaster", text))
    assert edges_of(extraction, "FOUNDED") == [(("Avery Lancaster",), ("Insurellm",), {})]


# --- company pages and products ---------------------------------------------------------------

ABOUT = (
    "# About Insurellm\n\nInsurellm was founded by Avery Lancaster in 2015. The company maintains "
    "its San Francisco headquarters along with small satellite offices in key markets including "
    "New York, Austin, and Seattle.\n"
)
OVERVIEW = (
    "# Overview\n\nInsurellm is a firm with offices in San Francisco (HQ), New York, Austin, "
    "and Seattle.\n"
)


def test_the_about_page_says_who_founded_the_company_and_where_it_keeps_offices():
    extraction = EX.extract(company_page("about", ABOUT))
    assert edges_of(extraction, "FOUNDED") == [(("Avery Lancaster",), ("Insurellm",), {})]
    assert {(t, p["headquarters"]) for _, t, p in edges_of(extraction, "HAS_OFFICE")} == {
        (("San Francisco, California",), True),
        (("New York, New York",), False),
        (("Austin, Texas",), False),
        (("Seattle, Washington",), False),
    }


def test_the_two_wordings_of_the_offices_give_the_same_edges_and_flags():
    about = EX.extract(company_page("about", ABOUT))
    overview = EX.extract(company_page("overview", OVERVIEW))
    assert edges_of(about, "HAS_OFFICE") == edges_of(overview, "HAS_OFFICE")


def test_a_founder_who_is_not_on_the_roster_is_a_warning():
    extraction = EX.extract(company_page("about", "Insurellm was founded by Someone Else in 2015."))
    assert edges_of(extraction, "FOUNDED") == [] and "not on the roster" in extraction.warnings[0]


def test_an_office_city_with_no_known_state_is_a_warning_and_no_guess():
    extraction = EX.extract(company_page("overview", "Insurellm has offices in Atlantis (HQ)."))
    assert edges_of(extraction, "HAS_OFFICE") == [] and "Atlantis" in extraction.warnings[0]


def test_a_company_page_chunk_that_names_the_company_mentions_it_and_one_that_does_not_does_not():
    assert mentioned(EX.extract(company_page("culture", "Insurellm values curiosity."))) == {
        "Insurellm"
    }
    assert EX.extract(company_page("culture", "We value curiosity.", index=1)) == Extraction()


def test_a_product_page_gives_its_product_from_the_first_chunk_only():
    page = make("products", "products/Carllm.md", "# Carllm\n\nAuto insurance portal.")
    assert nodes_of(EX.extract(page), "Product") == [({"name": "Carllm"}, {})]
    later = make("products", "products/Carllm.md", "Pricing.", index=1)
    assert EX.extract(later) == Extraction()


def test_a_product_page_for_an_unknown_product_is_a_warning():
    page = make("products", "products/Widgetllm.md", "# Widgetllm")
    assert EX.extract(page).nodes == () and "not a known product" in EX.extract(page).warnings[0]


def test_a_category_with_no_rules_is_a_warning_not_silence():
    extraction = EX.extract(make("faq", "faq/q.md", "text"))
    assert extraction.nodes == () and "no rules for category" in extraction.warnings[0]


# --- what every extraction promises ------------------------------------------------------------


def test_every_edge_endpoint_is_among_the_nodes_of_the_same_extraction():
    chunks = [
        contract(ATLANTIC, f"# {ATLANTIC}\n\n" + SIGNED_A),
        employee(
            "Jessica Liu",
            "- **Job Title:** X\n- **Location:** Remote (Based in Seattle, Washington)\n",
        ),
        company_page("about", ABOUT),
    ]
    for extraction in EX.extract_all(chunks):
        present = {(n.label, tuple(sorted(n.key.items()))) for n in extraction.nodes}
        for edge in extraction.edges:
            for ref in (edge.source, edge.target):
                assert (ref.label, tuple(sorted(ref.key.items()))) in present


def test_a_node_emitted_twice_in_one_chunk_is_emitted_once_with_all_its_properties():
    title = "Contract with DriveSmart Insurance for Carllm"
    text = f"# {title}\n\n**Parties:**\n- Insurellm, Inc.\n- DriveSmart Insurance Corp.\n"
    extraction = EX.extract(contract(title, text))
    assert len([n for n in extraction.nodes if n.label == "Company"]) == 1


def test_extraction_is_repeatable():
    chunk = employee("Jessica Liu", "- **Job Title:** X\n- **Location:** Austin, Texas\nRellm.\n")
    assert EX.extract(chunk) == EX.extract(chunk)


# --- mentions: the bridge back to the passage --------------------------------------------------


def test_an_entity_is_mentioned_when_its_name_is_in_the_chunk_text():
    title = "Contract with Belvedere Insurance for Markellm"
    extraction = EX.extract(contract(title, f"# {title}\n\nBelvedere Insurance uses Markellm."))
    assert mentioned(extraction) == {"Belvedere Insurance", "Markellm"}


def test_a_contract_is_never_mentioned_because_it_is_never_named():
    title = "Contract with Belvedere Insurance for Markellm"
    extraction = EX.extract(contract(title, f"# {title}"))
    assert all(ref.label != "Contract" for ref in extraction.mentions)


def test_the_legal_name_counts_as_a_way_the_text_can_name_the_company():
    # "Pinnacle Insurance Co." is not inside "Pinnacle Insurance Company", so only the legal
    # name can make this chunk mention the client.
    title = "Contract with Pinnacle Insurance Co. for Homellm"
    text = "**Parties:**\n- Insurellm, Inc.\n- Pinnacle Insurance Company\n"
    assert mentioned(EX.extract(contract(title, text, index=1))) == {"Pinnacle Insurance Co."}


def test_a_name_cut_across_a_line_still_counts_as_mentioned():
    extraction = EX.extract(contract(HARMONY, HEAD, index=2), lookahead=TAIL)
    assert "Dr. Karen Phillips" in mentioned(extraction)


def test_a_city_is_mentioned_by_its_city_name_alone():
    extraction = EX.extract(company_page("overview", OVERVIEW))
    assert {"San Francisco, California", "Austin, Texas"} <= mentioned(extraction)


def test_a_name_only_in_the_lookahead_is_not_a_mention_even_when_the_node_exists():
    page = make("products", "products/Carllm.md", "Auto insurance portal.")
    extraction = EX.extract(page, lookahead="Carllm continues here")
    assert [n.key["name"] for n in extraction.nodes] == ["Carllm"] and extraction.mentions == ()


def test_an_entity_named_only_in_the_lookahead_is_not_mentioned_by_this_chunk():
    extraction = EX.extract(
        employee("Jessica Liu", "Works on other things.", index=2), lookahead="Rellm"
    )
    assert extraction.mentions == ()


def test_a_product_named_only_in_the_lookahead_is_not_something_this_employee_works_on():
    extraction = EX.extract(
        employee("Jessica Liu", "Other duties.", index=2), lookahead="Rellm work"
    )
    assert extraction.edges == () and extraction.nodes == ()


def test_a_name_inside_a_longer_word_is_not_a_mention():
    page = make("products", "products/Carllm.md", "# Carllmania is a different thing")
    assert EX.extract(page).mentions == ()


def test_extract_all_does_not_depend_on_the_order_the_chunks_are_listed_in():
    first, second = contract(HARMONY, HEAD, index=2), contract(HARMONY, TAIL, index=3)
    forwards = EX.extract_all([first, second])
    backwards = EX.extract_all([second, first])
    assert forwards[0] == backwards[1] and forwards[1] == backwards[0]


# --- the closed lists come from the corpus -----------------------------------------------------


def test_the_closed_lists_are_read_from_the_chunks():
    chunks = [
        make("products", "products/Bizllm.md", "# Bizllm"),
        make("employees", "employees/Ann Lee.md", "- **Location:** Denver, CO\n"),
        make(
            "employees",
            "employees/Bo Ray.md",
            "- **Location:** Remote (Based in Denver, Colorado)\n",
        ),
        make("contracts", "contracts/Contract with X for Bizllm.md", "# t"),
    ]
    extractor = GraphExtractor.from_chunks(chunks)
    overview = company_page("overview", "Insurellm has offices in Denver (HQ).")
    assert nodes_of(extractor.extract(overview), "Location") == [({"name": "Denver, Colorado"}, {})]


# --- the same checks on the real corpus (skipped when the data is not there) ------------------

needs_data = pytest.mark.skipif(
    not (ROOT / "data" / "chunks").is_dir() or not (ROOT / "data" / "contracts").is_dir(),
    reason="data/ is not present",
)


@pytest.fixture(scope="module")
def corpus():
    chunks = read_chunks(ROOT / "data" / "chunks")
    extractor = GraphExtractor.from_chunks(chunks)
    extractions = extractor.extract_all(chunks)
    return chunks, extractor, extractions


def distinct(extractions, what):
    seen = {}
    for extraction in extractions:
        for item in getattr(extraction, what):
            if what == "nodes":
                seen[(item.label, tuple(sorted(item.key.items())))] = item
            else:
                seen[
                    (item.type, item.source.label, tuple(sorted(item.source.key.items())))
                    + (item.target.label, tuple(sorted(item.target.key.items())))
                ] = item
    return list(seen.values())


@needs_data
def test_corpus_every_chunk_is_read_and_nothing_is_warned_about(corpus):
    chunks, _, extractions = corpus
    assert len(chunks) == 223 and len(extractions) == 223
    assert [w for e in extractions for w in e.warnings] == []


@needs_data
def test_corpus_the_entities_match_the_audit_counts(corpus):
    _, _, extractions = corpus
    labels = Counter(n.label for n in distinct(extractions, "nodes"))
    # 32 contracts, 32 HR records, 8 product pages (P4-M2 audit); the 32 clients plus Insurellm.
    assert (labels["Contract"], labels["Employee"], labels["Product"]) == (32, 32, 8)
    assert labels["Company"] == 33


@needs_data
def test_corpus_every_contract_has_a_client_and_a_product_and_every_employee_a_place(corpus):
    _, _, extractions = corpus
    edges = Counter(e.type for e in distinct(extractions, "edges"))
    assert (edges["WITH_CLIENT"], edges["FOR_PRODUCT"], edges["BASED_IN"]) == (32, 32, 32)
    remote = [
        e for e in distinct(extractions, "edges") if e.type == "BASED_IN" and e.properties["remote"]
    ]
    assert len(remote) == 5  # the audit's five "Remote (Based in ...)" records


@needs_data
def test_corpus_exactly_six_employees_work_on_a_product_and_they_are_the_audits_six(corpus):
    _, _, extractions = corpus
    pairs = {
        (e.source.key["name"], e.target.key["name"])
        for e in distinct(extractions, "edges")
        if e.type == "WORKS_ON"
    }
    assert pairs == {
        ("Jessica Liu", "Rellm"),
        ("Michelle Rivera", "Rellm"),
        ("Rachel Martinez", "Carllm"),
        ("Robert Chen", "Homellm"),
        ("Sarah Williams", "Homellm"),
        ("Tyler Brooks", "Carllm"),
    }


@needs_data
def test_corpus_twelve_clients_have_a_legal_name_and_eleven_of_them_differ_from_the_title(corpus):
    _, _, extractions = corpus
    legal = {
        n.key["name"]: n.properties["legal_name"]
        for n in distinct(extractions, "nodes")
        if n.label == "Company" and "legal_name" in n.properties
    }
    assert len(legal) == 12 and sum(1 for k, v in legal.items() if k != v) == 11


@needs_data
def test_corpus_the_company_has_five_offices_one_of_them_the_headquarters_and_one_founder(corpus):
    _, _, extractions = corpus
    edges = distinct(extractions, "edges")
    offices = [e for e in edges if e.type == "HAS_OFFICE"]
    assert len(offices) == 5
    assert [e.target.key["name"] for e in offices if e.properties["headquarters"]] == [
        "San Francisco, California"
    ]
    assert [(e.source.key["name"], e.target.key["name"]) for e in edges if e.type == "FOUNDED"] == [
        ("Avery Lancaster", "Insurellm")
    ]


# The audit's own pattern, written separately from the extractor's so the two can disagree.
AUDIT_BLOCK = re.compile(
    r"\*\*(?P<name>[^*\n]+?)\*\*[ \t]*\n"
    r"\*\*Title\*\*:?[ \t]*(?P<title>[^\n]+?)[ \t]*\n"
    r"\*\*(?P<org>[^*\n]+?)\*\*"
)


@needs_data
def test_corpus_the_audits_signature_blocks_are_all_found_and_nothing_else_is_taken_for_one(corpus):
    _, _, extractions = corpus
    audit = set()
    total = 0
    for path in sorted((ROOT / "data" / "contracts").glob("*.md")):
        doc_id = compute_doc_id(f"contracts/{path.name}")
        for m in AUDIT_BLOCK.finditer(path.read_text(encoding="utf-8")):
            total += 1
            if not m["name"].startswith("["):
                audit.add((doc_id, m["name"].strip(), m["title"].strip()))
    assert total == 34  # 34 blocks in 17 contracts, two of them unfilled (P4-M2 audit)

    found = {
        (e.source.key["doc_id"], e.target.key["name"], e.properties["title"])
        for e in distinct(extractions, "edges")
        if e.type == "SIGNED_BY"
    }
    assert audit <= found and len(audit) == 32
    # The other two layouts add signatories the audit did not count; measured, not assumed.
    assert len(found) == 46 and len(found - audit) == 14


@needs_data
def test_corpus_reading_chunks_finds_exactly_what_reading_each_whole_document_finds(corpus):
    chunks, extractor, extractions = corpus
    by_document: dict[str, list] = {}
    for chunk in chunks:
        if chunk.category == "contracts":
            by_document.setdefault(chunk.source_path, []).append(chunk)

    def signatories(extraction_list):
        return {
            (
                e.source.key["doc_id"],
                e.target.key["name"],
                e.target.key["organization"],
                e.properties["title"],
            )
            for extraction in extraction_list
            for e in extraction.edges
            if e.type == "SIGNED_BY"
        }

    chunked = signatories(e for c, e in zip(chunks, extractions) if c.category == "contracts")
    whole = set()
    for source_path, parts in by_document.items():
        text = (ROOT / "data" / source_path).read_text(encoding="utf-8")
        first = parts[0]
        whole_chunk = DocumentChunk(
            chunk_id=first.chunk_id,
            doc_id=first.doc_id,
            source_path=source_path,
            category="contracts",
            title=first.title,
            chunk_index=1,
            text=text,
            char_count=len(text),
            approx_token_count=0,
        )
        whole |= signatories([extractor.extract(whole_chunk)])
    assert chunked == whole  # one signature is cut by the chunker and only the lookahead saves it


@needs_data
def test_corpus_without_the_lookahead_exactly_one_signatory_is_lost(corpus):
    chunks, extractor, extractions = corpus
    with_lookahead = sum(1 for e in distinct(extractions, "edges") if e.type == "SIGNED_BY")
    without = sum(
        1
        for e in distinct([extractor.extract(c) for c in chunks], "edges")
        if e.type == "SIGNED_BY"
    )
    assert with_lookahead - without == 1


@needs_data
def test_corpus_the_extraction_composes_with_deduplication_and_no_edge_is_left_dangling(corpus):
    _, extractor, extractions = corpus
    nodes = [n for e in extractions for n in e.nodes]
    edges = [e for x in extractions for e in x.edges]
    titles = sorted({n.key["name"] for n in nodes if n.label == "Company"} - {"Insurellm"})

    result = resolve_entities(
        nodes,
        known_names={"Employee": sorted(extractor._employees), "Company": [*titles, "Insurellm"]},
    )
    counts = Counter(n.label for n in result.nodes)
    assert (counts["Company"], counts["Employee"], counts["Location"]) == (33, 32, 9)
    assert result.ambiguous == () and result.conflicts == ()
    # The five titles that end in Inc. or Co. keep them: that is what passing them as names is for.
    assert {"Pinnacle Insurance Co.", "Premier Adjusters Inc."} <= {
        n.key["name"] for n in result.nodes if n.label == "Company"
    }

    present = {(n.label, tuple(sorted(n.key.items()))) for n in result.nodes}
    for edge in remap_edges(edges, result.alias_map):
        for ref in (edge.source, edge.target):
            assert (ref.label, tuple(sorted(ref.key.items()))) in present, edge


@needs_data
def test_corpus_a_contacts_organisation_is_always_insurellm_or_a_clients_own_key(corpus):
    _, _, extractions = corpus
    clients = {n.key["name"] for e in extractions for n in e.nodes if n.label == "Company"}
    organisations = {
        n.key["organization"] for n in distinct(extractions, "nodes") if n.label == "Contact"
    }
    assert organisations <= clients
