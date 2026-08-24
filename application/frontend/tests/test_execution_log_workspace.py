import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from flask import session

from da_selection_ui import app as app_module


@contextmanager
def _request_context(client):
    with client.session_transaction() as stored:
        snapshot = dict(stored)
    with app_module.app.test_request_context("/"):
        session.update(snapshot)
        yield


def _managed_round(tmp_path: Path):
    client = app_module.app.test_client()
    response = client.post(
        "/api/projects",
        data={
            "project_name": "Live Execution Log",
            "task_description": "Verify bounded execution output.",
            "dataset_context": "Synthetic data.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    project = app_module._v2_get_project(response.get_json()["project_id"])
    rounds_root = app_module._v2_round_dirs_root_from_any_checkpoint_path(
        Path(project["checkpoint_dir"])
    )
    round_dir = rounds_root / "round_0"
    query_dir = round_dir / "query_01"
    query_dir.mkdir(parents=True, exist_ok=True)
    (query_dir / "query_status.json").write_text(json.dumps({
        "schema_version": 2,
        "query_num": 1,
        "status": "running",
        "current_step": "executing_code",
        "objective": "Run the deterministic fixture.",
        "summary": "Deterministic execution is running.",
        "started_at": "2026-08-20T12:00:00Z",
    }), encoding="utf-8")
    (query_dir / "checkpoint.json").write_text(json.dumps({
        "workflow_state": {"results": [{
            "query_num": 1,
            "query_raw": "<execution_agent>Run the deterministic fixture.</execution_agent>",
            "query_parsed": "Run the deterministic fixture.",
            "status": "running",
            "started_at": "2026-08-20T12:00:00Z",
        }]},
    }), encoding="utf-8")
    log_text = "command: printf '<script>'\nstdout: first ✓\nstderr: warning\n"
    (query_dir / "execution_log_01.txt").write_text(log_text, encoding="utf-8")
    client.get(f"/rounds/?project={project['url_slug']}&round=0")
    return client, project, round_dir, query_dir, log_text


def test_activity_tree_cursor_download_and_terminal_state(monkeypatch, tmp_path):
    client, project, _round_dir, query_dir, log_text = _managed_round(tmp_path)
    (query_dir.parent.parent.parent / "run_state.json").write_text(json.dumps({
        "project_id": project["id"],
        "round": 0,
        "status": "running",
        "pid": 43210,
    }), encoding="utf-8")
    monkeypatch.setattr(
        app_module.local_round_runner, "_pid_matches_state", lambda _state: True,
    )

    activity = client.get("/api/round_activity", query_string={"round": 0}).get_json()
    query = next(row for row in activity["discovery_activity"] if row["kind"] == "query")
    execution_call = next(
        call for call in query["subagent_calls"] if call["agent_role"] == "Execution Agent"
    )
    assert execution_call["execution_log"] == {
        "name": "execution_log_01.txt",
        "path": "query_01/execution_log_01.txt",
        "artifact_role": "execution_log",
        "query_number": 1,
    }
    assert execution_call["execution_log_live"] is True

    tree = client.get("/api/round_workspace_tree").get_json()
    round_zero = next(item for item in tree["rounds"] if item["round_index"] == 0)
    folder = next(item for item in round_zero["tree"]["children"] if item.get("path") == "query_01")
    log_node = next(item for item in folder["children"] if item["name"] == "execution_log_01.txt")
    assert log_node["artifact_role"] == "execution_log"
    assert log_node["query_number"] == 1

    params = {
        "round": 0, "query": 1, "path": "query_01/execution_log_01.txt",
        "cursor": 0, "count": 12,
    }
    first = client.get("/api/round_workspace_execution_log", query_string=params)
    assert first.status_code == 200
    first_payload = first.get_json()
    assert first_payload["content"] == log_text.encode("utf-8")[:12].decode("utf-8")
    assert first_payload["live"] is True
    params["cursor"] = first_payload["next_cursor"]
    second = client.get("/api/round_workspace_execution_log", query_string=params).get_json()
    assert second["content"] == log_text.encode("utf-8")[12:24].decode("utf-8")
    assert not second["content"].startswith(first_payload["content"])

    download = client.get("/api/round_workspace_artifact", query_string={
        "round": 0, "path": "query_01/execution_log_01.txt", "download": 1,
    })
    assert download.status_code == 200
    assert download.get_data(as_text=True) == log_text
    assert "attachment" in download.headers["Content-Disposition"]

    status = json.loads((query_dir / "query_status.json").read_text(encoding="utf-8"))
    status.update(status="completed", current_step="completed", completed_at="2026-08-20T12:01:00Z")
    (query_dir / "query_status.json").write_text(json.dumps(status), encoding="utf-8")
    terminal = client.get("/api/round_workspace_execution_log", query_string={
        **params, "cursor": len(log_text.encode("utf-8")), "count": 64,
    }).get_json()
    assert terminal["live"] is False
    assert terminal["complete"] is True


def test_execution_log_endpoint_rejects_untrusted_identity_and_bounds_requests(tmp_path):
    client, _project, _round_dir, query_dir, log_text = _managed_round(tmp_path)
    base = {"round": 0, "query": 1, "path": "query_01/execution_log_01.txt", "cursor": 0}

    assert client.get("/api/round_workspace_execution_log", query_string={**base, "path": "../execution_log_01.txt"}).status_code == 404
    assert client.get("/api/round_workspace_execution_log", query_string={**base, "path": str(query_dir / "execution_log_01.txt")}).status_code == 404
    assert client.get("/api/round_workspace_execution_log", query_string={**base, "round": 1}).status_code == 404
    assert client.get("/api/round_workspace_execution_log", query_string={**base, "query": 2}).status_code == 404
    assert client.get("/api/round_workspace_execution_log", query_string={**base, "cursor": app_module.ROUND_WORKSPACE_EXECUTION_LOG_MAX_CURSOR + 1}).status_code == 400
    stale = client.get("/api/round_workspace_execution_log", query_string={**base, "cursor": len(log_text.encode()) + 1})
    assert stale.status_code == 409 and stale.get_json()["replaced"] is True
    bounded = client.get("/api/round_workspace_execution_log", query_string={**base, "count": 10**9}).get_json()
    assert bounded["response_limit"] == app_module.ROUND_WORKSPACE_EXECUTION_LOG_RESPONSE_MAX_BYTES


def test_interrupted_round_stops_live_state_without_removing_log(tmp_path):
    client, _project, round_dir, _query_dir, log_text = _managed_round(tmp_path)
    (round_dir.parent.parent / "run_state.json").write_text(json.dumps({
        "round": 0, "status": "interrupted",
    }), encoding="utf-8")
    payload = client.get("/api/round_workspace_execution_log", query_string={
        "round": 0,
        "query": 1,
        "path": "query_01/execution_log_01.txt",
        "cursor": len(log_text.encode("utf-8")),
        "count": 64,
    }).get_json()
    assert payload["live"] is False
    assert payload["complete"] is True


def test_symlink_and_historical_missing_log_are_not_exposed(tmp_path):
    client, _project, _round_dir, query_dir, _log_text = _managed_round(tmp_path)
    (query_dir / "execution_log_01.txt").unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    try:
        os.symlink(outside, query_dir / "execution_log_01.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    response = client.get("/api/round_workspace_execution_log", query_string={
        "round": 0, "query": 1, "path": "query_01/execution_log_01.txt", "cursor": 0,
    })
    assert response.status_code == 404
    activity = client.get("/api/round_activity", query_string={"round": 0}).get_json()
    query = next(row for row in activity["discovery_activity"] if row["kind"] == "query")
    assert all("execution_log" not in call for call in query["subagent_calls"])


def test_audit_context_accepts_only_validated_execution_log_identity(tmp_path):
    client, project, _round_dir, _query_dir, _log_text = _managed_round(tmp_path)
    raw = {
        "version": 1,
        "project": project["url_slug"],
        "artifacts": [{
            "pane": 0,
            "project": project["url_slug"],
            "round": 0,
            "sourceKind": "checkpoint",
            "source": "",
            "path": "query_01/execution_log_01.txt",
            "artifactPath": "query_01/execution_log_01.txt",
            "artifactRole": "execution_log",
            "queryNumber": 1,
        }],
    }
    with _request_context(client):
        artifacts = app_module._canonicalize_audit_viewer_context(raw)
        prompt = app_module._build_audit_viewer_context_prompt(raw)
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_role"] == "execution_log"
    assert "query 1 execution log" in prompt
    assert "does not grant access to hidden or background log content" in prompt
