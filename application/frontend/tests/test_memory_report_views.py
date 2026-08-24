"""Persisted Summary/Full behavior for Round Workspace memory reports."""

import os
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _client(monkeypatch, storage_root):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: storage_root)
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
    return client


def _managed_project(client, storage_root, name="Memory Views"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Audit accumulated research memory.",
            "dataset_context": "A local scientific dataset.",
        },
        content_type="multipart/form-data",
    )
    payload = response.get_json()
    project_id = payload["project_id"]
    slug = payload["destination"].rsplit("/", 1)[-1]
    project_root = Path(app_module._v2_get_project(project_id)["project_root"])
    round_dir = project_root / "checkpoint" / "checkpoints" / "round_2"
    round_dir.mkdir(parents=True)
    assert client.get(f"/projects/{slug}").status_code == 200
    return project_id, slug, round_dir


def _write_completed_round(
    round_dir,
    *,
    memory_full=True,
    memory_summary=True,
    round_full=True,
    round_summary=True,
):
    if round_full:
        (round_dir / "round_report.md").write_text(
            "# Round 2 Report\n\n## Round Report\n\nFULL ROUND\n",
            encoding="utf-8",
        )
    if round_summary:
        (round_dir / "round_report_summary.md").write_text(
            "# Round Report Summary\n\nSUMMARY ROUND\n",
            encoding="utf-8",
        )
    figures = round_dir / "figures"
    figures.mkdir(exist_ok=True)
    (figures / "memory-summary.png").write_bytes(b"summary-image")
    (figures / "memory-full.png").write_bytes(b"full-image")
    if memory_full:
        (round_dir / "memory_report.md").write_text(
            "# Memory-Integrated Report - Round 2\n\n"
            "## Accumulated Report\n\nFULL MEMORY\n\n"
            "![full](figures/memory-full.png)\n\n"
            "[Full reference](https://example.org/full)\n",
            encoding="utf-8",
        )
    if memory_summary:
        (round_dir / "memory_report_summary.md").write_text(
            "# Memory Report Summary\n\nSUMMARY MEMORY\n\n"
            "![summary](figures/memory-summary.png)\n\n"
            "[Summary reference](https://example.org/summary)\n",
            encoding="utf-8",
        )


def _workspace_round(client):
    payload = client.get("/api/round_workspace_tree").get_json()
    return next(item for item in payload["rounds"] if item["round_index"] == 2)


def _memory_node(round_entry):
    return next(
        child for child in round_entry["tree"]["children"]
        if child["name"] == "memory_report.md"
    )


def test_memory_report_both_variants_are_one_logical_workspace_entry(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _project_id, _slug, round_dir = _managed_project(client, tmp_path)
    _write_completed_round(round_dir)

    entry = _workspace_round(client)
    node = _memory_node(entry)
    names = [child["name"] for child in entry["tree"]["children"]]

    assert names.count("memory_report.md") == 1
    assert "memory_report_summary.md" not in names
    assert node["report_kind"] == "memory"
    assert node["has_report_summary"] is True
    assert node["has_report_full"] is True
    assert entry["has_memory_report"] is True
    assert entry["has_memory_report_full"] is True
    assert entry["has_memory_report_summary"] is True


@pytest.mark.parametrize(
    ("view", "filename", "expected", "other", "figure_name"),
    [
        ("summary", "memory_report_summary.md", "SUMMARY MEMORY", "FULL MEMORY", "memory-summary.png"),
        ("full", "memory_report.md", "FULL MEMORY", "SUMMARY MEMORY", "memory-full.png"),
    ],
)
def test_memory_views_read_only_exact_persisted_markdown_with_links_and_figures(
    monkeypatch, tmp_path, view, filename, expected, other, figure_name,
):
    client = _client(monkeypatch, tmp_path)
    _project_id, _slug, round_dir = _managed_project(client, tmp_path)
    _write_completed_round(round_dir)
    paths = [round_dir / "memory_report.md", round_dir / "memory_report_summary.md"]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    monkeypatch.setattr(
        app_module,
        "get_or_restore_generator",
        lambda: pytest.fail("opening or switching a persisted report must not restore a model"),
    )

    response = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": filename},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["artifact_role"] == "memory_report"
    assert expected in payload["display_text"]
    assert other not in payload["display_text"]
    assert "https://example.org/" in payload["display_text"]
    assert len(payload["figures"]) == 1
    assert figure_name in payload["figures"][0]["url"]
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before


def test_memory_full_only_falls_back_honestly(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _project_id, _slug, round_dir = _managed_project(client, tmp_path)
    _write_completed_round(round_dir, memory_summary=False)

    entry = _workspace_round(client)
    node = _memory_node(entry)
    assert node["has_report_full"] is True
    assert node["has_report_summary"] is False
    assert client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": "memory_report.md"},
    ).status_code == 200
    assert client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": "memory_report_summary.md"},
    ).status_code == 404


