"""Regression coverage for containment-safe Audit Agent project-code figures."""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from da_selection_ui import app as app_module


FIGURE_REFERENCE = "figures/fig9b_region_auc_comparison.png"
FIGURE_BYTES = b"region-auc-composite"


def _clear_workspace_caches():
    with app_module._ROUND_WORKSPACE_CACHE_LOCK:
        app_module._ROUND_WORKSPACE_ARTIFACT_CACHE.clear()
        app_module._ROUND_WORKSPACE_TREE_CACHE.clear()
        app_module._ROUND_WORKSPACE_ROUND_ENTRY_CACHE.clear()


def _project(tmp_path, project_id, slug, layout, *, relative_code=False):
    root = tmp_path / layout
    code = root / "code"
    figure = code / FIGURE_REFERENCE
    round_dir = root / "checkpoint" / "checkpoints" / "round_0"
    figure.parent.mkdir(parents=True)
    round_dir.mkdir(parents=True)
    figure.write_bytes(FIGURE_BYTES + project_id.encode())
    record = {
        "id": project_id,
        "url_slug": slug,
        "project_root": str(root),
        "code_dir": "code" if relative_code else str(code),
        "checkpoint_dir": str(round_dir),
    }
    return record, code, figure


def _activate(monkeypatch, state):
    monkeypatch.setattr(app_module, "_active_project_record", lambda: state["record"])
    monkeypatch.setattr(
        app_module,
        "_round_workspace_checkpoint_dir_for_session",
        lambda: Path(state["record"]["checkpoint_dir"]),
    )
    monkeypatch.setattr(
        app_module,
        "_managed_interaction_project",
        lambda project_id=None: (
            state["record"]
            if str(project_id or "") == str(state["record"]["id"])
            else None
        ),
    )
    _clear_workspace_caches()


def _session(client, record):
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["v2_active_project_id"] = record["id"]
        flask_session["v2_active_project_slug"] = record["url_slug"]


@pytest.mark.parametrize(
    ("layout", "relative_code"),
    [
        ("application-data/projects/managed", False),
        ("custom/readable-project", False),
        ("legacy/discovery-agent-project", True),
    ],
)
def test_audit_relative_figures_use_registered_code_root_across_project_layouts(
    monkeypatch, tmp_path, layout, relative_code
):
    record, _code, figure = _project(
        tmp_path, "prj_layout", "layout-project", layout, relative_code=relative_code
    )
    state = {"record": record}
    _activate(monkeypatch, state)

    with app_module.app.test_request_context("/"):
        display, figures = app_module._prepare_audit_chat_figures(
            f"![Region AUC]({FIGURE_REFERENCE})"
        )

    assert display == "![Region AUC](rw-report-figure:rw_fig_0)"
    assert figures == [{
        "token": "rw_fig_0",
        "alt": "Region AUC",
        "available": True,
        "url": (
            "/api/round_workspace_code_artifact"
            "?path=figures/fig9b_region_auc_comparison.png"
        ),
        "artifact": {
            "round": None,
            "sourceKind": "project-code",
            "source": "",
            "artifactPath": FIGURE_REFERENCE,
        },
    }]
    assert app_module._round_workspace_project_code_figure(
        FIGURE_REFERENCE
    )["target"] == figure.resolve()


