"""Focused tests for the Current Research Understanding preview affordance
(soft fade + bounded height + restrained text action) on Project Home.

The preview's rendering lives in static/project_home.js, which is not exercised
by the Python test client (no JS runtime here), so the copy/markup contract is
verified by reading the shipped static asset directly alongside the existing
backend product-state tests that already cover the deterministic preview
extraction and destination logic (test_project_state.py). No live LLM, no S3.
"""
from pathlib import Path

from da_selection_ui import app as app_module

_JS_PATH = Path(app_module.app.static_folder) / "project_home.js"
_CSS_PATH = Path(app_module.app.static_folder) / "style.css"
_APP_PATH = Path(app_module.__file__)


def _js():
    return _JS_PATH.read_text(encoding="utf-8")


def _css():
    return _CSS_PATH.read_text(encoding="utf-8")


def test_project_home_omits_every_data_presentation_and_payload():
    js = _js()
    css = _css()
    app_source = _APP_PATH.read_text(encoding="utf-8")
    assert "buildInputData" not in js
    assert "state.input_data" not in js
    assert "Input data" not in js
    assert "Entire source" not in js
    assert "v2-input-data" not in js
    assert ".v2-input-data" not in css
    assert "buildDataFolder" not in js
    assert "Data Folder" not in js
    assert "Open in Finder" not in js
    assert "Copy path" not in js
    assert "v2-data-folder-section" not in js
    assert ".v2-data-folder-section" not in css
    state_builder = app_source[
        app_source.index("def _v2_build_project_state"):
        app_source.index("def _v2_active_round_dir_for")
    ]
    assert "data_folder" not in state_builder
    assert "input_data" not in state_builder
    assert "_v2_data_sources_payload" not in state_builder


def test_project_home_state_omits_selected_data_for_managed_and_legacy_projects(monkeypatch):
    projects = [
        {
            "id": "prj_managed",
            "managed": True,
            "data_sources": [{
                "dataset_id": "ds_managed",
                "selected_paths": ["."],
                "excluded_paths": [],
            }],
        },
        {
            "id": "legacy-project",
            "managed": False,
            "data_sources": [{
                "dataset_id": "ds_legacy",
                "selected_paths": ["nested/file.csv"],
                "excluded_paths": [],
            }],
        },
    ]
    monkeypatch.setattr(
        app_module,
        "_v2_project_state_for",
        lambda *_args: {
            "mode": "research_briefing",
            "project": {"title": "Project"},
            "research_status": {"state": "review_ready"},
            "evolution": [{"round_index": 1, "display_title": "Unchanged"}],
        },
    )

    for project in projects:
        monkeypatch.setattr(app_module, "_v2_get_project", lambda _project_id, value=project: value)
        with app_module.app.test_request_context("/api/project_state"):
            from flask import session

            session["v2_active_project_id"] = project["id"]
            session["v2_active_project_slug"] = project["id"]
            session["project_label"] = "Project"
            session["project_config"] = {"checkpoint_dir": "/tmp/unused"}
            state = app_module._v2_build_project_state()

        assert "input_data" not in state
        assert state["evolution"] == [{"round_index": 1, "display_title": "Unchanged"}]


# --- (1,2) action button copy ------------------------------------------------

def test_new_action_text_present_exactly():
    js = _js()
    assert "Explore Full Research Understanding" in js


def test_old_action_text_absent():
    js = _js()
    assert "Explore Research Understanding" not in js


def test_secondary_button_classes_removed():
    js = _js()
    # This action is no longer a button: it must not carry button surface
    # classes anywhere in project_home.js (the only place that used them for
    # this action).
    assert "v2-btn-secondary" not in js
    assert "v2-btn v2-btn-secondary" not in js


def test_text_action_styling_present():
    js = _js()
    assert "'v2-understanding-link'" in js
    css = _css()
    assert ".v2-understanding-link {" in css
    # The semantic button explicitly resets its native surface and keeps the
    # same restrained text-action appearance.
    import re
    blocks = re.findall(r"\.v2-understanding-link[^{]*\{([^}]*)\}", css)
    combined = "\n".join(blocks)
    assert "border: 0" in combined
    assert "border-color" not in combined
    assert "background: none" in combined
    assert "box-shadow" not in combined
    # Focus-visible must still be handled for keyboard users.
    assert ".v2-understanding-link:focus-visible" in css


