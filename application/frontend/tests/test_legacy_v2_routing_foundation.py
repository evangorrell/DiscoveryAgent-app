"""Focused contracts for the first legacy-to-V2 routing foundation batch."""

import json
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    return app_module.app.test_client()


def _create(client, name="Routing Foundation"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Verify compatibility routing.",
            "dataset_context": "Temporary routing test context.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    return response.get_json()["project_id"]


def _managed_project(tmp_path, project_id):
    stored = json.loads((tmp_path / "v2_projects.json").read_text())["projects"]
    record = next(project for project in stored if project["id"] == project_id)
    return app_module._v2_normalize_registered_project_for_resolution(
        record, managed=True, registry_parent=tmp_path,
    )


def _create_round(tmp_path, project_id, round_number):
    project = _managed_project(tmp_path, project_id)
    rounds_root = app_module._v2_round_dirs_root_from_any_checkpoint_path(
        Path(project["checkpoint_dir"]),
    )
    (rounds_root / f"round_{round_number}").mkdir(parents=True, exist_ok=True)


def test_resolver_finds_managed_project_by_slug_and_internal_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)

    by_slug = app_module._v2_resolve_registered_project_context(
        project_slug="routing-foundation",
    )
    by_id = app_module._v2_resolve_registered_project_context(project_id=project_id)

    assert by_slug["id"] == project_id
    assert by_id["url_slug"] == "routing-foundation"


@pytest.mark.parametrize("field", [
    "project_root", "code_dir", "data_dir", "readme_path", "checkpoint_dir",
])
def test_resolver_finds_managed_project_by_relevant_canonical_path(
    monkeypatch, tmp_path, field,
):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    project = _managed_project(tmp_path, project_id)
    value = project[field]
    if field == "checkpoint_dir":
        value = str(Path(value).parent / "round_9")

    resolved = app_module._v2_resolve_registered_project_context(
        path_context={field: value},
    )

    assert resolved["id"] == project_id


def test_resolver_finds_intentionally_registered_legacy_project(monkeypatch, tmp_path):
    managed_root = tmp_path / "managed"
    legacy_root = tmp_path / "legacy"
    project_root = tmp_path / "registered-project"
    legacy_root.mkdir()
    record = {
        "id": "legacy-project-id",
        "url_slug": "registered-legacy-project",
        "display_name": "Registered Legacy Project",
        "project_root": str(project_root),
        "code_dir": str(project_root / "code"),
        "data_dir": str(project_root / "code" / "data"),
        "checkpoint_dir": str(project_root / "checkpoint"),
        "readme_path": str(project_root / "code" / "data" / "README.md"),
    }
    (legacy_root / "v2_projects.json").write_text(
        json.dumps({"projects": [record]}), encoding="utf-8",
    )
    monkeypatch.setattr(app_module, "get_data_root", lambda: managed_root)
    monkeypatch.setattr(app_module, "get_projects_root", lambda: managed_root / "projects")
    monkeypatch.setattr(
        app_module, "get_project_root", lambda project_id: managed_root / "projects" / project_id,
    )
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: legacy_root)

    resolved = app_module._v2_resolve_registered_project_context(
        path_context={
            "checkpoint_dir": project_root / "checkpoint" / "checkpoints" / "round_4",
        },
    )

    assert resolved["id"] == "legacy-project-id"
    assert resolved["url_slug"] == "registered-legacy-project"


def test_resolver_returns_no_match_for_unknown_or_ambiguous_context(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first_id = _create(client, "First Project")
    second_id = _create(client, "Second Project")
    shared_code = tmp_path / "shared-code"
    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text())
    for record in registry["projects"]:
        if record["id"] in {first_id, second_id}:
            record["code_dir"] = str(shared_code)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    assert app_module._v2_resolve_registered_project_context(
        project_slug="does-not-exist",
        path_context={"code_dir": tmp_path / "also-missing"},
    ) is None
    assert app_module._v2_resolve_registered_project_context(
        path_context={"code_dir": shared_code},
    ) is None


