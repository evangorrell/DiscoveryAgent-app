"""Focused tests for the canonical V2 Project Home product-state adapter
(GET /api/project_state and its deterministic preview extractors).

Temporary filesystem fixtures only — no live LLM, no S3, no generator restore
is required for the states under test.
"""
import json
from pathlib import Path

from da_selection_ui import app as app_module


# --- fixture builders --------------------------------------------------------

def _round_dir(root: Path, index: int) -> Path:
    d = root / "project" / "checkpoint" / "checkpoints" / f"round_{index}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_config(round_dir: Path, task_description: str = "") -> None:
    round_dir.joinpath("config.json").write_text(
        json.dumps({"config": {"task_description": task_description}}),
        encoding="utf-8",
    )


def _base_tree(root: Path, task_description: str = "Explore ACS housing data") -> None:
    code_dir = root / "project" / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "README.txt").write_text("dataset readme", encoding="utf-8")
    _write_config(_round_dir(root, 0), task_description)


def _complete_round(root: Path, index: int, *, memory=True, report=True,
                    selected=None, feedback=None, report_text=None) -> Path:
    rd = _round_dir(root, index)
    _write_config(rd, "Explore ACS housing data")
    if report:
        rd.joinpath("round_report.md").write_text(report_text or ROUND_REPORT, encoding="utf-8")
    if memory:
        rd.joinpath("memory_report.md").write_text(MEMORY_REPORT, encoding="utf-8")
    if selected is not None:
        rd.joinpath("selected_hypothesis.md").write_text(selected, encoding="utf-8")
    if feedback is not None:
        rd.joinpath("scientist_feedback.md").write_text(feedback, encoding="utf-8")
    return rd


MEMORY_REPORT = """# Memory-Integrated Report - Round 0

**Timestamp:** 2026-01-24T18:53:43

**Round:** 0

---

## Description

This report represents the accumulated knowledge from ResultsMemoryManager after integrating findings.

---

## Accumulated Report

# Background and Literature Review

## Overview

The study examined how housing affordability varies across the fifty states using detailed survey microdata, revealing substantial and persistent regional disparities that remain even after controlling for differences in household income levels.

Renters in coastal metropolitan areas devote a markedly larger share of household income to housing than renters in inland regions, a gap that has widened over the past decade according to the assembled multi-year estimates.

### References

[1] Some reference entry that should be excluded from the preview text entirely because it is not prose.
"""

ROUND_REPORT = """# Round 0 Report

**Timestamp:** 2026-01-24T18:53:43

**Round:** 0

---

## Description

This report contains the raw findings from Round 0 only.

---

## Round Report

# Background

## Overview

The background paragraph describes the dataset and its provenance in general terms across many states and multiple years of survey collection effort undertaken by the bureau.

## Summary of Key Findings

States with the highest rent burden also exhibited the largest increases in young adults living with parents, indicating a compounding affordability effect that the analysis quantified across all fifty states.
"""

SELECTED_HYP = """**Title**: Rent burden and household formation

**Hypothesis**: Higher state-level rent burden is associated with a greater share of young adults living with their parents rather than forming independent households.

**Justification**: Prior descriptive work suggests a link that this round will test directly, and should never appear in the direction preview.

**Plan**: Regress household-formation rates on rent burden.
"""

# A distinct selection used to prove the hypothesis-selected attention state
# reads the ACTIVE round's own selected hypothesis (not a previous round's).
SELECTED_HYP_2 = """**Title**: Migration and cost of living

**Hypothesis**: Regional cost-of-living gaps predict interstate migration among young workers.

**Justification**: this must never appear in the attention body.

**Plan**: this must never appear either.
"""

SELECTED_HYP_3 = """**Title**: Commuting and affordability

**Hypothesis**: Longer commute times compound affordability pressure for renters in high-cost metropolitan areas.

**Justification**: this must never appear in the evolution display.

**Plan**: this must never appear either.
"""

SELECTED_HYP_4 = """**Title**: Wage volatility and household formation

**Hypothesis**: Higher local wage volatility delays independent household formation among young adults.

**Justification**: this must never appear in the evolution display.

**Plan**: this must never appear either.
"""


def _client_with_session(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["checkpoint_s3_prefix"] = "project"
        flask_session["project_config"] = app_module._derive_project_config("project")
        flask_session["project_label"] = "Housing Affordability Study"
        # Canonical /projects/<slug>/... URL building needs the active
        # project's slug; this session simulates an already-activated V2
        # project (not a raw legacy/pre-registry session).
        flask_session["v2_active_project_slug"] = "housing-affordability-study"
    return client


def _get_state(client):
    resp = client.get("/api/project_state")
    assert resp.status_code == 200, resp.data
    data = resp.get_json()
    assert data["success"] is True
    return data


def _write_runner_state(root: Path, round_number: int, status: str) -> None:
    (_rounds_root(root).parent / "run_state.json").write_text(json.dumps({
        "project_id": "fixture-project",
        "round": round_number,
        "status": status,
        "pid": 424242 if status in {"starting", "running"} else None,
        "config_file": str(_round_dir(root, round_number) / "config.json"),
    }), encoding="utf-8")


def _status(data):
    status = data["research_status"]
    assert data["attention"] == status
    assert status["label"] == "RESEARCH STATUS"
    return status


# --- durable six-state Research Status machine -------------------------------

def test_brand_new_project_shows_start_first_round(monkeypatch, tmp_path):
    # Before ANY autonomous round has started, Project Home shows the
    # initial-exploration "Start your first round" state (Round 0 is autonomous
    # and directed by no user hypothesis) -- NOT "explore hypotheses".
    _base_tree(tmp_path)
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["mode"] == "pre_research"
    status = _status(data)
    assert status["state"] == "start_first_round"
    assert status["status_pill"] == "Ready to start"
    assert status["headline"] == "Ready to begin your first investigation."
    assert status["body"] == (
        "DiscoveryAgent will begin with an exploratory round to investigate "
        "the project context, data, and research objective."
    )
    assert status["cta_label"] == "Start Round 0"
    # CTA is a shared action, not a navigation destination.
    assert status["cta_action"] == "start_round"
    assert status["cta_round"] == 0
    assert status["cta_destination"] is None
    assert data["research_understanding"] is None
    assert "current_direction" not in data   # permanent direction section removed
    assert data["evolution"] == []
    assert data["recent_rounds"] == []
    # research goal + title from real sources, not hardcoded
    assert data["project"]["research_goal"] == "Explore ACS housing data"
    assert data["project"]["title"] == "Housing Affordability Study"


def test_pre_round_zero_candidate_artifact_does_not_fake_round_completion(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _round_dir(tmp_path, 0).joinpath("generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": ["H1", "H2"]}), encoding="utf-8")
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["mode"] == "pre_research"
    assert _status(data)["state"] == "start_first_round"