def test_review_round_remains_visually_dominant():
    js = _js()
    # The primary CTA (Review Round N -> etc.) still renders via ctaButton's
    # solid v2-btn-primary; the understanding action is built with its own
    # standalone class and carries no v2-btn button-surface class at all, so
    # it can no longer visually compete with the primary CTA.
    assert "v2-btn v2-btn-primary" in js
    assert "el('button', 'v2-understanding-link'," in js


# --- (3) destination unchanged ------------------------------------------------

def test_complete_memory_is_available_inline_without_legacy_destination(monkeypatch, tmp_path):
    import json

    def _round_dir(root, index):
        d = root / "project" / "checkpoint" / "checkpoints" / f"round_{index}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    rd = _round_dir(tmp_path, 0)
    rd.joinpath("config.json").write_text(json.dumps({"config": {"task_description": "Goal."}}), encoding="utf-8")
    rd.joinpath("round_report.md").write_text("# R\n\n## Round Report\n\nDone.", encoding="utf-8")
    rd.joinpath("memory_report.md").write_text(
        "# Mem\n\n## Accumulated Report\n\n"
        "This is the first substantive prose paragraph describing the study in enough detail.\n\n"
        "This is the second substantive prose paragraph continuing the account further.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
        s["checkpoint_s3_prefix"] = "project"
        s["project_config"] = app_module._derive_project_config("project")
        s["project_label"] = "Preview Destination Project"

    data = client.get("/api/project_state").get_json()
    ru = data["research_understanding"]
    assert ru["preview_markdown"]
    assert "first substantive prose paragraph" in ru["full_markdown"]
    assert "second substantive prose paragraph" in ru["full_markdown"]
    assert "destination" not in ru


# --- (4) source/extraction logic unchanged ------------------------------------

def test_understanding_preview_extraction_untouched():
    # Same deterministic extractor as before; this change never touches it.
    from tests.test_project_state import MEMORY_REPORT
    preview = app_module._v2_understanding_preview(MEMORY_REPORT)
    assert preview is not None
    assert "housing affordability varies across the fifty states" in preview


# --- fade/frame markup + CSS (non-brittle: presence only, not pixel values) --

def test_preview_uses_bounded_fade_frame():
    js = _js()
    assert "'v2-understanding-frame is-collapsed'" in js
    css = _css()
    assert ".v2-understanding-frame" in css
    assert ".v2-understanding-frame.is-collapsed" in css
    assert "overflow: hidden" in css
    assert "linear-gradient(to bottom, transparent, var(--v2-bg))" in css


def test_action_expands_and_collapses_inline():
    js = _js()
    assert "el('button', 'v2-understanding-link'" in js
    assert "el('div', 'v2-understanding-toggle-dock')" in js
    assert "aria-expanded" in js
    assert "Collapse Full Research Understanding" in js
    assert "frame.classList.toggle('is-collapsed'" in js
    assert "toggleDock.classList.toggle('is-sticky', !expanded)" in js
    assert "section.insertBefore(toggleDock, frame)" in js
    assert "link.href = understanding.destination" not in js


def test_expanded_understanding_collapse_action_sticks_to_viewport_bottom():
    css = _css()
    assert ".v2-understanding-toggle-dock.is-sticky" in css
    assert "position: sticky" in css
    assert "top: calc(100dvh - 48px)" in css


def test_section_label_unchanged():
    js = _js()
    assert "Current Research Understanding" in js


# --- identity header: RESEARCH PROJECT eyebrow removed, title stays ----------

def test_research_project_eyebrow_removed():
    js = _js()
    assert "RESEARCH PROJECT" not in js
    assert "v2-eyebrow" not in js
    css = _css()
    assert ".v2-eyebrow" not in css


def test_title_still_rendered_and_centered_via_page_home_scope():
    js = _js()
    assert "v2-title" in js
    css = _css()
    assert ".v2-page-home .v2-identity { text-align: center; }" in css