def test_memory_summary_only_falls_back_honestly(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _project_id, _slug, round_dir = _managed_project(client, tmp_path)
    _write_completed_round(round_dir, memory_full=False)

    entry = _workspace_round(client)
    node = _memory_node(entry)
    assert node["has_report_full"] is False
    assert node["has_report_summary"] is True
    assert client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": "memory_report_summary.md"},
    ).status_code == 200
    assert client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": "memory_report.md"},
    ).status_code == 404


def test_missing_empty_directory_and_symlink_memory_variants_fail_closed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _project_id, _slug, round_dir = _managed_project(client, tmp_path)
    _write_completed_round(round_dir, memory_full=False, memory_summary=False)
    (round_dir / "memory_report.md").write_text("", encoding="utf-8")
    (round_dir / "memory_report_summary.md").mkdir()

    entry = _workspace_round(client)
    assert all(child["name"] != "memory_report.md" for child in entry["tree"]["children"])
    for filename in ("memory_report.md", "memory_report_summary.md"):
        assert client.get(
            "/api/round_workspace_markdown",
            query_string={"round": 2, "path": filename},
        ).status_code == 404

    (round_dir / "memory_report.md").unlink()
    (round_dir / "memory_report_summary.md").rmdir()
    outside = tmp_path / "other-project-memory.md"
    outside.write_text("foreign", encoding="utf-8")
    os.symlink(outside, round_dir / "memory_report_summary.md")
    app_module._ROUND_WORKSPACE_TREE_CACHE.clear()
    assert all(
        child["name"] != "memory_report.md"
        for child in _workspace_round(client)["tree"]["children"]
    )


def test_memory_variants_survive_new_client_and_registry_reload(monkeypatch, tmp_path):
    first = _client(monkeypatch, tmp_path)
    _project_id, slug, round_dir = _managed_project(first, tmp_path)
    _write_completed_round(round_dir)
    assert _memory_node(_workspace_round(first))["has_report_summary"] is True

    app_module._ROUND_WORKSPACE_TREE_CACHE.clear()
    second = _client(monkeypatch, tmp_path)
    assert second.get(f"/projects/{slug}").status_code == 200
    reopened = _memory_node(_workspace_round(second))

    assert reopened["has_report_summary"] is True
    assert reopened["has_report_full"] is True


def test_supported_legacy_project_uses_exact_memory_variant_files(monkeypatch, tmp_path):
    project_root = tmp_path / "legacy-project"
    round_dir = project_root / "checkpoint" / "checkpoints" / "round_2"
    round_dir.mkdir(parents=True)
    _write_completed_round(round_dir)
    record = {
        "id": "legacy-id",
        "url_slug": "legacy-project",
        "project_root": str(project_root),
        "checkpoint_dir": str(round_dir),
        "code_dir": str(project_root / "code"),
    }
    monkeypatch.setattr(app_module, "_active_project_record", lambda: record)
    monkeypatch.setattr(
        app_module, "_round_workspace_checkpoint_dir_for_session", lambda: round_dir,
    )
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["v2_active_project_id"] = "legacy-id"
        flask_session["v2_active_project_slug"] = "legacy-project"

    summary = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": "memory_report_summary.md"},
    )
    full = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": "memory_report.md"},
    )

    assert summary.status_code == full.status_code == 200
    assert "SUMMARY MEMORY" in summary.get_json()["display_text"]
    assert "FULL MEMORY" in full.get_json()["display_text"]


