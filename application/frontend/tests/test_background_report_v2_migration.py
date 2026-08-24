"""Focused contracts for retiring Background Report into V2 resources."""

import json
from pathlib import Path

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    return app_module.app.test_client()


def _create(client, name="Background Migration"):
    response = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": f"Investigate {name}.",
            "dataset_context": "Background migration test data.",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    project_id = response.get_json()["project_id"]
    return next(
        project for project in app_module._v2_load_projects()
        if project["id"] == project_id
    )


def _paper_dir(project, stamp="20260816_120000"):
    path = Path(project["project_root"]) / "checkpoint" / f"research_paper_{stamp}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _update_record(tmp_path, project_id, **changes):
    registry_path = tmp_path / "v2_projects.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    record = next(item for item in payload["projects"] if item["id"] == project_id)
    record.update(changes)
    registry_path.write_text(json.dumps(payload), encoding="utf-8")
    return registry_path


def test_pdf_resource_route_remains_available_without_v2_audit_shortcut(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create(client)
    paper_dir = _paper_dir(project)
    (paper_dir / "research_paper.md").write_text("MARKDOWN SHOULD BE FALLBACK", encoding="utf-8")
    (paper_dir / "research_paper.pdf").write_bytes(b"%PDF-1.4\nPDF PREFERRED")
    resource = app_module._resolve_v2_research_paper_resource(project)

    response = client.get(f"/rounds/?project={project['url_slug']}")
    html = response.get_data(as_text=True)

    assert resource is not None
    assert resource["kind"] == "pdf"
    assert resource["path"] == paper_dir / "research_paper.pdf"
    assert response.status_code == 200
    assert 'id="audit-resource-paper"' not in html
    assert 'href="/research_paper/pdf?project=background-migration"' not in html
    assert "audit-paper-modal" not in html
    pdf = client.get(f"/research_paper/pdf?project={project['url_slug']}")
    assert pdf.status_code == 200
    assert pdf.data == b"%PDF-1.4\nPDF PREFERRED"


def test_v2_audit_does_not_embed_markdown_paper_when_pdf_is_absent(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create(client)
    paper_dir = _paper_dir(project)
    marker = "MARKDOWN FALLBACK PAPER"
    (paper_dir / "research_paper.md").write_text(
        f"# {marker}\n\n<script>alert('unsafe')</script>\n\n"
        "![Figure](figures/result.png)\n\n![Unsafe](/Users/example/secret.png)",
        encoding="utf-8",
    )
    resource = app_module._resolve_v2_research_paper_resource(project)

    response = client.get(f"/rounds/?project={project['url_slug']}")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert resource is not None
    assert resource["kind"] == "markdown"
    assert resource["path"] == paper_dir / "research_paper.md"
    assert 'id="audit-resource-paper"' not in html
    assert 'id="audit-paper-modal"' not in html
    assert marker not in html
    assert "dompurify@3" in html
    assert "report_figures.js" in html
    assert 'href="/Users/' not in html
    assert 'src="/Users/' not in html
    assert client.get(f"/research_paper/pdf?project={project['url_slug']}").status_code == 404

    js = (Path(app_module.app.static_folder) / "project_audit_agent.js").read_text(encoding="utf-8")
    assert "DOMPurify.sanitize" not in js
    assert "trustedPaperImageUrl" not in js


def test_v2_audit_has_no_research_paper_placeholder_when_no_resource_exists(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create(client)

    response = client.get(f"/rounds/?project={project['url_slug']}")
    html = response.get_data(as_text=True)
    resource = app_module._resolve_v2_research_paper_resource(project)

    assert resource is None
    assert 'id="audit-resource-paper"' not in html
    assert "audit-paper-modal" not in html


def test_v2_audit_never_embeds_configured_or_discovered_markdown(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create(client)
    outside = tmp_path / "outside-paper"
    outside.mkdir()
    (outside / "research_paper.md").write_text("OUTSIDE PROJECT SECRET", encoding="utf-8")

    registry = _update_record(
        tmp_path, project["id"], research_paper_path=str(outside / "research_paper.md"),
    )
    configured_project = app_module._v2_get_project(project["id"])
    assert app_module._resolve_v2_research_paper_resource(configured_project) is None
    response = client.get(f"/rounds/?project={project['url_slug']}")
    assert 'id="audit-resource-paper"' not in response.get_data(as_text=True)
    assert "OUTSIDE PROJECT SECRET" not in response.get_data(as_text=True)
    persisted = json.loads(registry.read_text(encoding="utf-8"))
    assert next(item for item in persisted["projects"] if item["id"] == project["id"])[
        "research_paper_path"
    ] == str(outside / "research_paper.md")

    link = Path(project["project_root"]) / "checkpoint" / "research_paper_link"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    _update_record(tmp_path, project["id"], research_paper_path="research_paper_link")
    configured_project = app_module._v2_get_project(project["id"])
    assert app_module._resolve_v2_research_paper_resource(configured_project) is None
    response = client.get(f"/rounds/?project={project['url_slug']}")
    assert 'id="audit-resource-paper"' not in response.get_data(as_text=True)

    paper = _paper_dir(project, "20260817_120000") / "research_paper.md"
    paper.write_text("TOO LARGE", encoding="utf-8")
    _update_record(tmp_path, project["id"], research_paper_path=None)
    monkeypatch.setattr(app_module, "V2_RESEARCH_PAPER_MARKDOWN_MAX_BYTES", 4)
    configured_project = app_module._v2_get_project(project["id"])
    assert app_module._resolve_v2_research_paper_resource(configured_project) is None
    response = client.get(f"/rounds/?project={project['url_slug']}")
    assert 'id="audit-resource-paper"' not in response.get_data(as_text=True)
    assert "TOO LARGE" not in response.get_data(as_text=True)


def test_v2_audit_does_not_embed_any_project_research_paper(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project_a = _create(client, "Paper Project A")
    project_b = _create(client, "Paper Project B")
    (_paper_dir(project_a) / "research_paper.md").write_text("ONLY PROJECT A", encoding="utf-8")
    (_paper_dir(project_b) / "research_paper.md").write_text("ONLY PROJECT B", encoding="utf-8")
    resource_a = app_module._resolve_v2_research_paper_resource(project_a)
    resource_b = app_module._resolve_v2_research_paper_resource(project_b)

    html_a = client.get(f"/rounds/?project={project_a['url_slug']}").get_data(as_text=True)
    html_b = client.get(f"/rounds/?project={project_b['url_slug']}").get_data(as_text=True)

    assert resource_a is not None and resource_a["path"].read_text() == "ONLY PROJECT A"
    assert resource_b is not None and resource_b["path"].read_text() == "ONLY PROJECT B"
    assert "ONLY PROJECT A" not in html_a and "ONLY PROJECT B" not in html_a
    assert "ONLY PROJECT A" not in html_b and "ONLY PROJECT B" not in html_b


def test_registered_pdf_route_canonicalizes_id_and_rejects_out_of_project_pdf(
    monkeypatch, tmp_path,
):
    client = _client(monkeypatch, tmp_path)
    project = _create(client)
    paper = _paper_dir(project) / "research_paper.pdf"
    paper.write_bytes(b"%PDF-1.4\nCANONICAL")

    by_id = client.get(f"/research_paper/pdf?project={project['id']}")
    assert by_id.status_code == 302
    assert by_id.headers["Location"] == (
        f"/research_paper/pdf?project={project['url_slug']}"
    )

    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\nOUTSIDE")
    _update_record(tmp_path, project["id"], research_paper_path=str(outside))
    assert client.get(
        f"/research_paper/pdf?project={project['url_slug']}"
    ).status_code == 404


def test_legacy_background_asset_route_remains_for_stored_report_links(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    project = _create(client)
    checkpoint = Path(project["checkpoint_dir"])
    image = checkpoint / "images" / "legacy.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"legacy-image")

    response = client.get(
        f"/background_report/assets/legacy.png?project={project['url_slug']}",
    )

    assert response.status_code == 200
    assert response.data == b"legacy-image"
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert 'src.startswith("/background_report/assets/")' in source


def test_background_and_report_shell_templates_and_v1_script_are_removed():
    root = Path(app_module.app.root_path)
    routes = {rule.rule for rule in app_module.app.url_map.iter_rules()}
    assert not (root / "templates" / "background_report.html").exists()
    assert not (root / "templates" / "report.html").exists()
    assert not (root / "static" / "script.js").exists()
    assert "/background_report" not in routes
    assert "/report" not in routes
    assert "/background_report/assets/<path:filename>" in routes
    assert "/report_assets/<root>/<path:filename>" in routes
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert "render_template('background_report.html'" not in source
    assert 'render_template("background_report.html"' not in source
    audit_template = (root / "templates" / "round_workspace.html").read_text(encoding="utf-8")
    assert "Background Report Chat" not in audit_template
    assert "report-chat-panel" not in audit_template
