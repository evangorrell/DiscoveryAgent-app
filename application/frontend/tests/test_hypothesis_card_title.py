"""Card-title contract: parsing + display normalization.

A hypothesis card title is a concise scientific *label*, never the full
hypothesis claim (which stays in the Hypothesis field). These tests pin the
shared pure helpers in select_hypothesis.py and the read-side display guard in
app._v2_normalize_hypothesis_snapshot, which together keep collapsed cards
scannable for both freshly generated and already-persisted (historical) sets.

No model calls, no Flask app, no S3 — pure functions only.
"""
import re

from da_selection_ui import app as app_module
from da_selection_ui.select_hypothesis import (
    InteractiveHypothesisGenerator,
    CARD_TITLE_MAX_WORDS,
    CARD_TITLE_PREFERRED_WORDS,
    display_card_title,
    derive_concise_card_title,
    title_needs_card_rewrite,
)


def _parser():
    # parse_hypothesis_sections is a pure instance method; no __init__ needed.
    return object.__new__(InteractiveHypothesisGenerator)


# --- fixtures ----------------------------------------------------------------

GOOD_BOLD = (
    "**Title**: AlphaONS-Enriched Resilience Signature as a Regeneration-Permissive State\n\n"
    "**Hypothesis**: The 43_AlphaONS subtype harbors a baseline program enriched for "
    "regeneration orthologs relative to vulnerable subtypes.\n\n"
    "**Justification**: The background report shows 43_AlphaONS is the most resilient "
    "subtype after ONC.\n\n"
    "**Plan**: Compare ortholog enrichment across subtypes."
)

GOOD_PLAIN = (
    "Title: MAPT/Tau Suppression as a Brake on Microtubule-Based Axon Extension\n\n"
    "Hypothesis: Tau suppression releases a microtubule brake and permits axon growth.\n\n"
    "Justification: Prior work links tau dosage to microtubule dynamics.\n\n"
    "Plan: Knock down Mapt and measure neurite length."
)

# Model wrote a full hypothesis sentence into the Title field (the Round-4 bug).
SENTENCE_TITLE = (
    "**Title**: W3-type RGCs represent a \"regeneration-primed but survival-deficient\" "
    "state that can be computationally decomposed to identify the specific survival "
    "bottleneck preventing utilization of their pre-loaded regeneration program.\n\n"
    "**Hypothesis**: The W3D1.1 subtype pairs high regeneration readiness with low "
    "survival capacity, and this gap is decomposable into a specific bottleneck.\n\n"
    "**Justification**: Readiness and survival scores diverge for W3-type cells.\n\n"
    "**Plan**: Decompose the survival deficit factor by factor."
)

MISSING_TITLE = (
    "**Hypothesis**: Microglial state gates regeneration competence in injured RGCs.\n\n"
    "**Justification**: Single-cell data suggests a microglial axis."
)

SHORT_VALID_TITLE = (
    "**Title**: Rgs5 as an AlphaONS Resilience Marker\n\n"
    "**Hypothesis**: Rgs5 expression marks the resilient AlphaONS subtype after ONC.\n\n"
    "**Justification**: Rgs5 concentrates in AlphaONS control RGCs."
)


# --- parser ------------------------------------------------------------------

def test_parses_bold_title_field():
    sections = _parser().parse_hypothesis_sections(GOOD_BOLD)
    assert sections["title"] == "AlphaONS-Enriched Resilience Signature as a Regeneration-Permissive State"
    assert sections["hypothesis"].startswith("The 43_AlphaONS subtype")
    assert sections["justification"].startswith("The background report")
    assert sections["plan"] == "Compare ortholog enrichment across subtypes."


def test_parses_plain_label_title_field():
    sections = _parser().parse_hypothesis_sections(GOOD_PLAIN)
    assert sections["title"] == "MAPT/Tau Suppression as a Brake on Microtubule-Based Axon Extension"
    assert sections["hypothesis"].startswith("Tau suppression releases")
    assert sections["plan"].startswith("Knock down Mapt")


def test_missing_title_parses_as_none_but_other_fields_survive():
    sections = _parser().parse_hypothesis_sections(MISSING_TITLE)
    assert sections["title"] is None
    assert sections["hypothesis"].startswith("Microglial state gates")
    assert sections["justification"].startswith("Single-cell data")


