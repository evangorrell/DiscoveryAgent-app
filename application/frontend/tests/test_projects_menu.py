"""Focused tests for the project ⋮ menu (Edit + ownership-aware removal).

Every project is editable. Managed projects retain Delete while imported
projects use Remove Project. The pytest suite has no JS runtime, so the menu
construction contract is locked deterministically from projects.js source.
(Live DOM/interaction is additionally verified with headless Chrome during
manual visual verification.)"""
from pathlib import Path

from da_selection_ui import app as app_module

JS = Path(app_module.__file__).resolve().parent / "static" / "projects.js"


def _src():
    return JS.read_text()


def _buildmenu_block():
    s = _src()
    start = s.index("function buildMenu(")
    end = s.index("\n    function ", start + 10)
    return s[start:end]


# --- V2 menu contains Edit and the ownership-appropriate removal action ------

def test_menu_has_edit_and_ownership_appropriate_removal_without_settings():
    block = _buildmenu_block()
    assert "menuItem('Edit'" in block
    assert "project.imported_in_place ? 'Remove Project' : 'Delete'" in block
    assert "dangerMenuItem(" in block
    assert "menuItem('Settings'" not in block
    assert block.index("menuItem('Edit'") < block.index("dangerMenuItem(")


# --- Edit + removal are both unconditional (no editability branch) -----------

def test_edit_and_removal_are_unconditional():
    block = _buildmenu_block()
    edit = block.index("menuItem('Edit'")
    removal = block.index("dangerMenuItem(")
    assert removal > edit
    assert "if (project.editable)" not in block  # no editability branch remains


# --- menu is shown for every row --------------------------------------------

def test_menu_rendered_for_all_rows():
    s = _src()
    assert "head.appendChild(buildMenu(p))" in s
    assert "if (p.editable) head.appendChild(buildMenu" not in s


def test_settings_routes_are_absent():
    rules = {r.rule for r in app_module.app.url_map.iter_rules()}
    assert not any(rule.startswith("/settings/") for rule in rules)
    assert not any(rule.startswith("/api/settings/") for rule in rules)


def test_edit_uses_the_same_unified_data_selection_as_create():
    source = _src()
    template = JS.parent.parent / "templates" / "projects.html"
    markup = template.read_text()
    assert markup.count('id="np-data-field"') == 1
    assert 'class="v2-data-action v2-project-data-action" id="np-select-data"' in markup
    assert "Select the data this project will have access to." in markup
    assert "No data selected" in markup
    assert "Click anywhere to select data" not in markup
    assert "Click to select data" in markup
    assert 'id="np-data-library-add-data"' in markup
    assert '<a class="v2-data-action" id="np-data-library-add-data"' in markup
    assert """url_for('discoveryagent_data', open_data_picker='1')""" in markup
    assert "to use it in a project." not in markup
    assert "Click to browse DiscoveryAgent Data" not in markup
    assert "Open Data Folder" not in markup
    assert "Copy path" not in markup
    assert "Drop files here" not in markup
    assert 'type="file"' not in markup
    assert "new FormData" not in source
    assert "uploadSelectedFiles" not in source


def test_edit_restores_library_selection_without_scanning_project_data_folder():
    source = _src()
    edit = source[source.index("async function openEdit"):source.index("function openDeleteConfirmation")]
    assert "selectedDataSources = inputData.selection || []" in edit
    assert "renderSelectedDataHierarchy()" in edit
    assert "await loadLibrarySources()" in edit
    assert "knownDataSourceNames" not in source
    assert "rememberDataPathKind" not in source
    assert "selectedTreeModel" not in source
    assert "projectDataTree.renderSources" in source
    assert "fetchSelectedPreviewPage" in source
    assert "open-data-folder" not in source
    assert "rescanDataFolder" not in source


def test_edit_hides_create_only_step_eyebrow():
    source = _src()
    template = JS.parent.parent / "templates" / "projects.html"
    markup = template.read_text()
    apply_mode = source[source.index("function applyMode()"):source.index("function resetSubmitState()")]

    assert 'id="np-project-details-heading"' not in markup
    assert "PROJECT DETAILS" not in markup
    assert 'id="np-project-details-intro"' not in markup
    assert "Tell DiscoveryAgent" not in markup
    assert "const isEdit = modalMode === 'edit'" in apply_mode
    assert "projectDetailsHeading" not in source
    assert "projectDetailsIntro" not in source
    assert "els.title.textContent = isEdit" in apply_mode


def test_reopening_project_modal_clears_stale_submit_state():
    source = _src()
    reset = source[source.index("function resetSubmitState()"):source.index("function openCreate()")]
    assert "projectSubmissionRunning = false" in reset
    assert "els.submit.disabled = false" in reset
    assert "els.submit.classList.remove('is-busy')" in reset
    assert "applyMode()" in reset

    open_create = source[source.index("function openCreate()"):source.index("async function openEdit")]
    open_edit = source[source.index("async function openEdit"):source.index("function openDeleteConfirmation")]
    assert "resetSubmitState()" in open_create
    assert "resetSubmitState()" in open_edit


def test_successful_edit_resets_submit_before_closing_modal():
    source = _src()
    start = source.index("if (modalMode === 'edit') {", source.index("async function persistProject"))
    success = source[start:source.index("if (typeof data.projects_url", start)]
    assert success.index("resetSubmitState()") < success.index("closeModal({ force: true })")


def test_normal_project_ui_has_no_browser_upload_worker():
    s = _src()
    assert "const body = new URLSearchParams()" in s
    assert "new FormData" not in s
    assert "uploadSelectedFiles" not in s
    assert "Uploading 0 of " not in s


# --- listing carries no editability/legacy branch fields --------------------

def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    return client


def test_list_has_no_editable_or_legacy_fields(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    client.post("/api/projects", data={
        "project_name": "P",
        "task_description": "Identify testable regeneration hypotheses.",
        "dataset_context": "c",
    },
                content_type="multipart/form-data")
    rows = client.get("/api/projects").get_json()["projects"]
    assert rows and all("editable" not in r and "is_legacy" not in r for r in rows)