@pytest.mark.parametrize(
    ("view", "filename", "body"),
    [
        ("summary", "round2_memory_report_summary.md", "SUMMARY MEMORY"),
        ("full", "round2_memory_report.md", "FULL MEMORY"),
    ],
)
def test_memory_download_tracks_selected_representation(
    monkeypatch, tmp_path, view, filename, body,
):
    client = _client(monkeypatch, tmp_path)
    _project_id, slug, round_dir = _managed_project(client, tmp_path)
    _write_completed_round(round_dir)
    before = {
        path.name: path.read_bytes()
        for path in (round_dir / "memory_report.md", round_dir / "memory_report_summary.md")
    }

    response = client.get(
        f"/projects/{slug}/rounds/2/report/download",
        query_string={"type": "memory", "view": view},
    )

    assert response.status_code == 200
    assert body in response.get_data(as_text=True)
    assert f"filename={filename}" in response.headers["Content-Disposition"]
    assert {
        path.name: path.read_bytes()
        for path in (round_dir / "memory_report.md", round_dir / "memory_report_summary.md")
    } == before


def _memory_snapshot(view):
    return {
        "version": 1,
        "project": "",
        "artifacts": [{
            "pane": 0,
            "project": "",
            "round": 2,
            "sourceKind": "checkpoint",
            "source": "",
            "path": "memory_report.md",
            "artifactPath": f"memory_report{'_summary' if view == 'summary' else ''}.md",
            "artifactType": "markdown",
            "reportKind": "memory",
            "reportView": view,
            "figures": [],
        }],
    }


def test_audit_context_resolves_only_selected_memory_representation(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    round_dir = project_root / "checkpoint" / "checkpoints" / "round_2"
    round_dir.mkdir(parents=True)
    _write_completed_round(round_dir)
    record = {
        "id": "project-id", "url_slug": "project", "project_root": str(project_root),
        "checkpoint_dir": str(round_dir), "code_dir": str(project_root / "code"),
    }
    monkeypatch.setattr(app_module, "_active_project_record", lambda: record)
    monkeypatch.setattr(
        app_module, "_round_workspace_checkpoint_dir_for_session", lambda: round_dir,
    )

    with app_module.app.test_request_context("/"):
        summary = app_module._canonicalize_audit_viewer_context(_memory_snapshot("summary"))
        full = app_module._canonicalize_audit_viewer_context(_memory_snapshot("full"))
        summary_prompt = app_module._build_audit_viewer_context_prompt(_memory_snapshot("summary"))
        full_prompt = app_module._build_audit_viewer_context_prompt(_memory_snapshot("full"))

    assert [item["target"].name for item in summary] == ["memory_report_summary.md"]
    assert [item["target"].name for item in full] == ["memory_report.md"]
    assert summary[0]["report_kind"] == full[0]["report_kind"] == "memory"
    assert summary[0]["report_view"] == "summary"
    assert full[0]["report_view"] == "full"
    assert "Report identity: memory report summary" in summary_prompt
    assert "Report identity: memory report full" in full_prompt
    assert "memory_report.md" not in summary_prompt
    assert "memory_report_summary.md" not in full_prompt


def test_round_and_memory_context_metadata_are_unambiguous(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    round_dir = project_root / "checkpoint" / "checkpoints" / "round_2"
    round_dir.mkdir(parents=True)
    _write_completed_round(round_dir)
    record = {
        "id": "project-id", "url_slug": "project", "project_root": str(project_root),
        "checkpoint_dir": str(round_dir), "code_dir": str(project_root / "code"),
    }
    monkeypatch.setattr(app_module, "_active_project_record", lambda: record)
    monkeypatch.setattr(
        app_module, "_round_workspace_checkpoint_dir_for_session", lambda: round_dir,
    )
    round_snapshot = _memory_snapshot("summary")
    round_snapshot["artifacts"][0].update({
        "path": "round_report.md",
        "artifactPath": "round_report_summary.md",
        "reportKind": "round",
    })

    with app_module.app.test_request_context("/"):
        memory = app_module._canonicalize_audit_viewer_context(_memory_snapshot("summary"))[0]
        round_report = app_module._canonicalize_audit_viewer_context(round_snapshot)[0]

    assert (memory["report_kind"], memory["report_view"]) == ("memory", "summary")
    assert (round_report["report_kind"], round_report["report_view"]) == ("round", "summary")
    assert memory["target"].name == "memory_report_summary.md"
    assert round_report["target"].name == "round_report_summary.md"
