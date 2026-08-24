"""Contained project-code viewing behavior in the V2 Round Workspace."""

import json
import os
from pathlib import Path

import pytest

from da_selection_ui import app as app_module
from da_selection_ui import local_round_runner as runner


def _record(project_root: Path, *, relative_code: bool = False) -> dict:
    code = project_root / "code"
    return {
        "id": "active-project",
        "url_slug": "active-project",
        "project_root": str(project_root),
        "code_dir": "code" if relative_code else str(code),
        "checkpoint_dir": str(project_root / "checkpoint"),
    }


def _authenticated_client():
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["v2_active_project_id"] = "active-project"
        flask_session["v2_active_project_slug"] = "active-project"
    return client


def test_only_code_files_return_exact_canonical_file_path(monkeypatch, tmp_path):
    project_root = tmp_path / "managed-project"
    code = project_root / "code"
    nested_dir = code / "analysis"
    nested_dir.mkdir(parents=True)
    nested_file = nested_dir / "pipeline.py"
    secret_source = "print('VISIBLE-CODE-FIXTURE')\n"
    nested_file.write_text(secret_source, encoding="utf-8")
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(project_root))
    client = _authenticated_client()

    root = client.get("/api/round_workspace_code_location", query_string={"path": ""})
    directory = client.get(
        "/api/round_workspace_code_location", query_string={"path": "analysis"},
    )
    file_response = client.get(
        "/api/round_workspace_code_location", query_string={"path": "analysis/pipeline.py"},
    )

    assert root.status_code == directory.status_code == 404
    assert file_response.status_code == 200
    assert file_response.get_json() == {
        "success": True,
        "absolute_path": str(nested_file.resolve()),
    }
    assert file_response.get_json()["absolute_path"] != str(code.resolve())
    assert file_response.get_json()["absolute_path"].endswith("analysis/pipeline.py")
    assert secret_source.strip() not in file_response.get_data(as_text=True)

    content_response = client.get(
        "/api/round_workspace_code_artifact",
        query_string={"path": "analysis/pipeline.py"},
    )
    assert content_response.status_code == 200
    assert content_response.get_data(as_text=True) == secret_source


def test_supported_legacy_relative_code_root_is_anchored_to_registered_project(monkeypatch, tmp_path):
    project_root = tmp_path / "legacy-project"
    code = project_root / "code"
    code.mkdir(parents=True)
    script = code / "legacy_analysis.R"
    script.write_text("stop('never preview this')\n", encoding="utf-8")
    monkeypatch.setattr(
        app_module, "_active_project_record", lambda: _record(project_root, relative_code=True),
    )

    with app_module.app.test_request_context("/"):
        assert app_module._round_workspace_registered_code_root_for_session() == code.resolve()
        selection = app_module._round_workspace_code_selection("legacy_analysis.R")

    assert selection is not None
    assert selection["target"] == script.resolve()
    assert selection["code_root"] == str(code.resolve())
    assert selection["absolute_path"] == str(script.resolve())


