"""Server-side contracts for exact Round Workspace resources."""

import json
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _project_record(tmp_path: Path) -> dict:
    project_root = tmp_path / "optic-project"
    code_dir = project_root / "code"
    data_dir = code_dir / "data"
    checkpoint_dir = project_root / "checkpoint"
    data_dir.mkdir(parents=True)
    checkpoint_dir.mkdir(parents=True)
    readme = data_dir / "README.md"
    readme.write_text("# Optic Nerve Regeneration", encoding="utf-8")
    return {
        "id": "optic-project",
        "url_slug": "optic-nerve-regeneration",
        "display_name": "Optic Nerve Regeneration",
        "project_root": str(project_root),
        "code_dir": str(code_dir),
        "data_dir": str(data_dir),
        "checkpoint_dir": str(checkpoint_dir),
        "readme_path": str(readme),
    }


def _client(monkeypatch, tmp_path, rounds=(0, 1, 2, 3, 4)):
    record = _project_record(tmp_path)
    registry = tmp_path / "v2_projects.json"
    registry.write_text(json.dumps({"projects": [record]}), encoding="utf-8")
    rounds_root = Path(record["checkpoint_dir"]) / "checkpoints"
    for round_number in rounds:
        (rounds_root / f"round_{round_number}").mkdir(parents=True)
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    return app_module.app.test_client(), registry


def _assert_standard_v2_404(response):
    html = response.get_data(as_text=True)
    assert response.status_code == 404
    assert 'class="v2-error-page"' in html
    assert 'id="error-code">404<' in html
    assert 'href="/projects">Return to Projects<' in html
    assert 'id="round-workspace-root"' not in html
    assert "unavailable for this project" not in html


def test_explicit_valid_round_renders_exact_round(monkeypatch, tmp_path):
    client, _registry = _client(monkeypatch, tmp_path)

    response = client.get(
        "/rounds/?project=optic-nerve-regeneration&round=3"
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="round-workspace-root"' in html
    assert "pinnedRound: 3" in html
    assert "round_3" in html
    with client.session_transaction() as flask_session:
        assert flask_session["v2_active_project_slug"] == "optic-nerve-regeneration"


@pytest.mark.parametrize("round_value", ["6", "missing", "-1", "4.0", ""])
def test_explicit_missing_or_malformed_round_is_standard_404(
    monkeypatch, tmp_path, round_value,
):
    client, registry = _client(monkeypatch, tmp_path)
    registry_before = registry.read_bytes()
    with client.session_transaction() as flask_session:
        active_before = (
            flask_session.get("v2_active_project_id"),
            flask_session.get("v2_active_project_slug"),
            flask_session.get("project_config"),
        )

    response = client.get(
        "/rounds/",
        query_string={"project": "optic-nerve-regeneration", "round": round_value},
    )

    _assert_standard_v2_404(response)
    assert registry.read_bytes() == registry_before
    with client.session_transaction() as flask_session:
        assert (
            flask_session.get("v2_active_project_id"),
            flask_session.get("v2_active_project_slug"),
            flask_session.get("project_config"),
        ) == active_before


def test_round_omission_preserves_default_workspace_selection(monkeypatch, tmp_path):
    client, _registry = _client(monkeypatch, tmp_path)

    response = client.get("/rounds/?project=optic-nerve-regeneration")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="round-workspace-root"' in html
    assert "pinnedRound: 4" in html
    assert "round_4" in html


def test_unknown_explicit_project_is_404_even_with_an_active_project(
    monkeypatch, tmp_path,
):
    client, registry = _client(monkeypatch, tmp_path)
    valid = client.get("/projects/optic-nerve-regeneration")
    assert valid.status_code == 200
    registry_before = registry.read_bytes()

    response = client.get("/rounds/?project=definitely-not-real")

    _assert_standard_v2_404(response)
    assert response.headers.get("Location") is None
    assert registry.read_bytes() == registry_before
    with client.session_transaction() as flask_session:
        assert flask_session["v2_active_project_slug"] == "optic-nerve-regeneration"


def test_round_workspace_without_project_context_is_not_found(monkeypatch, tmp_path):
    client, _registry = _client(monkeypatch, tmp_path)

    response = client.get("/rounds/")

    _assert_standard_v2_404(response)


@pytest.mark.parametrize("prefix", ["/projects", "/legacy/projects"])
def test_numbered_compatibility_route_rejects_missing_round(
    monkeypatch, tmp_path, prefix,
):
    client, registry = _client(monkeypatch, tmp_path)
    registry_before = registry.read_bytes()

    response = client.get(
        f"{prefix}/optic-nerve-regeneration/rounds/6"
    )

    _assert_standard_v2_404(response)
    assert response.headers.get("Location") is None
    assert registry.read_bytes() == registry_before


@pytest.mark.parametrize(
    "path",
    [
        "/round_monitor?project=optic-nerve-regeneration&round=6",
        "/checkpoint_browser?project=optic-nerve-regeneration&round=6",
        "/report?type=round&project=optic-nerve-regeneration&round=6",
    ],
)
def test_retired_generic_round_entry_is_direct_404_without_drift(
    monkeypatch, tmp_path, path,
):
    client, _registry = _client(monkeypatch, tmp_path)

    response = client.get(path, follow_redirects=True)

    _assert_standard_v2_404(response)
    assert response.headers.get("Location") is None
    assert response.request.args.get("round") == "6"


def test_invalid_project_with_explicit_round_preserves_v2_404(monkeypatch, tmp_path):
    client, _registry = _client(monkeypatch, tmp_path)

    for path in (
        "/rounds/?project=does-not-exist&round=3",
        "/projects/does-not-exist/rounds/3",
        "/legacy/projects/does-not-exist/rounds/3",
    ):
        _assert_standard_v2_404(client.get(path))