def test_hypothesis_label_colon_inside_bold_markers_parses_cleanly():
    sections = _parser().parse_hypothesis_sections(
        "**Hypothesis:** Axon growth remains visible.\n\n"
        "**Justification:** The evidence remains visible too."
    )
    assert sections["hypothesis"] == "Axon growth remains visible."
    assert sections["justification"] == "The evidence remains visible too."


def test_hypothesis_label_colon_outside_bold_markers_remains_supported():
    sections = _parser().parse_hypothesis_sections(
        "**Hypothesis**: Axon growth remains visible.\n\n"
        "**Justification**: The evidence remains visible too."
    )
    assert sections["hypothesis"] == "Axon growth remains visible."
    assert sections["justification"] == "The evidence remains visible too."


def test_hypothesis_and_justification_labels_without_colons_remain_visible():
    sections = _parser().parse_hypothesis_sections(
        "**Hypothesis** Axon growth remains visible.\n\n"
        "**Justification** The evidence remains visible too."
    )
    assert sections["hypothesis"] == "Axon growth remains visible."
    assert sections["justification"] == "The evidence remains visible too."


def test_changed_emphasis_markers_and_plain_labels_parse_consistently():
    single_emphasis = _parser().parse_hypothesis_sections(
        "*Hypothesis* Axon growth remains visible.\n\n"
        "_Justification_: The evidence remains visible too."
    )
    plain = _parser().parse_hypothesis_sections(
        "Hypothesis: Axon growth remains visible.\n\n"
        "Justification: The evidence remains visible too."
    )
    assert single_emphasis["hypothesis"] == plain["hypothesis"] == "Axon growth remains visible."
    assert single_emphasis["justification"] == plain["justification"] == "The evidence remains visible too."


def test_hypothesis_label_whitespace_variation_does_not_hide_content():
    sections = _parser().parse_hypothesis_sections(
        "  **Hypothesis**   :   Axon growth remains visible.\n\n"
        "\t**Justification:**\tThe evidence remains visible too."
    )
    assert sections["hypothesis"] == "Axon growth remains visible."
    assert sections["justification"] == "The evidence remains visible too."


def test_unexpected_document_format_falls_back_to_complete_visible_text():
    edited = "A scientist-authored free-form hypothesis with no structured Markdown label."
    sections = _parser().parse_hypothesis_sections(edited)
    assert sections["hypothesis"] == edited
    assert app_module._parse_hypothesis_sections_standalone(edited)["hypothesis"] == edited


def test_partial_or_empty_hypothesis_section_never_produces_an_empty_card():
    edited = "**Hypothesis**\n\nEvidence-driven free-form text after an empty label."
    sections = _parser().parse_hypothesis_sections(edited)
    assert sections["hypothesis"] == "Evidence-driven free-form text after an empty label."
    assert sections["hypothesis"]


def test_read_side_snapshot_uses_same_tolerant_parser_and_preserves_full_text():
    edited = (
        "**Hypothesis** Axon growth remains visible after a punctuation edit.\n\n"
        "**Justification** The supporting evidence is still displayed."
    )
    snap = app_module._v2_normalize_hypothesis_snapshot({"full_text": edited}, 1)
    assert snap["full_text"] == edited
    assert snap["hypothesis"] == "Axon growth remains visible after a punctuation edit."
    assert snap["justification"] == "The supporting evidence is still displayed."
    assert snap["title"]


# --- rewrite decision --------------------------------------------------------

def test_valid_concise_title_is_left_alone():
    title = "AlphaONS-Enriched Resilience Signature as a Regeneration-Permissive State"
    assert title_needs_card_rewrite(title) is False
    assert display_card_title(title) == title


def test_short_but_clear_title_is_valid_even_below_preferred_length():
    # "Rgs5 as an AlphaONS Resilience Marker" is 6 words: shorter than the
    # preferred 10-14 band but a clear scientific label, so it is kept as-is.
    title = "Rgs5 as an AlphaONS Resilience Marker"
    assert len(title.split()) < CARD_TITLE_PREFERRED_WORDS
    assert title_needs_card_rewrite(title) is False
    assert display_card_title(title) == title