def test_resolver_does_not_backfill_migrate_save_or_modify_registry(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    registry_path = tmp_path / "v2_projects.json"
    before = registry_path.read_bytes()
    before_mtime = registry_path.stat().st_mtime_ns

    def unexpected_mutation(*_args, **_kwargs):
        raise AssertionError("read-only resolver invoked a mutating helper")

    monkeypatch.setattr(app_module, "_v2_ensure_url_slugs_backfilled", unexpected_mutation)
    monkeypatch.setattr(app_module, "_maybe_migrate_legacy_interactions", unexpected_mutation)
    monkeypatch.setattr(app_module, "_v2_save_projects", unexpected_mutation)

    assert app_module._v2_resolve_registered_project_context(project_id=project_id)["id"] == project_id
    assert registry_path.read_bytes() == before
    assert registry_path.stat().st_mtime_ns == before_mtime


@pytest.mark.parametrize("round_number", [0, 7])
def test_exact_round_route_preserves_requested_number(monkeypatch, tmp_path, round_number):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    _create_round(tmp_path, project_id, round_number)

    response = client.get(f"/projects/routing-foundation/rounds/{round_number}")

    assert response.status_code == 302
    assert response.headers["Location"] == (
        f"/rounds/?project=routing-foundation&round={round_number}"
    )


def test_exact_round_internal_id_canonicalizes_slug_without_losing_number(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)
    _create_round(tmp_path, project_id, 12)

    response = client.get(f"/projects/{project_id}/rounds/12")

    assert response.status_code == 302
    assert response.headers["Location"] == "/rounds/?project=routing-foundation&round=12"


def test_round_workspace_plain_round_query_selects_exact_round():
    javascript = (
        Path(app_module.app.static_folder) / "round_workspace.js"
    ).read_text(encoding="utf-8")

    assert "const explicitRoundEntry = requestedRoundRaw !== null;" in javascript
    assert "const exactRoundEntry = Number.isInteger(requestedRound) && requestedRound >= 0;" in javascript
    assert "activeRound: explicitRoundEntry ? (exactRoundEntry ? requestedRound : null)" in javascript
    assert "const exactRoundUnavailable = explicitRoundEntry" not in javascript
    assert "unavailable for this project" not in javascript
    assert "if (!explicitRoundEntry && (state.activeRound == null" in javascript


def test_invalid_exact_round_project_does_not_drift_to_active_project(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_id = _create(client)

    response = client.get("/projects/not-a-project/rounds/8")

    assert response.status_code == 404
    assert response.headers.get("Location") is None
    with client.session_transaction() as flask_session:
        assert flask_session["v2_active_project_id"] == project_id


def test_hypothesis_studio_never_falls_back_to_legacy_round_monitor():
    static = Path(app_module.app.static_folder)
    template = Path(app_module.app.template_folder or "templates")
    if not template.is_absolute():
        template = Path(app_module.app.root_path) / template
    javascript = (static / "hypothesis_studio.js").read_text(encoding="utf-8")
    html = (template / "hypothesis_studio.html").read_text(encoding="utf-8")

    assert "'/round_monitor'" not in javascript
    assert '"/round_monitor"' not in javascript
    assert "CFG.projectHomeUrl || CFG.projectsUrl || '/projects'" in javascript
    assert "projectHomeUrl:" in html
    assert "projectsUrl:" in html


def test_legacy_round_monitor_presentation_and_constellation_are_removed():
    static = Path(app_module.app.static_folder)
    template = Path(app_module.app.template_folder or "templates")
    if not template.is_absolute():
        template = Path(app_module.app.root_path) / template
    source = Path(app_module.__file__).read_text(encoding="utf-8")

    assert not (template / "round_monitor.html").exists()
    assert not (static / "round_monitor.js").exists()
    assert not (static / "round_monitor.css").exists()
    assert "def _build_round_constellation(" not in source
    assert "'constellation':" not in source
