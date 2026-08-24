"""Focused regressions for exact Report -> V2 Round Workspace migration."""

import os
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, name="Optic Nerve Regeneration"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Identify testable regeneration hypotheses.",
            "dataset_context": "A local scientific dataset.",
        },
        content_type="multipart/form-data",
    )
    payload = response.get_json()
    return payload["project_id"], payload["destination"].rsplit("/", 1)[-1]


def _round_dir(tmp_path, project_id, round_number):
    project_root = Path(app_module._v2_get_project(project_id)["project_root"])
    return project_root / "checkpoint" / "checkpoints" / f"round_{round_number}"


def _complete_round(tmp_path, project_id, round_number, marker, *, summary=True):
    round_dir = _round_dir(tmp_path, project_id, round_number)
    round_dir.mkdir(parents=True, exist_ok=True)
    (round_dir / "round_report.md").write_text(
        f"# Round {round_number} Report\n\n## Round Report\n\nFULL {marker}\n",
        encoding="utf-8",
    )
    (round_dir / "memory_report.md").write_text(
        f"# Project Memory\n\nUnderstanding from {marker}.\n",
        encoding="utf-8",
    )
    if summary:
        (round_dir / "round_report_summary.md").write_text(
            f"# Summary\n\nSUMMARY {marker}\n",
            encoding="utf-8",
        )
    return round_dir


def _activate(client, slug):
    assert client.get(f"/projects/{slug}").status_code == 200


def test_latest_report_loader_resolves_once_without_undefined_round(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id, _slug = _create(client)
    round_one = _complete_round(tmp_path, project_id, 1, "ROUND ONE")
    round_two = _complete_round(tmp_path, project_id, 2, "ROUND TWO")
    (_round_dir(tmp_path, project_id, 3) / "query_001").mkdir(parents=True)

    full, summary, report_dir = app_module._load_latest_round_report_content(round_one)

    assert report_dir == round_two
    assert "ROUND TWO" in full and "ROUND ONE" not in full
    assert "ROUND TWO" in summary and "ROUND ONE" not in summary


def test_round_workspace_summary_full_and_figures_are_exact_round(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    round_one = _complete_round(tmp_path, project_id, 1, "ROUND ONE")
    round_two = _complete_round(tmp_path, project_id, 2, "ROUND TWO")
    for number, round_dir in ((1, round_one), (2, round_two)):
        figures = round_dir / "figures"
        figures.mkdir()
        (figures / f"round-{number}.png").write_bytes(b"png")
        (round_dir / "round_report_summary.md").write_text(
            f"SUMMARY ROUND {number}\n\n![exact](figures/round-{number}.png)",
            encoding="utf-8",
        )
    _activate(client, slug)

    tree = client.get("/api/round_workspace_tree").get_json()
    entries = {item["round_index"]: item for item in tree["rounds"]}
    assert entries[1]["has_round_report_summary"] is True
    assert entries[2]["has_round_report_summary"] is True

    one_summary = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 1, "path": "round_report_summary.md"},
    ).get_json()
    two_summary = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": "round_report_summary.md"},
    ).get_json()
    one_full = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 1, "path": "round_report.md"},
    ).get_json()

    assert "SUMMARY ROUND 1" in one_summary["display_text"]
    assert "SUMMARY ROUND 2" not in one_summary["display_text"]
    assert "SUMMARY ROUND 2" in two_summary["display_text"]
    assert "FULL ROUND ONE" in one_full["display_text"]
    assert "round_1/figures/round-1.png" in one_summary["figures"][0]["url"]
    assert "round_2" not in one_summary["figures"][0]["url"]


def test_round_report_pair_is_one_logical_entry_without_hiding_unrelated_summaries(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    round_dir = _complete_round(tmp_path, project_id, 1, "ROUND ONE")
    (round_dir / "experiment_summary.md").write_text(
        "# Independent summary\n", encoding="utf-8",
    )
    _activate(client, slug)

    entry = next(
        item for item in client.get("/api/round_workspace_tree").get_json()["rounds"]
        if item["round_index"] == 1
    )
    names = [child["name"] for child in entry["tree"]["children"]]
    report = next(
        child for child in entry["tree"]["children"]
        if child.get("report_kind") == "round"
    )

    assert names.count("round_report.md") == 1
    assert "round_report_summary.md" not in names
    assert "experiment_summary.md" in names
    assert report["has_report_summary"] is True
    assert report["has_report_full"] is True


def test_missing_summary_is_not_substituted_from_another_round(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    _complete_round(tmp_path, project_id, 1, "ROUND ONE")
    _complete_round(tmp_path, project_id, 2, "ROUND TWO", summary=False)
    _activate(client, slug)

    tree = client.get("/api/round_workspace_tree").get_json()
    round_two = next(item for item in tree["rounds"] if item["round_index"] == 2)
    assert round_two["has_round_report_summary"] is False
    response = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 2, "path": "round_report_summary.md"},
    )
    assert response.status_code == 404


