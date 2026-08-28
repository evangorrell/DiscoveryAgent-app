"""Focused tests for Projects-list sort order: most recently viewed/opened
first. last_opened_at is stamped at creation and re-stamped on every
/continue, independent of the metadata-edit-driven updated_at used for the
"Updated ..." display line. Temporary filesystem roots only; no live LLM, no S3."""
import json

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    return client


def _create(client, name):
    resp = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Identify testable regeneration hypotheses.",
            "dataset_context": "Some dataset context.",
        },
        content_type="multipart/form-data",
    )
    return resp.get_json()["project_id"]


def _continue(client, pid):
    return client.post(f"/api/projects/{pid}/continue")


def _list_ids(client):
    payload = client.get("/api/projects").get_json()
    return [p["id"] for p in payload["projects"]]


def _registry(tmp_path):
    return json.loads((tmp_path / "projects.json").read_text())["projects"]


# --- (1) newest creation sorts first -----------------------------------------

def test_most_recently_created_project_sorts_first(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = _create(client, "First Project")
    second = _create(client, "Second Project")
    assert _list_ids(client) == [second, first]


# --- (2) opening an older project bumps it to the top ------------------------

def test_opening_older_project_moves_it_to_top(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = _create(client, "First Project")
    second = _create(client, "Second Project")
    assert _list_ids(client) == [second, first]  # second is newest so far

    _continue(client, first)  # re-open the older project
    assert _list_ids(client) == [first, second]


# --- (3) last_opened_at is actually persisted --------------------------------

def test_last_opened_at_persisted_on_create_and_continue(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client, "Solo Project")
    record = next(p for p in _registry(tmp_path) if p["id"] == pid)
    created_stamp = record["last_opened_at"]
    assert created_stamp  # stamped at creation

    _continue(client, pid)
    record = next(p for p in _registry(tmp_path) if p["id"] == pid)
    assert record["last_opened_at"] >= created_stamp


# --- (4) registries without last_opened_at (pre-existing data) still sort ----

def test_missing_last_opened_at_falls_back_to_created_at(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    older = _create(client, "Older Project")
    newer = _create(client, "Newer Project")

    # Simulate a pre-existing registry entry written before this field existed.
    reg_path = tmp_path / "projects.json"
    data = json.loads(reg_path.read_text())
    for p in data["projects"]:
        p.pop("last_opened_at", None)
    reg_path.write_text(json.dumps(data))

    assert _list_ids(client) == [newer, older]


# --- (5) editing metadata does not affect view/open sort order ---------------

def test_editing_metadata_does_not_bump_open_order(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = _create(client, "First Project")
    second = _create(client, "Second Project")
    assert _list_ids(client) == [second, first]

    client.post(
        f"/api/projects/{first}/edit",
        data={
            "project_name": "First Project Renamed",
            "task_description": "Identify testable regeneration hypotheses.",
            "dataset_context": "Updated context.",
        },
        content_type="multipart/form-data",
    )
    # Editing changes updated_at (for the "Updated ..." label) but must not
    # reorder the list, since that is driven by last_opened_at only.
    assert _list_ids(client) == [second, first]
