from pathlib import Path

import pytest

from da_selection_ui import app as app_module


def test_shared_round_start_asset_precedes_each_surface_client():
    templates = Path(app_module.__file__).resolve().parent / "templates"
    surfaces = {
        "project_home.html": "project_home.js",
        "hypothesis_studio.html": "hypothesis_studio.js",
        "round_workspace.html": "round_workspace.js",
    }

    base = (templates / "base.html").read_text(encoding="utf-8")
    shared = "filename='round_start.js'"
    assert base.index(shared) < base.index("{% block scripts %}")
    assert base.count(shared) == 1
    for template_name, surface_asset in surfaces.items():
        source = (templates / template_name).read_text(encoding="utf-8")
        surface = f"filename='{surface_asset}'"
        assert shared not in source
        assert surface in source

    overlay = (templates / "_start_round_overlay.html").read_text(encoding="utf-8")
    assert "data-start-round" in overlay


@pytest.mark.parametrize("suffix", ["", "/hypotheses", "/skills", "workspace"])
def test_project_header_has_one_hidden_pending_round_action_before_back_link(suffix):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    created = client.post("/api/projects", data={
        "project_name": "Header verification", "task_description": "Goal.", "dataset_context": "Context.",
    }, content_type="multipart/form-data")
    assert created.status_code == 200
    slug = app_module._v2_load_projects()[0]["url_slug"]
    url = f"/rounds/?project={slug}" if suffix == "workspace" else f"/projects/{slug}{suffix}"
    response = client.get(url)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert html.count('id="project-start-round"') == 1
    assert 'class="v2-btn v2-btn-primary" data-start-round hidden' in html
    assert html.index('id="project-start-round"') < html.index('Back to Projects')
    assert html.count('/static/round_start.js') == 1
    assert html.count('/static/project_round_action.js') == 1
    global_page = client.get('/projects').get_data(as_text=True)
    assert 'id="project-start-round"' not in global_page
    assert '/static/project_round_action.js' not in global_page