def test_round_workspace_report_controls_preserve_round_and_current_view():
    js = (
        Path(app_module.app.static_folder) / "round_workspace.js"
    ).read_text(encoding="utf-8")

    assert "requestedReportView" in js
    assert "roundReportFile(reviewRound,requestedReportView)" in js
    assert "reportArtifactPath" in js
    assert "round_report_summary.md" in js
    assert "setReportView(file,view)" in js
    assert "params.set('report_view','full')" in js
    assert "params.delete('report_view')" in js
    assert "reportDownloadUrl({...file,reportView:current})" in js
    assert "file.round" in js
    assert "file.hasReportSummary=item.has_report_summary" in js
    assert "file.reportView=file.hasReportSummary?'summary':'full'" in js
    assert "roundReportFile(reviewRound,requestedReportView)" in js
    assert "file.hasReportSummary&&file.hasReportFull" in js
    assert "['full','Full Report']" in js


def test_report_workspace_urls_use_summary_as_default_and_encode_explicit_full():
    with app_module.app.test_request_context("/"):
        assert app_module._v2_round_workspace_report_url("project", 4) == (
            "/rounds/?project=project&round=4&review=1"
        )
        assert app_module._v2_round_workspace_report_url(
            "project", 4, view="full",
        ) == "/rounds/?project=project&round=4&review=1&report_view=full"


def _research_markdown_artifacts(round_dir, marker):
    artifacts = {
        "selected_hypothesis.md": f"# Selected {marker}\n\nRaw selected bytes.\n",
        "query_004/query_004.md": f"# Query {marker}\n\nRaw query wrapper bytes.\n",
        "memory_report.md": f"# Memory {marker}\n\nRaw memory bytes.\n",
    }
    for relative, content in artifacts.items():
        target = round_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
    return artifacts


def test_round_markdown_download_name_preserves_basename_extension_and_round_zero():
    assert app_module._round_workspace_markdown_download_name(
        0, "nested/selected_hypothesis.md"
    ) == "round0_selected_hypothesis.md"
    assert app_module._round_workspace_markdown_download_name(
        4, "query_007.markdown"
    ) == "round4_query_007.markdown"


@pytest.mark.parametrize(
    "relative",
    ["selected_hypothesis.md", "query_004/query_004.md", "memory_report.md"],
)
def test_trusted_markdown_artifact_download_is_exact_and_raw(
    monkeypatch, tmp_path, relative
):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    round_three = _complete_round(tmp_path, project_id, 3, "ROUND THREE")
    round_four = _complete_round(tmp_path, project_id, 4, "ROUND FOUR")
    three = _research_markdown_artifacts(round_three, "THREE")
    _research_markdown_artifacts(round_four, "FOUR")

    response = client.get(
        f"/projects/{slug}/rounds/3/artifacts/markdown/download",
        query_string={"path": relative},
    )

    assert response.status_code == 200
    assert response.data == three[relative].encode("utf-8")
    assert "attachment" in response.headers["Content-Disposition"]
    assert (
        f"filename=round3_{Path(relative).name}"
        in response.headers["Content-Disposition"]
    )
    assert b"FOUR" not in response.data