def test_root_level_code_file_returns_exact_absolute_file_path(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    code = project_root / "code"
    code.mkdir(parents=True)
    selected = code / "analysis.py"
    selected.write_text("print('root')\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(project_root))

    response = _authenticated_client().get(
        "/api/round_workspace_code_location", query_string={"path": selected.name},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "absolute_path": str(selected.resolve()),
    }


def test_backend_generated_code_contract_and_all_launch_modes_share_canonical_root(tmp_path):
    project_root = tmp_path / "registered-project"
    code = project_root / "code"
    (code / "data").mkdir(parents=True)
    readme = code / "README.md"
    readme.write_text("Project context", encoding="utf-8")
    round_zero = project_root / "checkpoint" / "checkpoints" / "round_0"
    round_zero.mkdir(parents=True)
    (round_zero / "config.json").write_text(
        json.dumps({"config": {"num_queries": 2}}), encoding="utf-8",
    )
    project = {
        "id": "registered-project",
        "project_root": str(project_root),
        "code_dir": str(code),
        "checkpoint_dir": str(round_zero),
        "readme_path": str(readme),
        "task_description": "Generate a reproducible analysis.",
    }

    fresh_paths = runner._project_paths(project)
    fresh_paths.update(runner._resolve_launch(project, fresh_paths))
    fresh = runner._hydrate_round_zero_config(project, fresh_paths)

    # This is the path handed to the backend execution agent as its tool cwd;
    # a relative generated-code write therefore lands beneath the code tree.
    generated = Path(fresh["working_directory"]) / "analysis" / "generated_pipeline.py"
    generated.parent.mkdir()
    generated.write_text("print('generated')\n", encoding="utf-8")
    assert generated.resolve().is_relative_to(code.resolve())

    report = round_zero / "round_report.md"
    memory = round_zero / "memory_report.md"
    selection = round_zero / "selected_hypothesis.md"
    report.write_text("complete", encoding="utf-8")
    memory.write_text("memory", encoding="utf-8")
    selection.write_text("**Hypothesis**: Continue.", encoding="utf-8")
    os.utime(report, (100, 100))
    os.utime(memory, (101, 101))
    os.utime(selection, (102, 102))

    continued_paths = runner._project_paths(project)
    continued_paths.update(runner._resolve_launch(project, continued_paths))
    runner._hydrate_continuation_config(project, continued_paths)
    continued_config = json.loads(
        continued_paths["config_file"].read_text(encoding="utf-8"),
    )["config"]
    runner._refresh_resume_project_skills(project, continued_paths["config_file"])
    resumed_config = json.loads(
        continued_paths["config_file"].read_text(encoding="utf-8"),
    )["config"]

    canonical = str(code.resolve())
    assert project["code_dir"] == canonical
    assert {
        fresh["working_directory"], fresh["existing_codebase"],
        continued_config["working_directory"], continued_config["existing_codebase"],
        resumed_config["working_directory"], resumed_config["existing_codebase"],
    } == {canonical}
    assert Path(canonical).is_absolute()
    assert ".git" not in Path(canonical).parts
    assert not canonical.startswith(("http://", "https://", "git@"))

    state = runner._state_payload(
        project, continued_paths, tmp_path / "backend", tmp_path / "python",
        status="interrupted",
    )
    persisted_config = json.loads(Path(state["config_file"]).read_text(encoding="utf-8"))["config"]
    assert persisted_config["working_directory"] == canonical


def test_code_tree_is_stat_only_and_exposes_general_code_locations(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    code = project_root / "code"
    empty = code / "src" / "empty"
    empty.mkdir(parents=True)
    source = code / "src" / "analysis.py"
    source.write_text("UNIQUE-SOURCE-CONTENT", encoding="utf-8")
    (code / ".env").write_text("API_TOKEN=not-for-tree", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("outside", encoding="utf-8")
    (code / "escaped.py").symlink_to(outside)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(project_root))

    with app_module.app.test_request_context("/"):
        tree = app_module._round_workspace_code_tree()

    serialized = json.dumps(tree)
    assert tree["name"] == "code"
    assert tree["path"] == ""
    assert tree["status"] == "present"
    assert "analysis.py" in serialized
    assert '"empty"' in serialized
    assert "UNIQUE-SOURCE-CONTENT" not in serialized
    assert ".env" not in serialized
    assert "escaped.py" not in serialized
    source_node = next(
        node
        for node in tree["children"][0]["children"]
        if node["name"] == "analysis.py"
    )
    assert source_node["source_kind"] == "project-code"
    assert source_node["artifact_path"] == "src/analysis.py"


@pytest.mark.parametrize(
    "untrusted",
    ["../other-project/code/secret.py", "%2e%2e/secret.py", "/tmp/secret.py", "..\\secret.py"],
)
def test_code_location_rejects_traversal_and_absolute_injection(
    monkeypatch, tmp_path, untrusted,
):
    project_root = tmp_path / "active"
    (project_root / "code").mkdir(parents=True)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(project_root))
    client = _authenticated_client()

    response = client.get(
        "/api/round_workspace_code_location", query_string={"path": untrusted},
    )
    artifact = client.get(
        "/api/round_workspace_code_artifact", query_string={"path": untrusted},
    )

    assert response.status_code == artifact.status_code == 404


def test_code_location_rejects_decoded_traversal_cross_project_and_symlink_escape(
    monkeypatch, tmp_path,
):
    active_root = tmp_path / "active"
    active_code = active_root / "code"
    other_code = tmp_path / "other" / "code"
    active_code.mkdir(parents=True)
    other_code.mkdir(parents=True)
    outside = other_code / "foreign.py"
    outside.write_text("foreign", encoding="utf-8")
    (active_code / "foreign-link.py").symlink_to(outside)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(active_root))
    client = _authenticated_client()

    decoded = client.get("/api/round_workspace_code_location?path=%2e%2e%2fother%2fcode")
    forged_project = client.get(
        "/api/round_workspace_code_location",
        query_string={"path": "", "project": "other-project"},
    )
    symlink = client.get(
        "/api/round_workspace_code_location", query_string={"path": "foreign-link.py"},
    )
    symlink_artifact = client.get(
        "/api/round_workspace_code_artifact",
        query_string={"path": "foreign-link.py"},
    )

    assert decoded.status_code == 404
    assert symlink.status_code == symlink_artifact.status_code == 404
    assert forged_project.status_code == 404
    assert str(active_code) not in forged_project.get_data(as_text=True)
    assert str(other_code) not in forged_project.get_data(as_text=True)


@pytest.mark.parametrize("relative", [".env", ".hidden/file.py", "secrets/token.txt"])
def test_code_location_rejects_hidden_and_internal_files(monkeypatch, tmp_path, relative):
    project_root = tmp_path / "active"
    selected = project_root / "code" / relative
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text("private", encoding="utf-8")
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(project_root))

    response = _authenticated_client().get(
        "/api/round_workspace_code_location", query_string={"path": relative},
    )

    assert response.status_code == 404


