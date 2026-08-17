"""Focused coverage for the V2 local-first Add Data project flow."""

import json
from pathlib import Path

from da_selection_ui import app as app_module


PROJECTS_JS = Path(app_module.__file__).resolve().parent / "static" / "projects.js"
PROJECTS_CSS = Path(app_module.__file__).resolve().parent / "static" / "style.css"
PROJECTS_TEMPLATE = Path(app_module.__file__).resolve().parent / "templates" / "projects.html"


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _create(client, name="Local Data", context="Cohort and assay context."):
    response = client.post("/api/projects", data={
        "project_name": name,
        "task_description": "Find robust cohort-specific mechanisms.",
        "dataset_context": context,
    })
    assert response.status_code == 200, response.data
    return response.get_json()


def test_creation_finishes_before_add_data_and_preserves_metadata(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    project_id = created["project_id"]

    assert (tmp_path / project_id / "code" / "data").is_dir()
    record = app_module._v2_get_project(project_id)
    assert record["display_name"] == "Local Data"
    assert record["task_description"] == "Find robust cohort-specific mechanisms."
    assert record["dataset_context"] == "Cohort and assay context."

    # Creating a project is one page and one submit: it lands on Project Home,
    # which shows the data folder's path, instead of a second "Add data" step.
    source = PROJECTS_JS.read_text(encoding="utf-8")
    submit_flow = source[source.index("async function submitForm"):source.index("// ---- wiring")]
    assert "window.location.href = data.destination" in submit_flow
    template = PROJECTS_TEMPLATE.read_text(encoding="utf-8")
    assert 'id="np-add-data-state"' not in template
    assert "Step 1 of 2" not in template
    assert "Step 2 of 2" not in template


def test_project_context_is_canonical_root_data_readme(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client, context="Do not lose this context.")
    project_id = created["project_id"]
    readme = tmp_path / project_id / "code" / "data" / "README.md"

    assert readme.read_text(encoding="utf-8") == "# Local Data\n\nDo not lose this context.\n"
    assert app_module._v2_get_project(project_id)["readme_path"] == str(readme)
    assert not (tmp_path / project_id / "code" / "README.md").exists()


def test_file_manager_commands_are_platform_safe(tmp_path):
    data_dir = tmp_path / "data folder"
    assert app_module._v2_file_manager_command(data_dir, "darwin") == ["open", str(data_dir)]
    assert app_module._v2_file_manager_command(data_dir, "win32") == ["explorer.exe", str(data_dir)]
    assert app_module._v2_file_manager_command(data_dir, "linux") == ["xdg-open", str(data_dir)]


def test_open_data_folder_only_launches_selected_canonical_root(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = _create(client, name="First")
    second = _create(client, name="Second")
    launched = []

    class Process:
        pass

    def fake_popen(command, **kwargs):
        launched.append((command, kwargs))
        return Process()

    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(app_module.sys, "platform", "darwin")

    response = client.post(f"/api/projects/{first['project_id']}/open-data-folder")
    assert response.status_code == 200
    expected = tmp_path / first["project_id"] / "code" / "data"
    assert launched[0][0] == ["open", str(expected.resolve())]
    assert str(tmp_path / second["project_id"]) not in launched[0][0][1]
    assert launched[0][1]["stdout"] is app_module.subprocess.DEVNULL
    assert launched[0][1]["stderr"] is app_module.subprocess.DEVNULL

    registry_path = tmp_path / "v2_projects.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    record = next(p for p in registry["projects"] if p["id"] == first["project_id"])
    record["data_dir"] = "elsewhere"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    rejected = client.post(f"/api/projects/{first['project_id']}/open-data-folder")
    assert rejected.status_code == 500
    assert len(launched) == 1


def test_nested_files_appear_on_later_rescan_without_touching_readme(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    project_id = created["project_id"]
    data_dir = tmp_path / project_id / "code" / "data"
    readme = data_dir / "README.md"

    initial = client.get(f"/api/projects/{project_id}/files").get_json()["files"]
    assert initial == [{"name": "README.md", "size": readme.stat().st_size, "managed": True}]

    readme.write_text("Scientist replaced this manually.\n", encoding="utf-8")
    (data_dir / "cohort_a" / "docs").mkdir(parents=True)
    (data_dir / "cohort_a" / "cells.h5ad").write_bytes(b"tiny")
    (data_dir / "cohort_a" / "docs" / "README.md").write_text("nested", encoding="utf-8")
    (data_dir / "cohort_b.h5ad").write_bytes(b"tiny-b")

    rescanned = client.get(f"/api/projects/{project_id}/files").get_json()["files"]
    assert [item["name"] for item in rescanned] == [
        "cohort_a/cells.h5ad",
        "cohort_a/docs/README.md",
        "cohort_b.h5ad",
        "README.md",
    ]
    assert next(item for item in rescanned if item["name"] == "README.md")["managed"] is True
    assert next(item for item in rescanned if item["name"] == "cohort_a/docs/README.md").get("managed") is None
    assert readme.read_text(encoding="utf-8") == "Scientist replaced this manually.\n"


def test_external_file_removal_disappears_on_next_rescan(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    project_id = created["project_id"]
    data_dir = tmp_path / project_id / "code" / "data"
    external_file = data_dir / "removed-in-finder.csv"
    external_file.write_text("sample,value\nA,1\n", encoding="utf-8")

    before = client.get(f"/api/projects/{project_id}/files").get_json()["files"]
    assert "removed-in-finder.csv" in {item["name"] for item in before}

    external_file.unlink()

    after = client.get(f"/api/projects/{project_id}/files").get_json()["files"]
    assert "removed-in-finder.csv" not in {item["name"] for item in after}
    assert next(item for item in after if item["name"] == "README.md")["managed"] is True


def test_file_manager_hidden_path_components_are_excluded_from_data_inventory(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    project_id = created["project_id"]
    data_dir = tmp_path / project_id / "code" / "data"

    (data_dir / ".DS_Store").write_bytes(b"finder metadata")
    (data_dir / "visible.csv").write_text("sample,value\nA,1\n", encoding="utf-8")
    (data_dir / "cohort" / ".index").mkdir(parents=True)
    (data_dir / "cohort" / ".index" / "cache.json").write_text("{}", encoding="utf-8")
    (data_dir / "cohort" / ".notes.txt").write_text("hidden", encoding="utf-8")
    (data_dir / ".cache" / "visible-name.csv").parent.mkdir(parents=True)
    (data_dir / ".cache" / "visible-name.csv").write_text("hidden", encoding="utf-8")
    (data_dir / "~$Short Answers.docx").write_bytes(b"office lock")
    (data_dir / "cohort" / "~$samples.xlsx").write_bytes(b"office lock")

    files = client.get(f"/api/projects/{project_id}/files").get_json()["files"]
    names = {item["name"] for item in files}

    assert names == {"README.md", "visible.csv"}
    assert all(not any(part.startswith(".") for part in Path(name).parts) for name in names)
    assert all(not any(part.startswith("~$") for part in Path(name).parts) for name in names)


def test_platform_hidden_metadata_is_excluded_from_data_inventory(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    project_id = created["project_id"]
    data_dir = tmp_path / project_id / "code" / "data"
    finder_hidden = data_dir / "finder-hidden.csv"
    finder_hidden.write_text("hidden", encoding="utf-8")
    visible = data_dir / "visible.csv"
    visible.write_text("visible", encoding="utf-8")

    monkeypatch.setattr(app_module.sys, "platform", "darwin")

    def fake_getxattr(path, attribute):
        assert attribute == "com.apple.FinderInfo"
        if Path(path) == finder_hidden:
            return b"\0" * 8 + b"\x40\0" + b"\0" * 22
        raise OSError("attribute absent")

    monkeypatch.setattr(app_module.os, "getxattr", fake_getxattr, raising=False)

    files = client.get(f"/api/projects/{project_id}/files").get_json()["files"]
    names = {item["name"] for item in files}

    assert names == {"README.md", "visible.csv"}


def test_primary_create_uses_no_browser_multipart_upload():
    source = PROJECTS_JS.read_text(encoding="utf-8")
    assert "const body = new URLSearchParams()" in source
    assert "new FormData" not in source
    assert "uploadSelectedFiles" not in source
    template = PROJECTS_TEMPLATE.read_text(encoding="utf-8")
    assert 'type="file"' not in template


def test_continue_route_still_reaches_project_home_without_duplicate_creation(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    response = client.post(f"/api/projects/{created['project_id']}/continue")

    assert response.status_code == 200
    assert response.get_json()["destination"] == created["destination"] == "/projects/local-data"
    projects = json.loads((tmp_path / "v2_projects.json").read_text(encoding="utf-8"))["projects"]
    assert len(projects) == 1


def test_create_modal_is_one_page_that_asks_where_the_data_lives():
    template = PROJECTS_TEMPLATE.read_text(encoding="utf-8")
    source = PROJECTS_JS.read_text(encoding="utf-8")

    assert '<h2 class="v2-modal-title" id="np-title">Create a new DiscoveryAgent project</h2>' in template
    assert 'id="np-cancel">Cancel</button>' in template
    # One footer, one submit: the second step and its footer are gone.
    assert template.count('class="v2-modal-footer"') == 1
    assert 'id="np-data-footer"' not in template
    assert 'id="np-continue-project"' not in template
    assert 'id="np-data-back"' not in template
    assert "backToProjectDetails" not in source
    assert "continueToProjectHome" not in source
    assert "showAddDataState" not in source

    # Name, objective, context and data folder are all on the create page, in
    # that order.
    create_page = template[template.index('id="np-project-state"'):template.index('id="np-edit-data-field"')]
    assert create_page.index('id="np-name"') < create_page.index('id="np-objective"')
    assert create_page.index('id="np-objective"') < create_page.index('id="np-context"')
    assert create_page.index('id="np-context"') < create_page.index('id="np-data-folder"')
    assert 'id="np-data-folder-choose"' in create_page
    assert "DATA FOLDER" in create_page
    assert "nothing in it is copied, moved, renamed, or deleted" in create_page
    assert "Leave this blank" in create_page

    submit_flow = source[source.index("async function submitForm"):source.index("// ---- wiring")]
    assert "dataset_context" in submit_flow
    assert "body.append('data_folder', els.dataFolder.value.trim())" in submit_flow
    assert "els.title.textContent = isEdit" in source
    assert "els.dataFolderChoose.addEventListener('click', chooseDataFolder)" in source


def test_editing_a_project_shows_its_data_path_with_a_copy_action():
    template = PROJECTS_TEMPLATE.read_text(encoding="utf-8")
    source = PROJECTS_JS.read_text(encoding="utf-8")

    assert 'id="np-edit-data-path"' in template
    assert 'id="np-edit-copy-data-path"' in template
    assert "Copy path" in template
    assert "els.editCopyDataPath.addEventListener('click', copyEditDataPath)" in source
    assert "renderEditDataFolder(data.project.data_folder)" in source


def test_closing_the_create_modal_before_submitting_creates_nothing(monkeypatch, tmp_path):
    _client(monkeypatch, tmp_path)
    source = PROJECTS_JS.read_text(encoding="utf-8")
    close_flow = source[source.index("function closeModal()"):source.index("function setFormError")]

    assert "fetch(" not in close_flow
    assert not (tmp_path / "v2_projects.json").exists()


def test_create_page_keeps_one_stable_modal_scroll_boundary():
    css = PROJECTS_CSS.read_text(encoding="utf-8")
    template = PROJECTS_TEMPLATE.read_text(encoding="utf-8")
    source = PROJECTS_JS.read_text(encoding="utf-8")

    assert 'class="v2-modal v2-project-modal-shell"' in template
    shell_start = css.index(".v2-project-modal-shell {")
    create_shell = css[shell_start:css.index(".v2-modal-head {", shell_start)]
    assert "width: min(800px, calc(100vw - 48px))" in create_shell
    assert "height: min(870px, calc(100dvh - 48px))" in create_shell
    project_header = css[css.index(".v2-project-modal-shell > .v2-modal-head {"):css.index("}", css.index(".v2-project-modal-shell > .v2-modal-head {"))]
    assert "flex: 0 0 auto" in project_header
    assert "padding-bottom: 24px" in project_header
    project_close = css[css.index(".v2-project-modal-shell > .v2-modal-head .v2-modal-close {"):css.index("}", css.index(".v2-project-modal-shell > .v2-modal-head .v2-modal-close {"))]
    assert "position: absolute" in project_close
    assert "right: 20px" in project_close
    assert "width: 36px" in project_close
    assert "padding: 0" in project_close
    modal_body = css[css.index(".v2-modal-body {"):css.index(".v2-modal-copy")]
    assert "overflow-y: auto" in modal_body
    project_body = css[css.index(".v2-project-modal-shell > .v2-modal-body {"):css.index("}", css.index(".v2-project-modal-shell > .v2-modal-body {"))]
    assert "flex: 1 1 auto" in project_body
    assert "min-height: 0" in project_body
    assert "padding-top: 30px" in project_body
    step_one_form = css[css.index("#np-project-state > .v2-modal-form {"):css.index("}", css.index("#np-project-state > .v2-modal-form {"))]
    assert "padding: 0" in step_one_form
    assert "margin-top: 0" in step_one_form
    section_label = css[css.index(".v2-section-label {"):css.index("}", css.index(".v2-section-label {"))]
    modal_section_label = css[css.index("#np-edit-data-field > .v2-modal-section-label {"):css.index("}", css.index("#np-edit-data-field > .v2-modal-section-label {"))]
    assert "text-transform: uppercase" in section_label
    assert "letter-spacing: 0.14em" in section_label
    assert "font-family: var(--v2-sans)" in section_label
    assert "margin: 0 0 10px" in modal_section_label
    field_help = css[css.index(".v2-field-help {"):css.index("}", css.index(".v2-field-help {"))]
    assert "font-size: 0.85rem" in field_help
    assert "color: var(--v2-muted)" in field_help
    # The removed second step leaves no orphan rules behind.
    assert "#np-add-data-state" not in css
    assert ".v2-add-data" not in css
    project_footer = css[css.index(".v2-project-modal-shell > .v2-modal-footer {"):css.index("}", css.index(".v2-project-modal-shell > .v2-modal-footer {"))]
    assert "flex: 0 0 auto" in project_footer
    assert "align-items: center" in project_footer
    assert "padding-top: 18px" in project_footer
    assert "padding-bottom: 18px" in project_footer
    assert "margin-top: 0" in project_footer
    assert "justify-content" not in project_footer

    data_scan_section = css.index("/* ---- Local-first Add Data state ---- */")
    data_folder_start = css.index(".v2-data-scan {", data_scan_section)
    data_folder = css[data_folder_start:css.index(".v2-data-scan-head {", data_folder_start)]
    assert "overflow" not in data_folder
    file_list = next(line for line in css.splitlines() if line.startswith(".v2-file-list "))
    assert "overflow" not in file_list

    body = template[template.index('<div class="v2-modal-body" id="np-modal-body">'):template.index('<div class="v2-modal-footer" id="np-project-footer">')]
    assert 'id="np-project-state"' in body
    assert "els.modal.style" not in source
    assert 'id="np-refresh-data"' not in template
    assert "dataScanTimer = window.setInterval(rescanDataFolder, 2000)" in source


def test_project_modal_always_opens_at_the_top_of_its_scroll_boundary():
    template = PROJECTS_TEMPLATE.read_text(encoding="utf-8")
    source = PROJECTS_JS.read_text(encoding="utf-8")

    assert 'class="v2-modal-body" id="np-modal-body"' in template
    assert "modalBody: $('np-modal-body')" in source
    reset_start = source.index("function resetProjectModalScroll()")
    reset_rule = source[reset_start:source.index("function focusProjectNameAtTop", reset_start)]
    show_start = source.index("function showModal()")
    show_rule = source[show_start:source.index("function closeModal()", show_start)]

    assert "els.overlay.scrollTop = 0" in reset_rule
    assert "els.modalBody.scrollTop = 0" in reset_rule
    assert "els.name.focus({ preventScroll: true })" in source
    assert show_rule.count("resetProjectModalScroll()") == 2
    assert "setTimeout(focusProjectNameAtTop, 30)" in show_rule
    assert "projects.js') }}?v=20260808-edit-scroll-top-1" in template


def test_closing_the_modal_never_removes_a_created_project(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    project_id = created["project_id"]

    # Closing is a client-only dismissal: it stops polling and nothing else. It
    # must not call a delete/rollback route.
    source = PROJECTS_JS.read_text(encoding="utf-8")
    close_flow = source[source.index("function closeModal()"):source.index("function setFormError")]
    assert "stopDataPolling()" in close_flow
    assert "DELETE" not in close_flow

    projects = json.loads((tmp_path / "v2_projects.json").read_text(encoding="utf-8"))["projects"]
    assert len(projects) == 1
    assert projects[0]["id"] == project_id
    assert (tmp_path / project_id / "code" / "data" / "README.md").is_file()


def test_editing_updates_the_existing_project_instead_of_creating_another(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _create(client)
    project_id = created["project_id"]

    response = client.post(f"/api/projects/{project_id}/edit", data={
        "project_name": "Revised Local Data",
        "task_description": "Use the revised objective.",
        "dataset_context": "Revised cohort context.",
    })
    assert response.status_code == 200
    projects = json.loads((tmp_path / "v2_projects.json").read_text(encoding="utf-8"))["projects"]
    assert len(projects) == 1
    assert projects[0]["id"] == project_id
    assert projects[0]["display_name"] == "Revised Local Data"