def test_title_longer_than_hard_limit_is_rewritten_within_budget():
    long_title = (
        "ATF3 acts as a locus-specific transcriptional repressor at poised developmental "
        "guidance gene loci, and its removal or functional replacement at Vangl2 and Rgs3 "
        "enhancers would unlock regeneration-phase transcription in mouse RGCs"
    )
    assert len(long_title.split()) > CARD_TITLE_MAX_WORDS
    assert title_needs_card_rewrite(long_title) is True
    display = display_card_title(long_title)
    assert len(display.split()) <= CARD_TITLE_PREFERRED_WORDS
    assert display.startswith("ATF3 locus-specific transcriptional repressor")
    # The mechanistic tail belongs in the hypothesis, not the card.
    assert "would unlock" not in display


def test_full_sentence_title_is_replaced_by_a_label():
    title = (
        'W3-type RGCs represent a "regeneration-primed but survival-deficient" state '
        "that can be computationally decomposed to identify the specific survival "
        "bottleneck preventing utilization of their pre-loaded regeneration program."
    )
    assert title_needs_card_rewrite(title) is True
    display = display_card_title(title)
    # A title-like noun phrase, NOT a mechanical truncation of the sentence: the
    # trailing clause is dropped whole (not clipped mid-phrase), and there is no
    # ellipsis. The leading target/state phrase survives.
    assert display == 'W3-type RGCs "regeneration-primed but survival-deficient" state'
    assert len(display.split()) <= CARD_TITLE_PREFERRED_WORDS
    assert not display.endswith("...")
    assert "computationally decomposed" not in display
    assert "specific survival" not in display


def test_valid_title_over_120_chars_but_within_word_budget_is_not_rewritten():
    # 12 words but 137 characters: long in characters, fine in words. Character
    # count must NEVER trigger a rewrite — the full title is returned verbatim.
    title = (
        "Pseudobulk Correction Reveals a Sample-Replicate-Validated Zebrafish "
        "Regeneration Core Distinct from Single-Replicate-Dominated Artifacts"
    )
    assert len(title.split()) <= CARD_TITLE_MAX_WORDS
    assert len(title) > 120
    assert title_needs_card_rewrite(title) is False
    assert display_card_title(title) == title


def test_long_title_identical_to_hypothesis_is_rewritten():
    # A long claim duplicated into the Title field is rewritten to a label...
    claim = (
        "Microglial reactive-state transitions gate regeneration competence across all "
        "injured RGC subtypes by controlling a shared inflammatory checkpoint upstream "
        "of intrinsic axon growth programs."
    )
    assert len(claim.split()) > CARD_TITLE_PREFERRED_WORDS
    assert title_needs_card_rewrite(claim, claim) is True
    display = display_card_title(claim, claim)
    assert 0 < len(display.split()) <= CARD_TITLE_PREFERRED_WORDS


def test_short_title_equal_to_hypothesis_is_kept_as_fallback_label():
    # ...but a short one-line hypothesis used as a fallback title is fine as-is.
    claim = "RGC survival via mTOR reactivation."
    assert len(claim.split()) <= CARD_TITLE_PREFERRED_WORDS
    assert title_needs_card_rewrite(claim, claim) is False
    assert display_card_title(claim, claim) == claim


def test_derive_from_hypothesis_when_title_missing():
    hypothesis = (
        "Ribosomal protein stoichiometric imbalance across RGC subtypes predicts "
        "differential translational capacity and downstream regeneration outcomes."
    )
    derived = derive_concise_card_title(None, hypothesis)
    assert derived and derived != "Untitled research direction"
    assert len(derived.split()) <= CARD_TITLE_PREFERRED_WORDS


# --- read-side display guard preserves scientific content --------------------

def test_snapshot_normalizes_display_title_but_preserves_all_sections():
    stored = {
        "index": 1,
        "state_id": "abc",
        "title": (
            'W3-type RGCs represent a "regeneration-primed but survival-deficient" state '
            "that can be computationally decomposed to identify the specific survival "
            "bottleneck preventing utilization of their pre-loaded regeneration program."
        ),
        "full_text": SENTENCE_TITLE,
        "hypothesis": (
            "The W3D1.1 subtype pairs high regeneration readiness with low survival "
            "capacity, and this gap is decomposable into a specific bottleneck."
        ),
        "justification": "Readiness and survival scores diverge for W3-type cells.",
        "plan": "Decompose the survival deficit factor by factor.",
    }
    snap = app_module._v2_normalize_hypothesis_snapshot(stored, 1)

    # Display title is now a concise label...
    assert len(snap["title"].split()) <= CARD_TITLE_PREFERRED_WORDS
    assert snap["title"].startswith("W3-type RGCs")
    # ...the original long title is preserved for debugging...
    assert snap["raw_title"] == stored["title"]
    # ...and the scientific content is untouched.
    assert snap["full_text"] == SENTENCE_TITLE
    assert snap["hypothesis"] == stored["hypothesis"]
    assert snap["justification"] == stored["justification"]
    assert snap["plan"] == stored["plan"]


