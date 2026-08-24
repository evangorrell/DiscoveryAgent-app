"""Focused contracts for exact Round Activity query Input and trusted Output."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from flask import session

from da_selection_ui import app as app_module


def _write_checkpoint(query_dir: Path, query_num: int, exact_input: str, result: str) -> None:
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "checkpoint_version": "2.0",
        "workflow_state": {"results": [{
            "query_num": query_num,
            "query_raw": (
                "PRIVATE DIRECTOR THOUGHT\n"
                f"<execution_agent>{exact_input}</execution_agent>\n"
                "provider-secret-that-must-not-be-exposed"
            ),
            "query_parsed": exact_input,
            "result": result,
            "timestamp": "2026-08-19T12:00:00Z",
        }]},
    }), encoding="utf-8")


def _write_status(query_dir: Path, query_num: int, status: str, **extra) -> None:
    (query_dir / "query_status.json").write_text(json.dumps({
        "schema_version": 2,
        "query_num": query_num,
        "status": status,
        "current_step": "completed" if status == "completed" else "planning",
        "objective": "Short operational objective.",
        "summary": "Existing concise summary remains unchanged.",
        "started_at": "2026-08-19T11:00:00Z",
        "completed_at": "2026-08-19T12:00:00Z" if status == "completed" else None,
        **extra,
    }), encoding="utf-8")


def _query_item(query_dir: Path, query_num: int) -> dict:
    return app_module._build_discovery_query_item(
        query_dir,
        query_num,
        {},
        {},
        False,
        query_dir.parent / "round_report.md",
        False,
        None,
    )


@contextmanager
def _client_request_context(client):
    with client.session_transaction() as stored:
        session_snapshot = dict(stored)
    with app_module.app.test_request_context("/"):
        session.update(session_snapshot)
        yield


def test_completed_query_keeps_summary_and_subagent_while_linking_paired_io(tmp_path):
    round_dir = tmp_path / "round_4"
    query_dir = round_dir / "query_07"
    query_dir.mkdir(parents=True)
    exact_input = "Compare treatment and control.\n\nPreserve this exact second paragraph."
    exact_result = "# Scientific result\n\nThe matched effect was 0.42."
    _write_checkpoint(query_dir, 7, exact_input, exact_result)
    _write_status(query_dir, 7, "completed", query_input=exact_input)
    (query_dir / "query_input_07.md").write_text(exact_input, encoding="utf-8")
    (query_dir / "query_report_07.md").write_text(exact_result, encoding="utf-8")

    item = _query_item(query_dir, 7)

    assert item["query_summary"] == "Existing concise summary remains unchanged."
    assert item["query_input"] == {
        "name": "query_input_07.md",
        "path": "query_07/query_input_07.md",
        "artifact_role": "query_input",
        "query_number": 7,
    }
    assert item["query_input"] != item["query_summary"]
    assert item["query_report"] == {
        "name": "query_report_07.md",
        "path": "query_07/query_report_07.md",
        "artifact_role": "query_report",
        "query_number": 7,
    }
    assert item["subagent_calls"][0]["agent_role"] == "Execution Agent"
    assert item["subagent_calls"][0]["sequence"] == 1
    serialized = json.dumps(item)
    assert exact_input not in serialized
    assert "PRIVATE DIRECTOR THOUGHT" not in serialized
    assert "provider-secret-that-must-not-be-exposed" not in serialized
    assert exact_result not in serialized


@pytest.mark.parametrize("status", ["running", "failed"])
def test_noncompleted_query_has_exact_input_without_completed_output(tmp_path, status):
    query_dir = tmp_path / "round_2" / "query_03"
    query_dir.mkdir(parents=True)
    exact_input = "Run the requested scientific comparison.\nKeep covariates fixed."
    _write_status(query_dir, 3, status, query_input=exact_input)
    (query_dir / "query_input_03.md").write_text(exact_input, encoding="utf-8")

    item = _query_item(query_dir, 3)

    assert item["query_input"]["path"] == "query_03/query_input_03.md"
    assert item["query_report"] is None


def test_historical_exact_input_fallbacks_do_not_use_objective_or_raw_reasoning(tmp_path):
    query_dir = tmp_path / "round_1" / "query_002"
    query_dir.mkdir(parents=True)
    _write_status(
        query_dir,
        2,
        "running",
        schema_version=1,
        objective="A legacy bounded objective is not an exact persisted input.",
        query_input="schema-one-field-must-not-be-trusted",
    )
    assert app_module._exact_persisted_query_input(query_dir, 2) is None

    historical = "Historical exact request.\nSecond line."
    (query_dir / "query_002.md").write_text(
        "# Query 2\n\n**Parsed Query:**\n```text\n"
        + historical
        + "\n```\n\n# Agent Report\n\nPersisted result.",
        encoding="utf-8",
    )
    assert app_module._exact_persisted_query_input(query_dir, 2) == historical

    (query_dir / "query_002.md").write_text(
        "# Query 2\n\nThought: hidden\n<execution_agent>fabricated</execution_agent>",
        encoding="utf-8",
    )
    assert app_module._exact_persisted_query_input(query_dir, 2) is None


def test_missing_corrupt_mismatched_and_oversized_input_fail_closed(tmp_path):
    query_dir = tmp_path / "round_8" / "query_004"
    query_dir.mkdir(parents=True)
    assert app_module._exact_persisted_query_input(query_dir, 4) is None

    (query_dir / "checkpoint.json").write_text("{not-json", encoding="utf-8")
    assert app_module._exact_persisted_query_input(query_dir, 4) is None

    _write_checkpoint(query_dir, 5, "another query", "another result")
    assert app_module._exact_persisted_query_input(query_dir, 4) is None

    _write_status(
        query_dir,
        4,
        "running",
        query_input="x" * (app_module.ROUND_ACTIVITY_QUERY_INPUT_MAX_CHARS + 1),
    )
    assert app_module._exact_persisted_query_input(query_dir, 4) is None


def test_query_output_identity_requires_a_real_contained_artifact(tmp_path):
    round_dir = tmp_path / "round_5"
    query_dir = round_dir / "query_06"
    query_dir.mkdir(parents=True)
    report = query_dir / "query_report_06.md"
    report.write_text("# Final", encoding="utf-8")

    assert app_module._round_activity_query_report(query_dir, 6, "completed")["path"] == (
        "query_06/query_report_06.md"
    )
    assert app_module._round_activity_query_report(query_dir, 5, "completed") is None
    assert app_module._round_activity_query_report(query_dir, 6, "running")["path"] == "query_06/query_report_06.md"
    assert app_module._round_activity_query_report(query_dir, 6, "failed")["path"] == "query_06/query_report_06.md"
    report.unlink()
    report.symlink_to(tmp_path / "outside.md")
    (tmp_path / "outside.md").write_text("private", encoding="utf-8")
    assert app_module._round_activity_query_report(query_dir, 6, "completed") is None


def test_reading_activity_details_does_not_write_project_data(tmp_path):
    query_dir = tmp_path / "round_3" / "query_001"
    query_dir.mkdir(parents=True)
    exact_input = "Inspect the persisted measurements."
    _write_checkpoint(query_dir, 1, exact_input, "Final output")
    _write_status(query_dir, 1, "completed", query_input=exact_input)
    (query_dir / "query_001.md").write_text("# Final output", encoding="utf-8")
    before = {
        path.relative_to(query_dir): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in query_dir.iterdir()
    }

    _query_item(query_dir, 1)

    after = {
        path.relative_to(query_dir): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in query_dir.iterdir()
    }
    assert after == before


def test_exact_input_reader_rejects_query_directory_symlink(tmp_path):
    real_query = tmp_path / "elsewhere" / "query_001"
    real_query.mkdir(parents=True)
    _write_checkpoint(real_query, 1, "Do not cross this boundary.", "result")
    linked_round = tmp_path / "round_9"
    linked_round.mkdir()
    linked_query = linked_round / "query_001"
    try:
        os.symlink(real_query, linked_query, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    assert app_module._exact_persisted_query_input(linked_query, 1) is None
    assert app_module._round_activity_query_report(linked_query, 1, "completed") is None


def test_managed_project_exposes_trusted_query_report_and_distinct_audit_context(tmp_path):
    client = app_module.app.test_client()
    response = client.post(
        "/api/projects",
        data={
            "project_name": "Query Context Project",
            "task_description": "Validate query report context.",
            "dataset_context": "Synthetic context.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    project = app_module._v2_get_project(response.get_json()["project_id"])
    rounds_root = app_module._v2_round_dirs_root_from_any_checkpoint_path(
        Path(project["checkpoint_dir"])
    )
    query_dir = rounds_root / "round_0" / "query_02"
    query_dir.mkdir(parents=True, exist_ok=True)
    exact_input = "Evaluate the managed project evidence."
    _write_checkpoint(query_dir, 2, exact_input, "Managed result")
    _write_status(query_dir, 2, "completed", query_input=exact_input)
    (query_dir / "query_input_02.md").write_text(exact_input, encoding="utf-8")
    (query_dir / "query_report_02.md").write_text("# Managed query report", encoding="utf-8")
    client.get(f"/rounds/?project={project['url_slug']}&round=0")

    activity = client.get("/api/round_activity", query_string={"round": 0}).get_json()
    query = next(row for row in activity["discovery_activity"] if row["kind"] == "query")
    assert query["query_input"]["path"] == "query_02/query_input_02.md"
    assert query["query_report"]["path"] == "query_02/query_report_02.md"

    tree = client.get("/api/round_workspace_tree").get_json()
    round_zero = next(row for row in tree["rounds"] if row["round_index"] == 0)
    report_node = next(
        row
        for folder in round_zero["tree"]["children"]
        if folder.get("path") == "query_02"
        for row in folder.get("children", [])
        if row.get("path") == "query_02/query_report_02.md"
    )
    assert report_node["artifact_role"] == "query_report"
    assert report_node["query_number"] == 2
    query_folder = next(
        row for row in round_zero["tree"]["children"] if row.get("path") == "query_02"
    )
    input_node = next(
        row for row in query_folder["children"] if row.get("name") == "query_input_02.md"
    )
    assert input_node["artifact_role"] == "query_input"
    assert input_node["query_number"] == 2

    input_display = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 0, "path": "query_02/query_input_02.md"},
    )
    report_display = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 0, "path": "query_02/query_report_02.md"},
    )
    assert input_display.status_code == 200
    assert input_display.get_json()["display_text"] == exact_input
    assert input_display.get_json()["artifact_role"] == "query_input"
    assert report_display.status_code == 200
    assert report_display.get_json()["artifact_role"] == "query_report"
    input_download = client.get(
        f"/projects/{project['url_slug']}/rounds/0/artifacts/markdown/download",
        query_string={"path": "query_02/query_input_02.md"},
    )
    report_download = client.get(
        f"/projects/{project['url_slug']}/rounds/0/artifacts/markdown/download",
        query_string={"path": "query_02/query_report_02.md"},
    )
    assert input_download.data == exact_input.encode("utf-8")
    assert report_download.data == b"# Managed query report"
    assert "round0_query_input_02.md" in input_download.headers["Content-Disposition"]
    assert "round0_query_report_02.md" in report_download.headers["Content-Disposition"]

    with _client_request_context(client):
        registry = app_module._round_workspace_artifact_registry(0)
        source = next(key for key, value in registry.items() if value.get("kind") == "checkpoint")
        raw = {
            "pane": 0,
            "project": project["url_slug"],
            "round": 0,
            "sourceKind": "checkpoint",
            "source": source,
            "path": "query_02/query_report_02.md",
            "artifactPath": "query_02/query_report_02.md",
            "artifactType": "markdown",
            "reportKind": None,
            "reportView": None,
            "artifactRole": "query_report",
            "queryNumber": 2,
            "figures": [],
        }
        resolved = app_module._audit_resolve_viewer_artifact(raw)
        prompt = app_module._build_audit_viewer_context_prompt({
            "version": 1,
            "project": project["url_slug"],
            "artifacts": [raw],
        })
        inferred = app_module._audit_resolve_viewer_artifact({
            **raw,
            "artifactRole": None,
            "queryNumber": None,
        })
        mismatched = app_module._audit_resolve_viewer_artifact({**raw, "queryNumber": 3})
        raw_input = {
            **raw,
            "path": "query_02/query_input_02.md",
            "artifactPath": "query_02/query_input_02.md",
            "artifactRole": "query_input",
        }
        resolved_input = app_module._audit_resolve_viewer_artifact(raw_input)
        input_prompt = app_module._build_audit_viewer_context_prompt({
            "version": 1,
            "project": project["url_slug"],
            "artifacts": [raw_input],
        })

    assert resolved["target"] == (query_dir / "query_report_02.md").resolve()
    assert resolved["artifact_role"] == "query_report"
    assert resolved["query_number"] == 2
    assert inferred["artifact_role"] == "query_report"
    assert "Query artifact identity: query 2 report" in prompt
    assert "Report identity: round report" not in prompt
    assert "Report identity: memory report" not in prompt
    assert mismatched is None
    assert resolved_input["target"] == (query_dir / "query_input_02.md").resolve()
    assert resolved_input["artifact_role"] == "query_input"
    assert "Query artifact identity: query 2 input" in input_prompt


def test_paired_query_paths_reject_mismatch_traversal_and_absolute_paths(tmp_path):
    assert app_module._round_workspace_query_artifact_identity(
        "query_09/query_input_09.md"
    ) == (9, "query_input")
    assert app_module._round_workspace_query_artifact_identity(
        "query_100/query_report_100.md"
    ) == (100, "query_report")
    assert app_module._round_workspace_query_artifact_identity(
        "query_09/query_input_10.md"
    ) is None
    assert app_module._round_workspace_safe_relative(
        "query_09/../../private.md"
    ) is None
    assert app_module._round_workspace_safe_relative(
        "/tmp/query_09/query_input_09.md"
    ) is None


def test_legacy_round_activity_reads_exact_persisted_input_without_migration(monkeypatch, tmp_path):
    rounds = tmp_path / "legacy" / "checkpoint" / "checkpoints"
    query_dir = rounds / "round_6" / "query_001"
    query_dir.mkdir(parents=True)
    exact_input = "Historical delegated instruction.\nNo migration is required."
    _write_checkpoint(query_dir, 1, exact_input, "Historical result")
    (query_dir / "query_001.md").write_text("# Historical result", encoding="utf-8")
    (query_dir.parent / "round_report.md").write_text("# Completed round", encoding="utf-8")
    before = (query_dir / "checkpoint.json").read_bytes()
    monkeypatch.setattr(
        app_module,
        "_round_workspace_checkpoint_dir_for_session",
        lambda: rounds,
    )
    client = app_module.app.test_client()
    with client.session_transaction() as stored:
        stored["authenticated"] = True

    payload = client.get("/api/round_activity", query_string={"round": 6}).get_json()
    query = next(row for row in payload["discovery_activity"] if row["kind"] == "query")

    assert query["query_input"] is None
    assert query["query_report"]["path"] == "query_001/query_001.md"
    assert (query_dir / "checkpoint.json").read_bytes() == before