def test_selected_round_not_started(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _round_dir(tmp_path, 0).joinpath("selected_hypothesis.md").write_text(SELECTED_HYP, encoding="utf-8")
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["mode"] == "pre_research"
    status = _status(data)
    assert status["state"] == "hypothesis_selected"
    assert status["headline"] == "Your hypothesis is ready for investigation."
    assert status["cta_label"] == "Start Round 0"
    assert status["cta_round"] == 0


def test_running_round_in_progress(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    rd = _round_dir(tmp_path, 0)
    rd.joinpath("selected_hypothesis.md").write_text(SELECTED_HYP, encoding="utf-8")
    (rd / "query_000").mkdir()
    _write_runner_state(tmp_path, 0, "running")
    monkeypatch.setattr(app_module.local_round_runner, "_pid_matches_state", lambda state: True)
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["mode"] == "pre_research"
    status = _status(data)
    assert status["state"] == "research_in_progress"
    assert status["status_pill"] == "Round 0 in progress"
    assert status["headline"] == "Round 0 investigation is in progress."
    assert status["cta_label"] == "Open Round 0"
    assert status["cta_destination"] == "/rounds/?project=housing-affordability-study&round=0"


def test_running_query_evidence_keeps_exact_active_round_without_runner_record(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 4)
    r5 = _round_dir(tmp_path, 5)
    _write_config(r5, "Explore ACS housing data")
    (r5 / "query_000").mkdir()

    status = _status(_get_state(_client_with_session(monkeypatch, tmp_path)))

    assert status["state"] == "research_in_progress"
    assert status["round"] == 5
    assert status["status_pill"] == "Round 5 in progress"
    assert status["cta_destination"].endswith("round=5")


def test_starting_round_before_first_query_routes_home_to_workspace(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _write_runner_state(tmp_path, 0, "starting")
    monkeypatch.setattr(app_module.local_round_runner, "_pid_matches_state", lambda state: True)

    data = _get_state(_client_with_session(monkeypatch, tmp_path))

    assert data["mode"] == "pre_research"
    status = _status(data)
    assert status["state"] == "research_in_progress"
    assert status["headline"] == "Round 0 investigation is in progress."
    assert status["cta_label"] == "Open Round 0"
    assert status["cta_destination"] == "/rounds/?project=housing-affordability-study&round=0"


def test_stopped_incomplete_round_routes_home_to_round_actions(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _write_runner_state(tmp_path, 0, "interrupted")

    data = _get_state(_client_with_session(monkeypatch, tmp_path))

    assert data["mode"] == "pre_research"
    status = _status(data)
    assert status["state"] == "research_stopped"
    assert status["headline"] == "Round 0 was stopped."
    assert status["body"] == (
        "The investigation stopped before completion and cannot be resumed from "
        "a saved checkpoint. Resubmit the round to run it again."
    )
    assert status["cta_label"] == "Resubmit Round 0"
    assert status["cta_action"] == "resubmit_round"
    assert status["cta_destination"] == "/rounds/?project=housing-affordability-study&round=0"


def test_stopped_later_round_uses_exact_round_and_runner_resumability(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0, feedback="reviewed")
    _write_config(_round_dir(tmp_path, 1), "Explore ACS housing data")
    query = _round_dir(tmp_path, 1) / "query_000"
    query.mkdir()
    query.joinpath("checkpoint.json").write_text("{}", encoding="utf-8")
    _write_runner_state(tmp_path, 1, "interrupted")

    data = _get_state(_client_with_session(monkeypatch, tmp_path))

    assert data["mode"] == "research_briefing"
    status = _status(data)
    assert status["state"] == "research_stopped"
    assert status["status_pill"] == "Round 1 stopped"
    assert status["headline"] == "Round 1 was stopped."
    assert status["cta_label"] == "Resume Round 1"
    assert status["cta_action"] == "resume_round"
    assert status["cta_destination"] == "/rounds/?project=housing-affordability-study&round=1"
    assert app_module.local_round_runner.inspect_round_status(
        _round_dir(tmp_path, 0)
    )["resumable"] is True


# --- (5,6) completed round awaiting review + briefing mode -------------------

def test_completed_round_ready_for_review(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    # completed, no scientist_feedback.md and no fresh next-round selection
    _complete_round(tmp_path, 0)
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["mode"] == "research_briefing"
    assert "current_direction" not in data   # permanent direction section removed
    status = _status(data)
    assert status["state"] == "round_ready_for_review"
    assert status["status_pill"] == "Round 0 complete"
    assert status["headline"] == "Round 0 is complete and ready for review."
    assert status["body"] == (
        "Round 0 has finished. Review its findings before developing the "
        "hypothesis for the next investigation."
    )
    assert status["cta_label"] == "Review Round 0"
    assert status["cta_destination"] == "/rounds/?project=housing-affordability-study&round=0&review=1"


def test_review_marker_does_not_replace_round_complete_without_next_studio(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    # completed + reviewed, and no fresh next-round selection yet
    _complete_round(tmp_path, 0, feedback="Looks good, proceed.")
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["mode"] == "research_briefing"
    assert _status(data)["state"] == "round_ready_for_review"


# --- (7) briefing mode still reports current attention independently ---------

def test_briefing_still_reports_current_action(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0, selected=SELECTED_HYP, feedback="reviewed")
    r1 = _round_dir(tmp_path, 1)
    _write_config(r1, "Explore ACS housing data")
    (r1 / "query_000").mkdir()  # round_1 actively running
    _write_runner_state(tmp_path, 1, "running")
    monkeypatch.setattr(app_module.local_round_runner, "_pid_matches_state", lambda state: True)
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["mode"] == "research_briefing"
    status = _status(data)
    assert status["state"] == "research_in_progress"
    assert status["cta_label"] == "Open Round 1"
    assert status["headline"] == "Round 1 investigation is in progress."
    # understanding still sourced from completed round_0
    assert data["research_understanding"]["source_round"] == 0
    assert len(data["evolution"]) == 2


def test_round4_complete_then_durable_studio_then_round5_selection(monkeypatch, tmp_path):
    """States 2 -> 3 -> 4 use the completed round's durable Studio cycle."""
    import os

    _base_tree(tmp_path)
    r4 = _complete_round(tmp_path, 4)
    client = _client_with_session(monkeypatch, tmp_path)

    status = _status(_get_state(client))
    assert status["state"] == "round_ready_for_review"
    assert status["round"] == 4
    assert status["cta_destination"].endswith("round=4&review=1")

    app_module.hypothesis_history.append_events(r4, [
        app_module.hypothesis_history.hypothesis_set_generated_event([
            {"index": 1, "hypothesis": "A durable candidate."},
        ])
    ])
    status = _status(_get_state(client))
    assert status["state"] == "hypothesis_development"
    assert status["round"] == 5
    assert status["headline"] == "Develop the hypothesis for Round 5."
    assert status["cta_destination"] == "/projects/housing-affordability-study/hypotheses"

    selection = r4 / "selected_hypothesis.md"
    selection.write_text(SELECTED_HYP_2, encoding="utf-8")
    report_mtime = (r4 / "round_report.md").stat().st_mtime
    os.utime(selection, (report_mtime + 30, report_mtime + 30))
    status = _status(_get_state(client))
    assert status["state"] == "hypothesis_selected"
    assert status["round"] == 5
    assert status["cta_label"] == "Start Round 5"
    assert status["cta_round"] == 5


def test_research_status_tracks_current_studio_candidates_not_generation_history(
    monkeypatch, tmp_path,
):
    """State 3 follows Studio's current card projection, not past generation."""
    _base_tree(tmp_path)
    r4 = _complete_round(tmp_path, 4)
    client = _client_with_session(monkeypatch, tmp_path)

    # A completed round with no current candidates is ready for review.
    assert _status(_get_state(client))["state"] == "round_ready_for_review"
    assert app_module._v2_current_studio_hypotheses(r4) == []

    first_set = [
        {"index": 1, "state_id": "candidate-1", "hypothesis": "Candidate one."},
        {"index": 2, "state_id": "candidate-2", "hypothesis": "Candidate two."},
    ]
    r4.joinpath("generated_hypotheses.json").write_text(
        json.dumps({
            "hypotheses": [item["hypothesis"] for item in first_set],
            "hypothesis_state_ids": [item["state_id"] for item in first_set],
        }),
        encoding="utf-8",
    )
    app_module.hypothesis_history.append_events(r4, [
        app_module.hypothesis_history.hypothesis_set_generated_event(first_set),
    ])

    # Current cards put Home and Studio in hypothesis-development state.
    assert len(app_module._v2_current_studio_hypotheses(r4)) == 2
    assert _status(_get_state(client))["state"] == "hypothesis_development"

    # Removing the last card clears the operational cache and leaves an empty
    # current-set snapshot. The older generation event intentionally remains.
    r4.joinpath("generated_hypotheses.json").unlink()
    app_module.hypothesis_history.append_events(r4, [
        app_module.hypothesis_history.hypothesis_removed_event(
            1, first_set[0], [],
        ),
    ])
    events = app_module.hypothesis_history.load_history(r4)["events"]
    assert any(
        event["type"] == app_module.hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED
        for event in events
    )
    assert app_module._v2_active_hypotheses_from_history(r4) == []
    assert app_module._v2_current_studio_hypotheses(r4) == []
    status = _status(_get_state(client))
    assert status["state"] == "round_ready_for_review"
    assert status["round"] == 4

    # A newly generated current set makes State 3 available again.
    regenerated = [
        {"index": 1, "state_id": "candidate-3", "hypothesis": "A new candidate."},
    ]
    app_module.hypothesis_history.append_events(r4, [
        app_module.hypothesis_history.hypothesis_set_generated_event(regenerated),
    ])
    assert len(app_module._v2_current_studio_hypotheses(r4)) == 1
    status = _status(_get_state(client))
    assert status["state"] == "hypothesis_development"
    assert status["round"] == 5


def test_six_state_transition_chain_and_precedence(monkeypatch, tmp_path):
    """1 -> 5 -> 2 -> 3 -> 4 -> 5 -> 6 -> 5 without stale fallback."""
    import os

    _base_tree(tmp_path)
    client = _client_with_session(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module.local_round_runner, "_pid_matches_state", lambda state: True)
    assert _status(_get_state(client))["state"] == "start_first_round"

    _write_runner_state(tmp_path, 0, "running")
    assert _status(_get_state(client))["state"] == "research_in_progress"

    r0 = _complete_round(tmp_path, 0)
    _write_runner_state(tmp_path, 0, "completed")
    assert _status(_get_state(client))["state"] == "round_ready_for_review"

    r0.joinpath("generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": ["H1", "H2"]}), encoding="utf-8"
    )
    assert _status(_get_state(client))["state"] == "hypothesis_development"

    selection = r0 / "selected_hypothesis.md"
    selection.write_text(SELECTED_HYP_2, encoding="utf-8")
    report_mtime = (r0 / "round_report.md").stat().st_mtime
    os.utime(selection, (report_mtime + 30, report_mtime + 30))
    assert _status(_get_state(client))["state"] == "hypothesis_selected"

    r1 = _round_dir(tmp_path, 1)
    _write_config(r1, "Explore ACS housing data")
    query = r1 / "query_000"
    query.mkdir()
    query.joinpath("checkpoint.json").write_text("{}", encoding="utf-8")
    _write_runner_state(tmp_path, 1, "running")
    running = _status(_get_state(client))
    assert running["state"] == "research_in_progress"
    assert running["round"] == 1

    _write_runner_state(tmp_path, 1, "interrupted")
    stopped = _status(_get_state(client))
    assert stopped["state"] == "research_stopped"
    assert stopped["cta_action"] == "resume_round"

    _write_runner_state(tmp_path, 1, "running")
    resumed = _status(_get_state(client))
    assert resumed["state"] == "research_in_progress"
    assert resumed["round"] == 1


def test_nonresumable_round_resubmit_then_running(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0)
    r1 = _round_dir(tmp_path, 1)
    _write_config(r1, "Explore ACS housing data")
    _write_runner_state(tmp_path, 1, "interrupted")
    client = _client_with_session(monkeypatch, tmp_path)

    stopped = _status(_get_state(client))
    assert stopped["state"] == "research_stopped"
    assert stopped["cta_action"] == "resubmit_round"
    assert stopped["cta_label"] == "Resubmit Round 1"

    monkeypatch.setattr(app_module.local_round_runner, "_pid_matches_state", lambda state: True)
    _write_runner_state(tmp_path, 1, "running")
    running = _status(_get_state(client))
    assert running["state"] == "research_in_progress"
    assert running["round"] == 1


# --- (8) memory-report preview extraction -----------------------------------

def test_memory_preview_strips_scaffolding_returns_prose():
    preview = app_module._v2_understanding_preview(MEMORY_REPORT)
    assert preview is not None
    assert "housing affordability varies across the fifty states" in preview
    assert "Renters in coastal metropolitan areas" in preview
    for scaffold in ("Accumulated Report", "Timestamp", "Description", "ResultsMemoryManager"):
        assert scaffold not in preview
    assert "References" not in preview and "[1]" not in preview


def test_memory_preview_never_truncates_paragraph():
    preview = app_module._v2_understanding_preview(MEMORY_REPORT)
    # each returned paragraph must be a complete source paragraph
    for para in preview.split("\n\n"):
        assert para.strip().endswith(".")


def test_memory_preview_350_rule_adds_third_when_short():
    short = """## Accumulated Report

Regenerative programs were activated after injury in the model organism studied here.

Mouse retinal ganglion cells engaged those programs only weakly after comparable injury.

A regulatory bottleneck appears to limit effective transcription in the mammalian setting studied.
"""
    preview = app_module._v2_understanding_preview(short)
    # first two short paragraphs are < 350 chars combined -> third appended
    assert preview.count("\n\n") == 2
    assert "regulatory bottleneck" in preview


# --- selected/development state copy -----------------------------------------

def test_hypothesis_selected_shows_hypothesis_in_attention(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    # round_0 completed + reviewed -> research_briefing mode.
    _complete_round(tmp_path, 0, selected=SELECTED_HYP, feedback="reviewed")
    # round_1 freshly holds its OWN distinct selection, not yet started.
    r1 = _round_dir(tmp_path, 1)
    _write_config(r1, "Explore ACS housing data")
    r1.joinpath("selected_hypothesis.md").write_text(SELECTED_HYP_2, encoding="utf-8")
    data = _get_state(_client_with_session(monkeypatch, tmp_path))

    assert data["mode"] == "research_briefing"
    assert "current_direction" not in data
    attn = data["attention"]
    assert attn["state"] == "hypothesis_selected"
    assert attn["status_pill"] == "Ready for investigation"
    assert attn["headline"] == "Your hypothesis is ready for investigation."
    assert attn["cta_label"] == "Start Round 1"
    assert attn["body_lead"] is None
    assert attn["body"] == (
        "A hypothesis has been selected for Round 1. Start the investigation "
        "when you are ready."
    )


def test_briefing_generated_none_selected_copy(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    r0 = _complete_round(tmp_path, 0, feedback="reviewed")
    r0.joinpath("generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": ["H1", "H2"]}), encoding="utf-8")  # generated, none selected
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["mode"] == "research_briefing"
    attn = data["attention"]
    assert attn["state"] == "hypothesis_development"
    assert attn["status_pill"] == "Hypothesis development"
    assert attn["headline"] == "Develop the hypothesis for Round 1."
    assert attn["cta_label"] == "Open Hypothesis Studio"
    # fixed product copy only; no artifact framing / no lead-in
    assert attn["body_lead"] is None
    assert attn["body"] == (
        "Review, discuss, and refine the candidate hypotheses before selecting "
        "what DiscoveryAgent should investigate next."
    )


# --- running status uses fixed product copy ----------------------------------

def test_running_round_frames_investigated_hypothesis(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    # round_0 completed+reviewed provides round_1's launcher (cross-round);
    # round_1 is running.
    _complete_round(tmp_path, 0, selected=SELECTED_HYP_2, feedback="reviewed")
    r1 = _round_dir(tmp_path, 1)
    _write_config(r1, "Explore ACS housing data")
    (r1 / "query_000").mkdir()
    _write_runner_state(tmp_path, 1, "running")
    monkeypatch.setattr(app_module.local_round_runner, "_pid_matches_state", lambda state: True)
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    attn = data["attention"]
    assert attn["state"] == "research_in_progress"
    assert attn["body_lead"] is None
    assert attn["body"] == (
        "DiscoveryAgent is currently executing this investigation. Open the "
        "workspace to follow activity and inspect results as they are produced."
    )


def test_completed_without_next_studio_uses_fixed_product_copy(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0, feedback="reviewed")
    attn = _get_state(_client_with_session(monkeypatch, tmp_path))["attention"]
    assert attn["state"] == "round_ready_for_review"
    assert attn["body_lead"] is None
    assert attn["body"] == (
        "Round 0 has finished. Review its findings before developing the "
        "hypothesis for the next investigation."
    )


def test_central_finding_phrase_preferred_verbatim():
    report = ("## Round Report\n\n## Overview\n\nSome intro paragraph that is quite "
              "substantive and long enough to pass the filter here.\n\n## Results\n\n"
              "The central finding of this investigation is that rent burden strongly "
              "predicts household formation across the fifty states studied.\n")
    out = app_module._v2_round_outcome_preview(report)
    assert out == ("The central finding of this investigation is that rent burden strongly "
                   "predicts household formation across the fifty states studied.")


def test_outcome_preview_is_verbatim_no_llm_framing():
    # The completed-round body is a verbatim report paragraph; no prefix is added.
    out = app_module._v2_round_outcome_preview(ROUND_REPORT)
    assert out is not None
    assert out in ROUND_REPORT               # exact substring of the artifact
    assert not out.startswith("The central finding")  # no fabricated lead
    assert "rent burden" in out


def test_state_builder_writes_no_artifacts(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    root = _complete_round(tmp_path, 0)  # returns the round dir
    assert not hasattr(app_module, "_QUERY_SUMMARY_EXECUTOR")
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    _get_state(_client_with_session(monkeypatch, tmp_path))
    _get_state(_client_with_session(monkeypatch, tmp_path))
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert before == after  # no summary/finding artifact written by rendering


def test_query_summary_is_deterministic_and_writes_no_cache(tmp_path):
    query_dir = tmp_path / "query_001"
    query_dir.mkdir()
    (query_dir / "query_status.json").write_text(json.dumps({
        "status": "running",
        "objective": "Compare retinal ganglion cell survival across treatment and control cohorts.",
    }))

    expected = "Compare retinal ganglion cell survival across treatment and control cohorts."
    assert app_module._get_query_summary(query_dir) == expected
    assert app_module._get_query_summary(query_dir) == expected
    assert not (query_dir / "query_summary.txt").exists()


def test_state_builder_is_deterministic(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0)
    client = _client_with_session(monkeypatch, tmp_path)
    assert _get_state(client)["attention"] == _get_state(client)["attention"]


# --- (10) how the research evolved -------------------------------------------

def test_evolution_is_chronological_and_round0_is_exploratory(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0, selected=SELECTED_HYP, feedback="reviewed")
    _complete_round(tmp_path, 1, feedback="reviewed")
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    indices = [r["round_index"] for r in data["evolution"]]
    assert indices == [0, 1]  # oldest -> newest
    assert data["evolution"][-1]["is_latest"] is True
    assert data["evolution"][0]["is_latest"] is False
    assert data["evolution"][0]["display_title"] == "Exploratory round"
    assert data["evolution"][0]["direction"] == "Exploratory round"
    # Round 1 is directed by round_0's selected hypothesis (cross-round convention).
    assert data["evolution"][1]["display_title"] == (
        "Higher state-level rent burden is associated with a greater share of young adults "
        "living with their parents rather than forming independent households."
    )


def test_evolution_round0_never_reads_a_later_selection(monkeypatch, tmp_path):
    # selected_hypothesis.md in round_0 directs Round 1; it must never replace
    # Round 0's exploratory timeline label.
    _base_tree(tmp_path)
    r0 = _round_dir(tmp_path, 0)
    r0.joinpath("selected_hypothesis.md").write_text(SELECTED_HYP, encoding="utf-8")
    r0.joinpath("selected_hypothesis_20260101_000000_user.md").write_text(SELECTED_HYP_2, encoding="utf-8")
    session_dir = r0 / "interactive_hyp_gen"
    session_dir.mkdir()
    session_dir.joinpath("session_20260101_000000.json").write_text("{}", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_on_round0_recovery_files(path, *args, **kwargs):
        if path.name.startswith("selected_hypothesis_"):
            raise AssertionError("Round 0 evolution must not read selected-hypothesis archives")
        if "interactive_hyp_gen" in path.parts:
            raise AssertionError("Round 0 evolution must not read interactive generation sessions")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_on_round0_recovery_files)
    assert app_module._v2_evolution_direction(r0, 0) == "Exploratory round"


def test_evolution_round0_is_always_exploratory(tmp_path):
    r0 = _round_dir(tmp_path, 0)
    assert app_module._v2_evolution_direction(r0, 0) == "Exploratory round"


def test_evolution_round1_plus_uses_previous_round_selected_hypothesis(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0, selected=SELECTED_HYP, feedback="reviewed")
    _complete_round(tmp_path, 1, selected=SELECTED_HYP_2, feedback="reviewed")
    _complete_round(tmp_path, 2, selected=SELECTED_HYP_3, feedback="reviewed")
    _complete_round(tmp_path, 3, feedback="reviewed")
    ev = {e["round_index"]: e["display_title"] for e in
          _get_state(_client_with_session(monkeypatch, tmp_path))["evolution"]}
    assert ev[0] == "Exploratory round"
    assert ev[1] == (
        "Higher state-level rent burden is associated with a greater share of young adults "
        "living with their parents rather than forming independent households."
    )
    assert ev[2] == "Regional cost-of-living gaps predict interstate migration among young workers."
    assert ev[3] == "Longer commute times compound affordability pressure for renters in high-cost metropolitan areas."
    for value in ev.values():
        assert "Title" not in value
        assert "Justification" not in value
        assert "Plan" not in value


# --- (11) recent rounds ------------------------------------------------------

def test_recent_rounds_latest_three_ordering(monkeypatch, tmp_path):
    cases = (
        (1, [0]),
        (2, [1, 0]),
        (3, [2, 1, 0]),
        (4, [3, 2, 1]),
        (5, [4, 3, 2]),
    )
    for completed_count, expected in cases:
        root = tmp_path / f"case_{completed_count}"
        _base_tree(root)
        for i in range(completed_count):
            _complete_round(root, i, selected=SELECTED_HYP, feedback="reviewed")
        data = _get_state(_client_with_session(monkeypatch, root))
        recent = data["recent_rounds"]
        assert [r["round_index"] for r in recent] == expected
        assert len(recent) <= 3
        assert all(r["status"] == "complete" for r in recent)
        assert all("display_title" not in r for r in recent)
        assert [r["destination"] for r in recent] == [
            f"/rounds/?project=housing-affordability-study&round={index}&review=1"
            for index in expected
        ]


def test_recent_rounds_latest_three_with_outcomes(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    for i in range(4):  # round_0..round_3 all completed
        _complete_round(tmp_path, i, selected=SELECTED_HYP, feedback="reviewed")
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    recent = data["recent_rounds"]
    assert [r["round_index"] for r in recent] == [3, 2, 1]  # latest three, newest first
    assert "rent burden" in recent[0]["outcome_preview"]  # findings-section prose
    assert recent[0]["outcome_preview"].endswith("...")
    # one concise finding: a single paragraph, bounded length, no report dump
    assert "\n\n" not in recent[0]["outcome_preview"]
    assert len(recent[0]["outcome_preview"]) <= 343


def test_recent_rounds_round0_uses_standard_finding_extraction(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    report = (
        "## Round Report\n\n## Principal Findings\n\n"
        "Round 0 identified a baseline affordability signal that should seed later hypothesis generation. "
        "Additional artifact-derived detail follows in the same section.\n"
    )
    _complete_round(tmp_path, 0, selected=SELECTED_HYP, feedback="reviewed", report_text=report)
    recent = _get_state(_client_with_session(monkeypatch, tmp_path))["recent_rounds"]
    assert [r["round_index"] for r in recent] == [0]
    assert recent[0]["outcome_preview"].startswith("Round 0 identified a baseline affordability signal")
    assert recent[0]["outcome_preview"].endswith("...")


def test_recent_round_outcome_null_when_no_prose(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    thin_report = "# Round 0 Report\n\n## Round Report\n\n# Heading Only\n\n- a list item\n- another item\n"
    _complete_round(tmp_path, 0, selected=SELECTED_HYP, report_text=thin_report)
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["recent_rounds"][0]["outcome_preview"] is None


def test_latest_finding_display_strips_emphasis_and_preserves_words(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    report = (
        "## Round Report\n\n## Principal Findings\n\n"
        "This analysis identified a **Regenerative Completion Gap** and an _ortholog sustainment_ signal.\n"
    )
    round_dir = _complete_round(tmp_path, 0, selected=SELECTED_HYP, feedback="reviewed", report_text=report)
    raw_report = (round_dir / "round_report.md").read_text(encoding="utf-8")
    raw_finding = app_module._v2_round_finding(raw_report)
    recent = _get_state(_client_with_session(monkeypatch, tmp_path))["recent_rounds"]
    preview = recent[0]["outcome_preview"]
    assert "**Regenerative Completion Gap**" in raw_finding  # full/source extraction untouched
    assert "_ortholog sustainment_" in raw_finding
    assert preview == (
        "This analysis identified a Regenerative Completion Gap and an "
        "ortholog sustainment signal..."
    )
    assert "**" not in preview and "__" not in preview
    assert "_" not in preview and "*" not in preview
    assert (round_dir / "round_report.md").read_text(encoding="utf-8") == raw_report


def test_latest_finding_display_strips_all_inline_emphasis_forms():
    preview = app_module._v2_latest_finding_display_preview(
        "A **bold** and __strong__ result with *italic* and _emphasized_ wording."
    )
    assert preview == "A bold and strong result with italic and emphasized wording..."


def test_latest_finding_display_normalizes_final_ellipsis():
    assert app_module._v2_latest_finding_display_preview("The result was strongly supported.") == (
        "The result was strongly supported..."
    )
    assert app_module._v2_latest_finding_display_preview("Four genes constitute this class:") == (
        "Four genes constitute this class..."
    )
    assert app_module._v2_latest_finding_display_preview("The result was strongly supported...") == (
        "The result was strongly supported..."
    )
    assert app_module._v2_latest_finding_display_preview("The result was strongly supported......") == (
        "The result was strongly supported..."
    )
    assert app_module._v2_latest_finding_display_preview("The result was strongly supported.\u2026") == (
        "The result was strongly supported..."
    )
    assert app_module._v2_latest_finding_display_preview(None) is None


def test_latest_finding_display_preserves_scientific_identifiers_and_notation():
    text = (
        "The 43_AlphaONS subtype retained gene_name and alpha_beta_gamma markers "
        "with 238× specificity, p=0.0016, p<=0.05, r>0.7, and q<0.1."
    )
    assert app_module._v2_latest_finding_display_preview(text) == (
        "The 43_AlphaONS subtype retained gene_name and alpha_beta_gamma markers "
        "with 238× specificity, p=0.0016, p<=0.05, r>0.7, and q<0.1..."
    )


def test_round_outcome_prefers_findings_section():
    outcome = app_module._v2_round_outcome_preview(ROUND_REPORT)
    assert outcome is not None
    assert "rent burden" in outcome
    assert "background paragraph" not in outcome


def test_finding_recognizes_summary_of_principal_findings():
    report = (
        "## Round Report\n\n## Summary of Principal Findings\n\n"
        "This investigation identified and validated a class of \"poised-but-unfired\" chromatin loci in injured mouse RGCs. "
        "The same paragraph continues with additional artifact-derived detail.\n"
    )
    out = app_module._v2_round_finding(report)
    assert out is not None
    assert out.startswith(
        "This investigation identified and validated a class of \"poised-but-unfired\" chromatin loci"
    )


def test_finding_recognizes_principal_findings():
    report = (
        "## Round Report\n\n## Principal Findings\n\n"
        "This analysis identified a Regenerative Completion Gap between zebrafish regeneration genes and mouse ONC responses. "
        "The paragraph continues with mechanistic detail from the report artifact.\n"
    )
    out = app_module._v2_round_finding(report)
    assert out is not None
    assert out.startswith("This analysis identified a Regenerative Completion Gap")


def test_finding_recognizes_summary_of_key_findings():
    out = app_module._v2_round_finding(ROUND_REPORT)
    assert out is not None
    assert out.startswith("States with the highest rent burden")


def test_newly_completed_round_summary_of_findings_displays_in_latest_findings(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    report = (
        "## Round Report\n\n# Discussion\n\n## Summary of Findings\n\n"
        "The core hypothesis is supported: regenerative alignment is associated with a "
        "distributed ribosomal stoichiometry program across retinal ganglion cell subtypes.\n"
    )
    _complete_round(tmp_path, 0, feedback="reviewed", report_text=report)

    recent = _get_state(_client_with_session(monkeypatch, tmp_path))["recent_rounds"]

    assert recent[0]["outcome_preview"].startswith("The core hypothesis is supported")
    assert recent[0]["outcome_preview"].endswith("...")


def test_newly_completed_exploratory_round_key_findings_variant_displays(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    report = (
        "## Round Report\n\n## Discussion\n\n### Key Findings and Scientific Value\n\n"
        "This exploratory analysis revealed a cross-regional pathology gradient associated "
        "with distinct inflammatory and neuronal transcriptional programs.\n"
    )
    _complete_round(tmp_path, 0, feedback="reviewed", report_text=report)

    recent = _get_state(_client_with_session(monkeypatch, tmp_path))["recent_rounds"]

    assert recent[0]["outcome_preview"].startswith("This exploratory analysis revealed")
    assert recent[0]["outcome_preview"].endswith("...")


def test_finding_accepts_previously_unseen_major_findings_heading():
    report = (
        "## Round Report\n\n## Major Findings\n\n"
        "A previously unseen findings heading still identifies a substantive scientific result "
        "without requiring report-generation changes or an additional model call.\n\n"
        "## Limitations\n\n"
        "This unrelated limitations paragraph must not become the displayed finding.\n"
    )

    out = app_module._v2_round_finding(report)

    assert out is not None
    assert out.startswith("A previously unseen findings heading")
    assert "limitations" not in out.lower()


def test_finding_heading_normalization_skips_background_prior_work():
    report = (
        "## Round Report\n\n## Background and Literature Review\n\n"
        "### Published Prior Findings\n\n"
        "Prior studies reported a background association that is not a result of this round.\n\n"
        "## Discussion\n\n### Key Findings and Scientific Value\n\n"
        "This round discovered a new mechanistic association supported by its own analysis.\n"
    )

    out = app_module._v2_round_finding(report)

    assert out == "This round discovered a new mechanistic association supported by its own analysis."


def test_explicit_central_finding_still_wins_over_other_sections():
    report = (
        "## Round Report\n\n## Summary of Principal Findings\n\n"
        "This investigation identified and validated a class of candidate loci in the report body.\n\n"
        "## Results\n\n"
        "The central finding of this investigation is that rent burden strongly predicts household formation across the fifty states studied.\n"
    )
    out = app_module._v2_round_finding(report)
    assert out == (
        "The central finding of this investigation is that rent burden strongly predicts household formation across the fifty states studied."
    )


def test_background_only_report_still_returns_null():
    report = (
        "## Round Report\n\n## Overview\n\n"
        "Optic nerve regeneration failure is a central unresolved problem in neuroscience that has motivated decades of study across labs.\n"
    )
    assert app_module._v2_round_outcome_preview(report) is None
    assert app_module._v2_round_finding(report) is None


def test_no_whole_report_fallback_when_only_general_sections_exist():
    report = (
        "## Round Report\n\n## Overview\n\n"
        "A substantive background paragraph that is long enough to pass the prose filter and would be incorrectly used by a naive whole-report fallback.\n\n"
        "## Discussion\n\n"
        "Another substantive paragraph that still should not count because no recognized finding-bearing heading exists.\n"
    )
    assert app_module._v2_round_outcome_preview(report) is None
    assert app_module._v2_round_finding(report) is None


# --- (12) current routes remain while obsolete V1 entrypoints stay retired ---

def test_current_routes_registered_and_obsolete_v1_endpoints_absent():
    endpoints = {rule.endpoint for rule in app_module.app.url_map.iter_rules()}
    for name in (
        "home", "api_session_info", "api_readme", "api_round_feedback_get",
        "project_home", "api_project_state",
    ):
        assert name in endpoints
    for name in (
        "app_interface", "round_monitor", "unified_report", "background_report",
        "checkpoint_browser", "activity_log", "thank_you", "website_guide",
    ):
        assert name not in endpoints


def test_root_redirects_to_projects_screen():
    client = app_module.app.test_client()
    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/projects"
    projects = client.get(response.headers["Location"])
    assert projects.status_code == 200
    assert b'id="projects-root"' in projects.data


def test_project_state_available_without_auth():
    resp = app_module.app.test_client().get("/api/project_state")
    assert resp.status_code == 200


def test_project_home_uses_scoped_wide_page_class(monkeypatch, tmp_path):
    """Project Home carries the .v2-page-home widening class; Projects does not,
    so the wider column and centered identity are scoped to Project Home only."""
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    # /home only reaches Project Home (now at /projects/<slug>) once a V2
    # project is active; create one the same way real navigation would.
    client.post(
        "/api/projects",
        data={
            "project_name": "Scoped Page Class Project",
            "task_description": "Identify testable regeneration hypotheses.",
            "dataset_context": "ctx",
        },
        content_type="multipart/form-data",
    )
    home = client.get("/home", follow_redirects=True).get_data(as_text=True)
    projects = client.get("/projects").get_data(as_text=True)
    assert 'class="v2 v2-page-home"' in home
    assert "v2-page-home" not in projects


def test_newer_selected_hypothesis_overrides_older_review(monkeypatch, tmp_path):
    """Precedence: a newer selected hypothesis (round not started) wins over an
    older completed round still awaiting review."""
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0, selected=SELECTED_HYP)  # completed, NOT reviewed
    r1 = _round_dir(tmp_path, 1)
    _write_config(r1, "Explore ACS housing data")
    r1.joinpath("selected_hypothesis.md").write_text(SELECTED_HYP_2, encoding="utf-8")
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    assert data["attention"]["state"] == "hypothesis_selected"
    assert data["attention"]["cta_label"] == "Start Round 1"


def test_project_home_js_removed_direction_section():
    """The permanent Current Research Direction section is gone from rendering."""
    js = (Path(app_module.__file__).resolve().parent / "static" / "project_home.js").read_text()
    assert "buildDirection" not in js
    assert "state.current_direction" not in js


def test_project_home_js_has_no_project_actions_menu():
    """Project Home should not render the Projects-page three-dot action menu."""
    js = (Path(app_module.__file__).resolve().parent / "static" / "project_home.js").read_text()
    assert "v2-home-actions" not in js
    assert "Project actions" not in js
    assert "deleteProject" not in js
    assert "buildPreResearch" not in js
    assert "buildResearchStatus(state.research_status || {})" in js


def test_stopped_status_pill_uses_danger_tone_and_running_reuses_activity_pulse():
    static = Path(app_module.app.static_folder)
    js = (static / "project_home.js").read_text()
    css = (static / "style.css").read_text()

    assert "status.state === 'research_stopped' ? ' v2-pill-danger' : ''" in js
    assert "status.state === 'research_in_progress' ? ' v2-pill-active' : ''" in js
    assert ".v2-pill-danger {" in css
    assert "color: #a24949;" in css[css.index(".v2-pill-danger {"):]
    assert "background: #f6e9e7;" in css[css.index(".v2-pill-danger {"):]
    assert "background: currentColor;" in css[css.index(".v2-pill-dot {"):]
    assert ".v2-pill-active .v2-pill-dot," in css
    assert ".rw-activity-state-icon.active," in css
    assert "@media (prefers-reduced-motion: reduce)" in css


# --- real cross-round lifecycle: completed round + next-round selection -------

def test_completed_round_then_next_selection_shows_next_round(monkeypatch, tmp_path):
    """round_3 completed; the next hypothesis is selected INTO round_3 (newer than
    round_report.md) and round_4 does not exist. Project Home must show the round
    that runs next (Round 4), not the storage directory's index (Round 3)."""
    import os
    _base_tree(tmp_path)
    r3 = _complete_round(tmp_path, 3)  # completed, no selection yet, NOT reviewed
    sel = r3 / "selected_hypothesis.md"
    sel.write_text(SELECTED_HYP_2, encoding="utf-8")
    report_mtime = (r3 / "round_report.md").stat().st_mtime
    os.utime(sel, (report_mtime + 30, report_mtime + 30))  # selected AFTER the report
    data = _get_state(_client_with_session(monkeypatch, tmp_path))

    assert data["mode"] == "research_briefing"
    assert "current_direction" not in data
    attn = data["attention"]
    # newer selection overrides the older completed-round review state
    assert attn["state"] == "hypothesis_selected"
    assert attn["status_pill"] == "Ready for investigation"
    assert attn["headline"] == "Your hypothesis is ready for investigation."
    assert attn["cta_label"] == "Start Round 4"
    assert attn["body_lead"] is None
    assert attn["body"] == (
        "A hypothesis has been selected for Round 4. Start the investigation "
        "when you are ready."
    )


def test_completed_round_own_launcher_not_treated_as_next_selection(monkeypatch, tmp_path):
    """A selected_hypothesis.md OLDER than round_report.md is that round's own
    launcher, not a fresh next-round selection -> stays in the review state."""
    import os
    _base_tree(tmp_path)
    r3 = _complete_round(tmp_path, 3, selected=SELECTED_HYP_2)  # no feedback -> review pending
    report_mtime = (r3 / "round_report.md").stat().st_mtime
    os.utime(r3 / "selected_hypothesis.md", (report_mtime - 120, report_mtime - 120))
    data = _get_state(_client_with_session(monkeypatch, tmp_path))
    attn = data["attention"]
    assert attn["state"] == "round_ready_for_review"
    assert attn["cta_label"] == "Review Round 3"
    assert attn["cta_destination"] == "/rounds/?project=housing-affordability-study&round=3&review=1"


def test_durable_chronology_keeps_precompletion_selection_historical(
    monkeypatch, tmp_path,
):
    _base_tree(tmp_path)
    r0 = _complete_round(tmp_path, 0, selected=SELECTED_HYP_2)
    r0.joinpath("generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": [SELECTED_HYP_2]}), encoding="utf-8",
    )
    r0.joinpath("hypothesis_studio_history.json").write_text(json.dumps({
        "version": 1,
        "events": [
            {
                "id": "historical-selection", "sequence": 1,
                "timestamp": "2026-01-15T10:03:00+00:00",
                "type": "hypothesis_selected",
                "hypothesis": {"index": 1, "full_text": SELECTED_HYP_2},
            },
        ],
    }), encoding="utf-8")
    r0.joinpath("round_activity.jsonl").write_text(json.dumps({
        "timestamp": "2026-01-15T10:08:00+00:00",
        "event_type": "round_report_saved",
    }) + "\n", encoding="utf-8")

    status = _status(_get_state(_client_with_session(monkeypatch, tmp_path)))
    assert status["state"] == "hypothesis_development"
    assert status["round"] == 1
    assert status["cta_label"] == "Open Hypothesis Studio"
    assert status["cta_action"] is None


def test_ready_selection_with_missing_continuation_config_has_no_start_action(
    monkeypatch, tmp_path,
):
    import os

    _base_tree(tmp_path)
    r0 = _complete_round(tmp_path, 0)
    r0.joinpath("generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": [SELECTED_HYP_2]}), encoding="utf-8",
    )
    selection = r0 / "selected_hypothesis.md"
    selection.write_text(SELECTED_HYP_2, encoding="utf-8")
    report_mtime = (r0 / "round_report.md").stat().st_mtime
    os.utime(selection, (report_mtime + 30, report_mtime + 30))
    r0.joinpath("config.json").unlink()

    status = _status(_get_state(_client_with_session(monkeypatch, tmp_path)))
    assert status["state"] == "hypothesis_start_blocked"
    assert status["status_pill"] == "Setup required"
    assert status["round"] == 1
    assert status["cta_action"] is None
    assert status["cta_label"] == "Inspect Round 0"
    assert "config.json" in status["blocking_reason"]


# --- How the Research Evolved <> Latest Findings distinction ------------------

def test_latest_findings_rename_and_no_repeated_title_in_js():
    js = (Path(app_module.__file__).resolve().parent / "static" / "project_home.js").read_text()
    assert "label('Latest Findings')" in js
    assert "label('Recent Rounds')" not in js
    assert "v2-recent-title" not in js  # repeated hypothesis/direction title removed
    assert "label('How the Research Evolved')" in js  # timeline kept
    assert "'Round ' + esc(round.round_index)" in js  # round labels unchanged
    assert "renderPreviewMarkdown(round.outcome_preview)" not in js
    assert "esc(round.outcome_preview)" in js  # finding body is plain text
    render = js[js.index("function render(state)"):]
    assert render.index("buildRecentRounds(state.recent_rounds)") < render.index(
        "buildEvolution(state.evolution)"
    )


def test_round_finding_is_concise_verbatim():
    # a report whose findings paragraph is one long multi-sentence block
    report = (
        "## Round Report\n\n## Key Findings\n\n"
        "Mouse RGC subtype resilience after injury is significantly associated with higher "
        "baseline expression of zebrafish regeneration orthologs. "
        "This relationship held across all subtypes examined and multiple injury timepoints. "
        + "Additional caveats and extended discussion continue at length here and beyond. " * 6
        + "\n"
    )
    out = app_module._v2_round_finding(report)
    assert out is not None
    assert out in " ".join(report.split())     # verbatim substring of the artifact
    assert out.startswith("Mouse RGC subtype resilience")
    assert out.rstrip().endswith(".")          # never cut mid-sentence
    assert "\n" not in out and len(out) <= 340  # concise, single claim


def test_first_sentences_keeps_first_sentence_whole():
    # first sentence alone exceeds the cap -> still returned whole (no mid-cut)
    long_first = "Y" * 200 + " done."
    assert app_module._v2_first_sentences(long_first, max_chars=100) == long_first
    # otherwise stops at a sentence boundary within the budget
    assert app_module._v2_first_sentences("Short one. " + "X" * 400 + " tail.", max_chars=100) == "Short one."


def test_first_sentence_is_verbatim_no_truncation():
    # full first sentence preserved (no char cut / ellipsis); clamping is visual
    text = ("In injured mouse retinal ganglion cells a resilient subtype retains "
            "regeneration-permissive transcriptional programs after optic nerve crush. Second sentence.")
    out = app_module._v2_first_sentence(text)
    assert out == ("In injured mouse retinal ganglion cells a resilient subtype retains "
                   "regeneration-permissive transcriptional programs after optic nerve crush.")
    assert "…" not in out and "Second sentence" not in out


def test_finding_omits_background_no_whole_report_fallback():
    # a report with only a background/overview paragraph yields NO finding
    report = ("## Round Report\n\n## Overview\n\nOptic nerve regeneration failure is a central "
              "unresolved problem in neuroscience that has motivated decades of study across labs.\n")
    assert app_module._v2_round_outcome_preview(report) is None
    assert app_module._v2_round_finding(report) is None


def test_finding_uses_priority_finding_section_only():
    report = ("## Round Report\n\n## Background\n\nBackground context paragraph that is substantive "
              "and long enough to otherwise be picked up by a naive first-paragraph fallback here.\n\n"
              "## Conclusion\n\nResilient RGC subtypes retained regeneration-permissive programs after "
              "crush, a finding that survived across all timepoints examined in this analysis.\n")
    out = app_module._v2_round_outcome_preview(report)
    assert out is not None
    assert out.startswith("Resilient RGC subtypes")     # Conclusion section, not Background
    assert "Background context" not in out


# --- initial-exploration lifecycle gate (no round has ever started) ----------

def _rounds_root(root: Path) -> Path:
    return root / "project" / "checkpoint" / "checkpoints"


def test_no_round_has_started_predicate(tmp_path):
    # Pristine project (config + README only) -> no round has started.
    _base_tree(tmp_path)
    assert app_module._v2_no_round_has_started(_rounds_root(tmp_path)) is True
    # Any research activity flips it: a running round (query dir).
    (_round_dir(tmp_path, 0) / "query_000").mkdir()
    assert app_module._v2_no_round_has_started(_rounds_root(tmp_path)) is False


def test_no_round_has_started_predicate_counts_precheckpoint_activity(tmp_path):
    _base_tree(tmp_path)
    round_zero = _round_dir(tmp_path, 0)
    round_zero.joinpath("round_activity.jsonl").write_text(
        json.dumps({"event_type": "round_started"}) + "\n", encoding="utf-8",
    )

    assert app_module._v2_no_round_has_started(_rounds_root(tmp_path)) is False


def test_no_round_has_started_predicate_counts_interrupted_runner_state(tmp_path):
    _base_tree(tmp_path)
    state_file = _rounds_root(tmp_path).parent / "run_state.json"
    state_file.write_text(json.dumps({"round": 0, "status": "interrupted"}), encoding="utf-8")

    assert app_module._v2_no_round_has_started(_rounds_root(tmp_path)) is False


def test_first_round_studio_gate_covers_running_and_interrupted_states(tmp_path):
    _base_tree(tmp_path)
    state_file = _rounds_root(tmp_path).parent / "run_state.json"

    for status in ("starting", "running", "interrupted", "failed"):
        state_file.write_text(
            json.dumps({"round": 0, "status": status}), encoding="utf-8"
        )
        assert app_module._v2_first_round_gate_state(
            _rounds_root(tmp_path)
        ) == "incomplete"

    _round_dir(tmp_path, 0).joinpath("round_report.md").write_text(
        "# Round 0 complete", encoding="utf-8"
    )
    assert app_module._v2_first_round_gate_state(_rounds_root(tmp_path)) is None


def test_no_round_has_started_predicate_ignores_deleted_runner_state(tmp_path):
    _base_tree(tmp_path)
    state_file = _rounds_root(tmp_path).parent / "run_state.json"
    state_file.write_text(json.dumps({"round": 0, "status": "not_started"}), encoding="utf-8")

    assert app_module._v2_no_round_has_started(_rounds_root(tmp_path)) is True


def test_no_round_started_predicate_hypothesis_artifacts_count(tmp_path):
    _base_tree(tmp_path)
    # A generated set (hypothesis work) also exits the initial gate.
    _round_dir(tmp_path, 0).joinpath("generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": ["H"]}), encoding="utf-8")
    assert app_module._v2_no_round_has_started(_rounds_root(tmp_path)) is False


def test_no_round_started_after_round0_completes(tmp_path):
    _base_tree(tmp_path)
    _complete_round(tmp_path, 0, feedback="reviewed")  # report + memory
    assert app_module._v2_no_round_has_started(_rounds_root(tmp_path)) is False


def test_round_status_reports_has_ever_started(monkeypatch, tmp_path):
    _base_tree(tmp_path)
    client = _client_with_session(monkeypatch, tmp_path)
    # Brand-new project: no round has ever started.
    data = client.get("/api/round_status").get_json()
    assert data["success"] is True
    assert data["has_ever_started"] is False
    # Round 0 starts (a query dir appears) -> has_ever_started True.
    (_round_dir(tmp_path, 0) / "query_000").mkdir()
    data = client.get("/api/round_status").get_json()
    assert data["has_ever_started"] is True