def test_snapshot_leaves_good_title_and_sections_intact():
    stored = {
        "index": 1,
        "title": "AlphaONS-Enriched Resilience Signature as a Regeneration-Permissive State",
        "full_text": GOOD_BOLD,
        "hypothesis": "The 43_AlphaONS subtype harbors a baseline program.",
        "justification": "The background report shows resilience.",
        "plan": "Compare ortholog enrichment across subtypes.",
    }
    snap = app_module._v2_normalize_hypothesis_snapshot(stored, 1)
    assert snap["title"] == stored["title"]
    assert "raw_title" not in snap  # nothing was rewritten
    assert snap["hypothesis"] == stored["hypothesis"]
    assert snap["justification"] == stored["justification"]
    assert snap["plan"] == stored["plan"]


# --- display layer never truncates valid titles ------------------------------

def _static_dir():
    from pathlib import Path
    return Path(app_module.__file__).parent / "static"


def _css_rule_bodies(css: str, selector_token: str) -> list[str]:
    """Return the `{ ... }` body of every top-level rule whose selector list
    contains *selector_token* as a token (e.g. ".hs-card-title" matches a
    selector of exactly that, or a comma-separated list containing it, but
    not an unrelated selector like ".hs-round-item-name" that merely shares
    a text-overflow declaration elsewhere in the stylesheet).
    """
    bodies = []
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        tokens = {part.strip() for part in selector.split(",")}
        if selector_token in tokens:
            bodies.append(body)
    return bodies


def test_collapsed_card_css_does_not_clamp_or_ellipsize_titles():
    css = (_static_dir() / "hypothesis_studio.css").read_text(encoding="utf-8")
    # Scoped to the card-title rule blocks themselves, not the whole
    # stylesheet: other, unrelated elements (e.g. the fixed-width round-list
    # sidebar item name) are free to use ellipsis for their own long-text
    # truncation without tripping this contract. What must never happen is
    # the *hypothesis card title* being clamped or ellipsized — valid titles
    # are word-budgeted and always shown in full.
    card_title_selectors = [".hs-card-title", ".hs-card-titlebtn", ".hs-card.is-collapsed", ".hs-card"]
    card_title_css = "\n".join(
        body for selector in card_title_selectors for body in _css_rule_bodies(css, selector)
    )
    assert card_title_css, "expected to find at least one .hs-card-title rule block"
    assert "-webkit-line-clamp" not in card_title_css
    assert "line-clamp" not in card_title_css
    assert "text-overflow: ellipsis" not in card_title_css
    # Collapsed cards use a growable min-height (not a fixed height that clips)
    # and equalize to the tallest card in the static grid. Value-agnostic so a
    # linter re-tuning the px does not break the intent.
    assert ".hs-card.is-collapsed { min-height:" in css
    assert ".hs-card.is-collapsed { height:" not in css
    assert ".hs-hypothesis-surface.is-static-row .hs-card-grid {" in css


def test_frontend_title_contract_is_word_based_not_character_based():
    js = (_static_dir() / "hypothesis_studio.js").read_text(encoding="utf-8")
    # No character display max / character rewrite trigger remains in the JS.
    assert "CARD_TITLE_DISPLAY_CHARS" not in js
    assert "CARD_TITLE_REWRITE_CHARS" not in js
    # The word-based contract constants remain.
    assert "CARD_TITLE_MAX_WORDS = 18" in js
    assert "CARD_TITLE_PREFERRED_WORDS = 14" in js


def test_backend_title_contract_has_no_character_display_max():
    from da_selection_ui import select_hypothesis
    assert not hasattr(select_hypothesis, "CARD_TITLE_DISPLAY_CHARS")
    assert not hasattr(select_hypothesis, "_CARD_TITLE_REWRITE_CHARS")
    assert select_hypothesis.CARD_TITLE_MAX_WORDS == 18
    assert select_hypothesis.CARD_TITLE_PREFERRED_WORDS == 14