def test_audit_new_and_restored_responses_revalidate_exact_reference_without_rewrites(
    monkeypatch, tmp_path
):
    record, _code, figure = _project(
        tmp_path, "prj_regionauc", "region-auc", "projects/region-auc"
    )
    state = {"record": record}
    _activate(monkeypatch, state)
    raw_relative = (
        "## Selected Figures\n\n"
        f"![Region AUC]({FIGURE_REFERENCE})\n\n"
        f"![Panel A]({FIGURE_REFERENCE})\n\n"
        f"![Panel C]({FIGURE_REFERENCE})"
    )
    raw_absolute = f"![Region AUC Comparison]({figure})"
    stored_history = json.loads(json.dumps({
        "messages": [{"role": "assistant", "text": raw_relative}],
        "figures": [{"available": False, "url": None}],
    }))
    transcript = (
        tmp_path / "transcript" / ".discoveryagent" / "interactions"
        / "audit_interactions.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes((raw_relative + "\n" + raw_absolute).encode())
    before = transcript.read_bytes()

    client = app_module.app.test_client()
    _session(client, record)
    for raw in (
        raw_relative,
        stored_history["messages"][0]["text"],
        raw_absolute,
    ):
        response = client.post(
            f"/api/audit_chat_figures?project_id={record['id']}",
            json={
                "project_id": record["id"],
                "markdown": raw,
                # A stale presentation descriptor is never authoritative.
                "figures": [{"available": False, "url": None}],
            },
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["project_id"] == record["id"]
        assert all(item["available"] is True for item in payload["figures"])
        assert {
            item["artifact"]["artifactPath"] for item in payload["figures"]
        } == {FIGURE_REFERENCE}
        assert len({item["url"] for item in payload["figures"]}) == 1

    assert transcript.read_bytes() == before


def test_audit_history_without_original_reference_remains_unresolved(
    monkeypatch, tmp_path
):
    record, _code, _figure = _project(
        tmp_path, "prj_no_ref", "no-ref", "projects/no-ref"
    )
    state = {"record": record}
    _activate(monkeypatch, state)
    raw = "Figure unavailable\n\nRegion AUC Comparison"
    client = app_module.app.test_client()
    _session(client, record)

    response = client.post(
        f"/api/audit_chat_figures?project_id={record['id']}",
        json={
            "project_id": record["id"],
            "markdown": raw,
            "figures": [{"available": False, "url": None}],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["display_text"] == raw
    assert payload["figures"] == []


def test_audit_project_relocation_uses_current_registered_root(monkeypatch, tmp_path):
    old_record, _old_code, _old_figure = _project(
        tmp_path, "prj_relocated", "relocated", "old/location"
    )
    new_record, _new_code, new_figure = _project(
        tmp_path, "prj_relocated", "relocated", "new/location"
    )
    state = {"record": old_record}
    _activate(monkeypatch, state)
    state["record"] = new_record

    with app_module.app.test_request_context("/"):
        resolved = app_module._round_workspace_project_code_figure(FIGURE_REFERENCE)

    assert resolved["target"] == new_figure.resolve()


def test_audit_figure_endpoint_isolates_identical_relative_paths_by_project_id(
    monkeypatch, tmp_path
):
    first, _first_code, first_figure = _project(
        tmp_path, "prj_first", "first", "projects/first"
    )
    second, _second_code, second_figure = _project(
        tmp_path, "prj_second", "second", "projects/second"
    )
    state = {"record": first}
    _activate(monkeypatch, state)
    client = app_module.app.test_client()

    def resolve(record):
        state["record"] = record
        _session(client, record)
        response = client.post(
            f"/api/audit_chat_figures?project_id={record['id']}",
            json={"project_id": record["id"], "markdown": f"![same]({FIGURE_REFERENCE})"},
        )
        assert response.status_code == 200
        return response.get_json()

    first_payload = resolve(first)
    second_payload = resolve(second)
    assert first_payload["project_id"] == first["id"]
    assert second_payload["project_id"] == second["id"]
    assert first_payload["figures"][0]["url"] != second_payload["figures"][0]["url"]
    assert parse_qs(urlsplit(first_payload["figures"][0]["url"]).query)["project"] == ["first"]
    assert parse_qs(urlsplit(second_payload["figures"][0]["url"]).query)["project"] == ["second"]
    assert first_figure.read_bytes() != second_figure.read_bytes()

    _session(client, first)
    mismatch = client.post(
        f"/api/audit_chat_figures?project_id={second['id']}",
        json={"project_id": second["id"], "markdown": f"![same]({FIGURE_REFERENCE})"},
    )
    assert mismatch.status_code == 409
    assert mismatch.get_json()["code"] == "project_identity_mismatch"


def test_audit_figures_fail_closed_without_guessing_or_escaping(monkeypatch, tmp_path):
    record, code, exact = _project(
        tmp_path, "prj_safety", "safety", "projects/safety"
    )
    state = {"record": record}
    _activate(monkeypatch, state)
    figures_dir = code / "figures"
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    (figures_dir / "linked.png").symlink_to(outside)
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "secret.png").write_bytes(b"outside-dir")
    (figures_dir / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
    (figures_dir / ".hidden.png").write_bytes(b"hidden")
    (figures_dir / "directory.png").mkdir()
    os.mkfifo(figures_dir / "pipe.png")
    (figures_dir / "fig9b_region_auc_comparison-v2.png").write_bytes(b"near")
    (figures_dir / "note.txt").write_text("not image", encoding="utf-8")
    other, _other_code, other_figure = _project(
        tmp_path, "prj_other", "other", "projects/other"
    )
    wrong_case = "figures/FIG9B_REGION_AUC_COMPARISON.png"
    references = [
        "figures/missing.png",
        "figures/fig9b_region_auc_comparison-v3.png",
        "figures/linked.png",
        "figures/linked-dir/secret.png",
        "figures/.hidden.png",
        "figures/directory.png",
        "figures/pipe.png",
        "figures/../outside.png",
        str(outside),
        str(other_figure),
        "figures/note.txt",
        "/old/machine/figures/fig9b_region_auc_comparison.png",
    ]
    if not (code / wrong_case).exists():
        references.append(wrong_case)

    markdown = "\n\n".join(
        f"![unsafe-{index}]({reference})"
        for index, reference in enumerate(references)
    )
    with app_module.app.test_request_context("/"):
        display, figures = app_module._prepare_audit_chat_figures(markdown)

    assert exact.exists()
    assert other["id"] != record["id"]
    assert figures
    assert all(item["available"] is False for item in figures)
    assert all(item["url"] is None for item in figures)
    assert all("artifact" not in item for item in figures)
    assert str(outside) not in display
    assert str(other_figure) not in display


def test_audit_client_revalidates_history_and_never_retains_settled_figure_results():
    js = (
        Path(app_module.app.static_folder) / "project_audit_agent.js"
    ).read_text(encoding="utf-8")
    prepare = js.split(
        "async function prepareAuditFigurePresentation(text)", 1
    )[1].split("async function renderAuditAssistantMarkdown", 1)[0]
    history = js.split("function renderStoredChatHistory(entries)", 1)[1].split(
        "function isActiveChatPending", 1
    )[0]

    assert "const cacheKey = `${projectId}\\n${text}`;" in prepare
    assert "?project_id=${encodeURIComponent(projectId)}" in prepare
    assert "project_id: projectId, markdown: text" in prepare
    assert "responseBelongsToActiveProject(data)" in prepare
    assert "finally" in prepare
    assert "auditFigurePresentationInflight.delete(cacheKey)" in prepare
    assert "appendTurn(entry.role, entry.text, false, entry.responseTimeMs);" in history