def test_missing_code_root_and_child_are_rejected_without_creation(
    monkeypatch, tmp_path,
):
    project_root = tmp_path / "new-project"
    code = project_root / "code"
    project_root.mkdir()
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(project_root))
    client = _authenticated_client()

    root = client.get("/api/round_workspace_code_location", query_string={"path": ""})
    child = client.get(
        "/api/round_workspace_code_location", query_string={"path": "not-created.py"},
    )

    assert root.status_code == child.status_code == 404
    assert not code.exists()


def test_path_like_html_filename_is_metadata_only_and_content_is_served_safely(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    code = project_root / "code"
    code.mkdir(parents=True)
    unusual = code / "<img onerror=alert(1)>.py"
    unusual.write_text("pass\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(project_root))
    client = _authenticated_client()

    response = client.get(
        "/api/round_workspace_code_location",
        query_string={"path": "<img onerror=alert(1)>.py"},
    )

    assert response.status_code == 200
    assert response.content_type.startswith("application/json")
    assert response.get_json() == {
        "success": True,
        "absolute_path": str(unusual.resolve()),
    }
    artifact = client.get(
        "/api/round_workspace_code_artifact",
        query_string={"path": unusual.name},
    )
    assert artifact.status_code == 200
    assert artifact.get_data(as_text=True) == "pass\n"


def test_code_selection_restores_safe_audit_file_identity_while_round_artifacts_stay_viewable(
    monkeypatch, tmp_path,
):
    project_root = tmp_path / "project"
    code = project_root / "code"
    code.mkdir(parents=True)
    script = code / "analysis.py"
    script.write_text("SILENT-CODE-CONTENT", encoding="utf-8")
    report = project_root / "checkpoint" / "round_report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Visible scientific report\n", encoding="utf-8")
    record = _record(project_root)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: record)

    with app_module.app.test_request_context("/"):
        artifact = app_module._audit_resolve_viewer_artifact({
            "pane": 0,
            "project": "",
            "round": None,
            "sourceKind": "project-code",
            "source": "",
            "path": "code/analysis.py",
            "artifactPath": "analysis.py",
            "reportView": None,
            "figures": [],
        })
        prompt = app_module._build_audit_viewer_context_prompt({
            "version": 1,
            "project": "",
            "artifacts": [{
                "pane": 0,
                "project": "",
                "round": None,
                "sourceKind": "project-code",
                "source": "",
                "path": "code/analysis.py",
                "artifactPath": "analysis.py",
                "reportView": None,
                "figures": [],
            }],
        })

    assert artifact is not None
    assert artifact["artifact_type"] == "code"
    assert artifact["target"] == script.resolve()
    assert artifact["size"] == len("SILENT-CODE-CONTENT")
    assert "shared project code" in prompt
    assert "location identity only" not in prompt
    assert "code/analysis.py" in prompt
    assert "SILENT-CODE-CONTENT" not in prompt

    monkeypatch.setattr(
        app_module,
        "_round_workspace_artifact_path",
        lambda round_index, relative: report if (round_index, relative) == (3, "round_report.md") else None,
    )
    client = _authenticated_client()
    response = client.get(
        "/api/round_workspace_artifact", query_string={"round": 3, "path": "round_report.md"},
    )
    assert response.status_code == 200
    assert response.data == b"# Visible scientific report\n"


@pytest.mark.parametrize(
    ("relative", "payload"),
    [
        ("analysis.py", b"print('PYTHON-CONTENT')\n"),
        ("analysis.R", b"print('R-CONTENT')\n"),
        ("README.md", b"# MARKDOWN-CONTENT\n"),
        ("notes.txt", b"TEXT-CONTENT\n"),
        ("settings.json", b'{"fixture":"JSON-CONTENT"}\n'),
        ("table.csv", b"gene,value\nATF3,7\n"),
        ("figure.png", b"\x89PNG\r\n\x1a\nFIGURE-CONTENT"),
        ("paper.pdf", b"%PDF-1.4\nFIGURE-PDF\n"),
        ("page.html", b"<h1>HTML-CONTENT</h1>"),
        ("analysis.ipynb", b'{"cells":[],"nbformat":4}'),
    ],
)
def test_general_code_files_render_and_download_exact_selected_bytes(
    monkeypatch, tmp_path, relative, payload,
):
    project_root = tmp_path / "project"
    code = project_root / "code"
    code.mkdir(parents=True)
    selected = code / relative
    selected.write_bytes(payload)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: _record(project_root))
    client = _authenticated_client()

    response = client.get(
        "/api/round_workspace_code_artifact", query_string={"path": relative},
    )
    download = client.get(
        "/api/round_workspace_code_artifact",
        query_string={"path": relative, "download": "1"},
    )
    location = client.get(
        "/api/round_workspace_code_location", query_string={"path": relative},
    )

    assert response.status_code == download.status_code == 200
    assert response.data == download.data == payload
    assert download.headers["Content-Disposition"].startswith("attachment;")
    assert f'filename={selected.name}' in download.headers["Content-Disposition"]
    assert location.status_code == 200
    assert location.get_json() == {
        "success": True,
        "absolute_path": str(selected.resolve()),
    }