def test_same_markdown_basename_uses_the_exact_selected_round_prefix(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    round_two = _complete_round(tmp_path, project_id, 2, "ROUND TWO")
    round_four = _complete_round(tmp_path, project_id, 4, "ROUND FOUR")
    two = _research_markdown_artifacts(round_two, "TWO")
    four = _research_markdown_artifacts(round_four, "FOUR")

    response_two = client.get(
        f"/projects/{slug}/rounds/2/artifacts/markdown/download",
        query_string={"path": "memory_report.md"},
    )
    response_four = client.get(
        f"/projects/{slug}/rounds/4/artifacts/markdown/download",
        query_string={"path": "memory_report.md"},
    )

    assert response_two.data == two["memory_report.md"].encode("utf-8")
    assert response_four.data == four["memory_report.md"].encode("utf-8")
    assert (
        "filename=round2_memory_report.md"
        in response_two.headers["Content-Disposition"]
    )
    assert (
        "filename=round4_memory_report.md"
        in response_four.headers["Content-Disposition"]
    )


def test_markdown_download_rejects_untrusted_paths_types_and_missing_files(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    round_dir = _complete_round(tmp_path, project_id, 1, "ROUND ONE")
    _research_markdown_artifacts(round_dir, "ONE")
    (round_dir / "technical_notes.md").write_text("private workflow notes", encoding="utf-8")
    (round_dir / "figure.png").write_bytes(b"png")
    outside = round_dir.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")

    endpoint = f"/projects/{slug}/rounds/1/artifacts/markdown/download"
    for relative in (
        "technical_notes.md",
        "figure.png",
        "missing.md",
        "../outside.md",
        "query_004/../../outside.md",
        "/etc/passwd.md",
    ):
        assert client.get(endpoint, query_string={"path": relative}).status_code == 404

    symlink = round_dir / "query_005"
    symlink.mkdir()
    os.symlink(outside, symlink / "query_005.md")
    assert client.get(
        endpoint, query_string={"path": "query_005/query_005.md"}
    ).status_code == 404


def test_markdown_download_is_project_round_scoped_and_size_limited(
    monkeypatch, tmp_path
):
    client = _client(monkeypatch, tmp_path)
    first_id, first_slug = _create(client, "First Project")
    second_id, second_slug = _create(client, "Second Project")
    first_round = _complete_round(tmp_path, first_id, 1, "FIRST")
    second_round = _complete_round(tmp_path, second_id, 1, "SECOND")
    _research_markdown_artifacts(first_round, "FIRST")
    _research_markdown_artifacts(second_round, "SECOND")

    first = client.get(
        f"/projects/{first_slug}/rounds/1/artifacts/markdown/download",
        query_string={"path": "selected_hypothesis.md"},
    )
    second = client.get(
        f"/projects/{second_slug}/rounds/1/artifacts/markdown/download",
        query_string={"path": "selected_hypothesis.md"},
    )
    assert b"FIRST" in first.data and b"SECOND" not in first.data
    assert b"SECOND" in second.data and b"FIRST" not in second.data
    assert client.get(
        "/projects/not-a-project/rounds/1/artifacts/markdown/download",
        query_string={"path": "selected_hypothesis.md"},
    ).status_code == 404
    assert client.get(
        f"/projects/{first_slug}/rounds/2/artifacts/markdown/download",
        query_string={"path": "selected_hypothesis.md"},
    ).status_code == 404

    cross_project_query = first_round / "query_099"
    cross_project_query.mkdir()
    os.symlink(
        second_round / "selected_hypothesis.md",
        cross_project_query / "query_099.md",
    )
    assert client.get(
        f"/projects/{first_slug}/rounds/1/artifacts/markdown/download",
        query_string={"path": "query_099/query_099.md"},
    ).status_code == 404

    monkeypatch.setattr(app_module, "ROUND_WORKSPACE_MARKDOWN_DOWNLOAD_MAX_BYTES", 2)
    assert client.get(
        f"/projects/{first_slug}/rounds/1/artifacts/markdown/download",
        query_string={"path": "selected_hypothesis.md"},
    ).status_code == 413


def test_markdown_artifact_controls_reuse_report_download_control_and_follow_tabs():
    js = (
        Path(app_module.app.static_folder) / "round_workspace.js"
    ).read_text(encoding="utf-8")

    assert "const isDownloadableMarkdown" in js
    assert "file.sourceKind==='checkpoint'" in js
    assert "'/artifacts/markdown/download?path='" in js
    assert "function downloadControl(file,current)" in js
    assert "function artifactViewControls(file)" in js
    assert "const viewControls=artifactViewControls(file)" in js
    assert "download.innerHTML='<svg class=\"rw-report-download-icon\"" in js
    assert js.index("download.innerHTML='<svg class=\"rw-report-download-icon\"") < js.index("<span>Download</span>'")
    assert "download.href=report?reportDownloadUrl" in js
    assert ":markdownArtifactDownloadUrl(file)" in js


@pytest.mark.parametrize(
    ("view", "body", "filename"),
    [
        ("full", "FULL ROUND THREE", "round3_round_report.md"),
        ("summary", "SUMMARY ROUND THREE", "round3_round_report_summary.md"),
    ],
)
def test_exact_report_download(monkeypatch, tmp_path, view, body, filename):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    round_three = _complete_round(tmp_path, project_id, 3, "ROUND THREE")
    _complete_round(tmp_path, project_id, 4, "ROUND FOUR")
    expected = (
        round_three / (
            "round_report_summary.md" if view == "summary" else "round_report.md"
        )
    ).read_bytes()

    response = client.get(
        f"/projects/{slug}/rounds/3/report/download", query_string={"view": view}
    )

    assert response.status_code == 200
    assert response.data == expected
    assert body in response.get_data(as_text=True)
    assert "ROUND FOUR" not in response.get_data(as_text=True)
    disposition = response.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert f"filename={filename}" in disposition


def test_download_rejects_wrong_round_missing_summary_traversal_and_size(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    _complete_round(tmp_path, project_id, 1, "ROUND ONE", summary=False)

    assert client.get(f"/projects/{slug}/rounds/9/report/download").status_code == 404
    assert client.get(
        f"/projects/{slug}/rounds/1/report/download?view=summary"
    ).status_code == 404
    assert client.get(
        f"/projects/{slug}/rounds/1/report/download?view=../../secret"
    ).status_code == 404

    monkeypatch.setattr(app_module, "ROUND_REPORT_DOWNLOAD_MAX_BYTES", 2)
    assert client.get(
        f"/projects/{slug}/rounds/1/report/download?view=full"
    ).status_code == 413


def test_download_internal_project_id_redirects_to_canonical_slug(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    _complete_round(tmp_path, project_id, 1, "ROUND ONE")

    response = client.get(
        f"/projects/{project_id}/rounds/1/report/download?view=summary"
    )

    assert response.status_code == 302
    assert response.headers["Location"] == (
        f"/projects/{slug}/rounds/1/report/download?view=summary"
    )


def test_download_rejects_cross_round_and_cross_project_symlinks(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first_id, first_slug = _create(client, "First Project")
    second_id, _second_slug = _create(client, "Second Project")
    first_round = _complete_round(tmp_path, first_id, 1, "FIRST", summary=False)
    second_round = _complete_round(tmp_path, second_id, 1, "SECOND")
    other_round = _complete_round(tmp_path, first_id, 2, "OTHER ROUND")

    os.symlink(
        second_round / "round_report_summary.md",
        first_round / "round_report_summary.md",
    )
    assert client.get(
        f"/projects/{first_slug}/rounds/1/report/download?view=summary"
    ).status_code == 404

    (first_round / "round_report_summary.md").unlink()
    os.symlink(
        other_round / "round_report_summary.md",
        first_round / "round_report_summary.md",
    )
    assert client.get(
        f"/projects/{first_slug}/rounds/1/report/download?view=summary"
    ).status_code == 404


def test_legacy_routes_and_project_home_use_exact_v2_destinations(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id, slug = _create(client)
    _complete_round(tmp_path, project_id, 1, "ROUND ONE")
    _complete_round(tmp_path, project_id, 2, "ROUND TWO")
    running = _round_dir(tmp_path, project_id, 3)
    (running / "query_001").mkdir(parents=True)
    registry = tmp_path / "v2_projects.json"
    before = registry.read_bytes()

    completed = client.get(f"/legacy/projects/{slug}/rounds/1")
    running_response = client.get(f"/legacy/projects/{slug}/rounds/3")
    missing = client.get(f"/legacy/projects/{slug}/rounds/8")

    assert completed.headers["Location"] == (
        f"/rounds/?project={slug}&round=1&review=1"
    )
    assert running_response.headers["Location"] == (
        f"/rounds/?project={slug}&round=3"
    )
    assert missing.status_code == 404
    assert registry.read_bytes() == before

    _activate(client, slug)
    state = client.get("/api/project_state").get_json()
    destinations = [item["destination"] for item in state["recent_rounds"]]
    assert destinations == [
        f"/rounds/?project={slug}&round=2&review=1",
        f"/rounds/?project={slug}&round=1&review=1",
    ]
    assert all("/legacy/" not in destination for destination in destinations)


def test_legacy_report_shell_and_private_endpoints_are_removed():
    root = Path(app_module.app.root_path)
    routes = {rule.rule for rule in app_module.app.url_map.iter_rules()}

    assert not (root / "templates" / "report.html").exists()
    assert not (root / "static" / "script.js").exists()
    assert "/report" not in routes
    assert "/api/report_chat" not in routes
    assert "/api/cancel_report_chat" not in routes
    assert "/download_report" not in routes
