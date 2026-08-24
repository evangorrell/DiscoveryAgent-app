"""A project may read a data folder the scientist chose and already owns.

Every test here is really one property: DiscoveryAgent reads that folder and
never changes it. Creation, editing, project deletion, and failed creation
cleanup all have to leave the scientist's files exactly as they were.
"""

import json
from pathlib import Path

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    # Managed projects live below their own root, as they do in production, so
    # a folder elsewhere under tmp_path genuinely is the scientist's own.
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: projects_root)
    monkeypatch.setattr(app_module, "get_projects_root", lambda: projects_root)
    monkeypatch.setattr(
        app_module, "get_project_root", lambda project_id: projects_root / project_id
    )
    trash = tmp_path / ".test-trash"

    def move_to_test_trash(target, platform_name=None):
        trash.mkdir(exist_ok=True)
        target.replace(trash / target.name)

    monkeypatch.setattr(app_module, "_v2_move_to_trash", move_to_test_trash)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _scientists_folder(tmp_path, *, name="lab data"):
    folder = tmp_path / name
    folder.mkdir(parents=True)
    (folder / "counts.csv").write_text("gene,count\nSOX2,42\n", encoding="utf-8")
    (folder / "notes").mkdir()
    (folder / "notes" / "protocol.md").write_text("# Protocol\n", encoding="utf-8")
    return folder


def _snapshot(folder: Path) -> dict[str, str]:
    return {
        str(path.relative_to(folder)): path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*")) if path.is_file()
    }


def _create(client, *, data_folder=None, name="Chosen Folder", context="Cohort context."):
    payload = {
        "project_name": name,
        "task_description": "Find robust mechanisms.",
        "dataset_context": context,
    }
    if data_folder is not None:
        payload["data_folder"] = str(data_folder)
    response = client.post("/api/projects", data=payload)
    return response


def test_chosen_folder_is_linked_in_place_and_left_untouched(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    before = _snapshot(folder)

    created = _create(client, data_folder=folder)
    assert created.status_code == 200, created.data
    record = app_module._v2_get_project(created.get_json()["project_id"])

    assert record["external_data"] is True
    assert record["data_dir"] == str(folder.resolve())
    link = Path(record["code_dir"]) / "data"
    assert link.is_symlink()
    assert link.resolve() == folder.resolve()
    # Nothing was copied in, moved out, or rewritten.
    assert _snapshot(folder) == before


def test_project_context_is_written_into_the_project_not_the_chosen_folder(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    existing_readme = folder / "README.md"
    existing_readme.write_text("My own notes, please keep.\n", encoding="utf-8")

    created = _create(client, data_folder=folder, context="DiscoveryAgent context.")
    record = app_module._v2_get_project(created.get_json()["project_id"])

    managed = Path(record["code_dir"]) / "README.md"
    assert record["readme_path"] == str(managed)
    assert managed.read_text(encoding="utf-8").endswith("DiscoveryAgent context.\n")
    assert existing_readme.read_text(encoding="utf-8") == "My own notes, please keep.\n"


def test_a_readme_in_the_chosen_folder_is_listed_as_ordinary_data(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    (folder / "README.md").write_text("Dataset documentation.\n", encoding="utf-8")

    created = _create(client, data_folder=folder)
    project_id = created.get_json()["project_id"]
    listed = client.get(f"/api/projects/{project_id}").get_json()["project"]["files"]

    assert "README.md" in {entry["name"] for entry in listed}


def test_clearing_project_context_never_deletes_the_scientists_readme(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    (folder / "README.md").write_text("Irreplaceable.\n", encoding="utf-8")

    created = _create(client, data_folder=folder, context="Managed context.")
    project_id = created.get_json()["project_id"]
    record = app_module._v2_get_project(project_id)
    managed = Path(record["code_dir"]) / "README.md"
    assert managed.is_file()

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Chosen Folder",
        "task_description": "Find robust mechanisms.",
        "dataset_context": "",
    })

    assert response.status_code == 200
    assert not managed.exists()
    assert (folder / "README.md").read_text(encoding="utf-8") == "Irreplaceable.\n"


def test_deleting_the_project_leaves_the_chosen_folder_in_place(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    before = _snapshot(folder)

    created = _create(client, data_folder=folder)
    project_id = created.get_json()["project_id"]
    response = client.delete(f"/api/projects/{project_id}")

    assert response.status_code == 200
    assert folder.is_dir()
    assert _snapshot(folder) == before


def test_failed_creation_cleanup_removes_only_the_project(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    before = _snapshot(folder)

    def explode(*_args, **_kwargs):
        raise RuntimeError("synthetic failure after the folder was linked")

    monkeypatch.setattr(app_module, "write_project_metadata", explode)
    response = _create(client, data_folder=folder)

    assert response.status_code == 500
    assert folder.is_dir()
    assert _snapshot(folder) == before


def test_unusable_folders_are_refused_before_a_project_exists(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    a_file = tmp_path / "counts.csv"
    a_file.write_text("not a folder", encoding="utf-8")

    refusals = {
        "missing": str(tmp_path / "nope"),
        "file": str(a_file),
        "relative": "relative/path",
        "home": str(Path.home()),
    }
    for _label, candidate in refusals.items():
        response = _create(client, data_folder=candidate)
        assert response.status_code == 400, candidate
        assert response.get_json()["error"]

    assert list((tmp_path / "projects").iterdir()) == []
    assert a_file.read_text(encoding="utf-8") == "not a folder"


def test_a_folder_inside_the_managed_projects_root_is_refused(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    managed = _create(client, name="Managed", data_folder=None)
    assert managed.status_code == 200
    inside = Path(app_module._v2_get_project(managed.get_json()["project_id"])["data_dir"])

    response = _create(client, name="Second", data_folder=inside)

    assert response.status_code == 400
    assert "manages" in response.get_json()["error"]


def test_inspect_endpoint_lists_a_candidate_folder_and_refuses_bad_ones(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)

    good = client.post("/api/data-folder/inspect", json={"path": str(folder)})
    assert good.status_code == 200
    payload = good.get_json()
    assert payload["path"] == str(folder.resolve())
    assert {entry["name"] for entry in payload["files"]} == {"counts.csv", "notes/protocol.md"}

    bad = client.post("/api/data-folder/inspect", json={"path": str(tmp_path / "absent")})
    assert bad.status_code == 400
    assert bad.get_json()["success"] is False
    assert _snapshot(folder)  # the inspection changed nothing


def test_open_data_folder_targets_the_chosen_folder(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    created = _create(client, data_folder=folder)
    record = app_module._v2_get_project(created.get_json()["project_id"])

    assert app_module._v2_canonical_data_dir(record) == folder.resolve()


def test_a_moved_away_folder_is_reported_rather_than_silently_ignored(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    created = _create(client, data_folder=folder)
    record = app_module._v2_get_project(created.get_json()["project_id"])

    folder.rename(tmp_path / "moved elsewhere")

    payload = app_module._v2_data_folder_payload(record)
    assert payload["available"] is False
    assert payload["external"] is True


def test_registry_records_survive_a_reload(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    folder = _scientists_folder(tmp_path)
    created = _create(client, data_folder=folder)
    project_id = created.get_json()["project_id"]

    registry = json.loads(
        (app_module._v2_projects_root().parent / "v2_projects.json").read_text(encoding="utf-8")
    )
    stored = next(entry for entry in registry["projects"] if entry["id"] == project_id)

    assert stored["external_data"] is True
    assert stored["data_dir"] == str(folder.resolve())
