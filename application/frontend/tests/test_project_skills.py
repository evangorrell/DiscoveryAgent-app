"""Focused coverage for project-scoped V1 importable skills."""

import io
import json
import shutil
import stat
import threading
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from da_selection_ui import app as app_module
from da_selection_ui import local_round_runner
from da_selection_ui import project_skills


def _zip(
    entries: dict[str, bytes | str], *, symlink: str | None = None,
    modes: dict[str, int] | None = None,
) -> io.BytesIO:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            if modes and name in modes:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | modes[name]) << 16
                archive.writestr(info, content)
            else:
                archive.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")
    payload.seek(0)
    return payload


def _skill_md(name="Single Cell Analysis", description="Analyze single-cell datasets reproducibly."):
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# Instructions\n"


def _client_and_project(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    response = client.post("/api/projects", data={
        "project_name": "Skills Project",
        "task_description": "Find a reproducible relationship.",
        "dataset_context": "A small scientific dataset.",
    })
    assert response.status_code == 200, response.data
    project_id = response.get_json()["project_id"]
    return client, app_module._v2_get_project(project_id)


def _post_skill(client, project_id, payload, filename="skill.zip", endpoint=""):
    return client.post(
        f"/api/projects/{project_id}/skills{endpoint}",
        data={"skill": (payload, filename)},
        content_type="multipart/form-data",
    )


@pytest.mark.parametrize("wrapped", [False, True])
def test_valid_zip_shapes_preserve_metadata_and_supporting_files(monkeypatch, tmp_path, wrapped):
    client, project = _client_and_project(monkeypatch, tmp_path)
    prefix = "uploaded-folder/" if wrapped else ""
    entries = {
        prefix + "SKILL.md": _skill_md(),
        prefix + "scripts/analyze.py": "print('analysis')\n",
        prefix + "references/guide.txt": "reference",
    }

    preview = _post_skill(client, project["id"], _zip(entries), endpoint="/preview")
    assert preview.status_code == 200, preview.data
    assert preview.get_json()["skill"] == {
        "id": "single-cell-analysis",
        "name": "Single Cell Analysis",
        "description": "Analyze single-cell datasets reproducibly.",
    }
    assert preview.get_json()["filename"] == "skill.zip"

    imported = _post_skill(client, project["id"], _zip(entries))
    assert imported.status_code == 201, imported.data
    installed = Path(project["code_dir"]) / ".claude" / "skills" / "single-cell-analysis"
    assert (installed / "SKILL.md").is_file()
    assert (installed / "scripts" / "analyze.py").read_text() == "print('analysis')\n"
    assert (installed / "references" / "guide.txt").read_text() == "reference"


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ({"README.md": "no skill"}, "missing_skill_md"),
        ({"SKILL.md": "# Missing frontmatter"}, "missing_metadata"),
        ({"SKILL.md": "---\nname: Only Name\n---\n"}, "missing_metadata"),
        ({"one/SKILL.md": _skill_md("One"), "two/SKILL.md": _skill_md("Two")}, "multiple_skills"),
        ({"one/SKILL.md": _skill_md("One"), "other.txt": "ambiguous"}, "multiple_skills"),
        ({"one/SKILL.md": _skill_md("One"), "other/": ""}, "multiple_skills"),
    ],
)
def test_invalid_or_multiple_skill_packages_are_rejected_without_partial_install(
    monkeypatch, tmp_path, entries, code,
):
    client, project = _client_and_project(monkeypatch, tmp_path)
    response = _post_skill(client, project["id"], _zip(entries))
    assert response.status_code == 400
    assert response.get_json()["code"] == code
    assert not (Path(project["code_dir"]) / ".claude" / "skills").exists()


@pytest.mark.parametrize("archive_name", ["../escape.txt", "/absolute.txt", "C:/absolute.txt"])
def test_archive_traversal_and_absolute_paths_are_rejected(monkeypatch, tmp_path, archive_name):
    client, project = _client_and_project(monkeypatch, tmp_path)
    response = _post_skill(client, project["id"], _zip({
        "SKILL.md": _skill_md(), archive_name: "unsafe",
    }))
    assert response.status_code == 400
    assert response.get_json()["code"] == "unsafe_archive"
    assert not (Path(project["code_dir"]) / ".claude" / "skills").exists()


def test_symlink_and_excessive_archive_are_rejected(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    symlinked = _post_skill(
        client, project["id"], _zip({"SKILL.md": _skill_md()}, symlink="scripts/latest")
    )
    assert symlinked.status_code == 400
    assert symlinked.get_json()["code"] == "unsafe_archive"

    monkeypatch.setattr(project_skills, "MAX_ARCHIVE_FILES", 1)
    excessive = _post_skill(client, project["id"], _zip({
        "SKILL.md": _skill_md(), "reference.txt": "extra",
    }))
    assert excessive.status_code == 400
    assert excessive.get_json()["code"] == "archive_too_large"
    assert not (Path(project["code_dir"]) / ".claude" / "skills").exists()


def test_archive_entry_limit_counts_directory_records(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    monkeypatch.setattr(project_skills, "MAX_ARCHIVE_FILES", 3)
    response = _post_skill(client, project["id"], _zip({
        "skill/": "",
        "skill/SKILL.md": _skill_md(),
        "skill/scripts/": "",
        "skill/references/": "",
    }))
    assert response.status_code == 400
    assert response.get_json()["code"] == "archive_too_large"
    assert not (Path(project["code_dir"]) / ".claude" / "skills").exists()


def test_excessive_uncompressed_member_is_rejected(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    monkeypatch.setattr(project_skills, "MAX_MEMBER_BYTES", 4)
    response = _post_skill(client, project["id"], _zip({
        "SKILL.md": _skill_md(),
    }))
    assert response.status_code == 400
    assert response.get_json()["code"] == "archive_too_large"
    assert not (Path(project["code_dir"]) / ".claude" / "skills").exists()


def test_conflicting_archive_paths_are_rejected_cleanly(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    payload = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("SKILL.md", _skill_md())
            archive.writestr("SKILL.md", _skill_md())
    payload.seek(0)
    response = _post_skill(client, project["id"], payload)
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_skill"
    assert not (Path(project["code_dir"]) / ".claude" / "skills").exists()


@pytest.mark.parametrize(
    "entries",
    [
        {"SKILL.md": _skill_md(), "scripts/Tool": "one", "scripts/tool": "two"},
        {"SKILL.md": _skill_md(), "references/caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt": "one", "references/cafe\N{COMBINING ACUTE ACCENT}.txt": "two"},
        {"SKILL.md": _skill_md(), "node": "file", "node/child.txt": "child"},
    ],
    ids=["case-only", "unicode-normalization", "file-ancestor"],
)
def test_release_filesystem_path_collisions_are_rejected(monkeypatch, tmp_path, entries):
    client, project = _client_and_project(monkeypatch, tmp_path)
    response = _post_skill(client, project["id"], _zip(entries))
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_skill"
    assert not (Path(project["code_dir"]) / ".claude" / "skills").exists()


def test_finder_metadata_is_ignored_when_resolving_wrapped_skill(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    payload = _zip({
        "my-skill/": "",
        "my-skill/SKILL.md": _skill_md("Finder Skill"),
        "my-skill/scripts/run.sh": "#!/bin/sh\n",
        "__MACOSX/": "",
        "__MACOSX/my-skill/": "",
        "__MACOSX/my-skill/._SKILL.md": "appledouble metadata",
        "my-skill/scripts/._run.sh": "appledouble metadata",
    })
    response = _post_skill(client, project["id"], payload)
    assert response.status_code == 201, response.data
    installed = Path(project["code_dir"]) / ".claude" / "skills" / "finder-skill"
    assert (installed / "SKILL.md").is_file()
    assert not (installed / "scripts" / "._run.sh").exists()
    assert not (installed / "__MACOSX").exists()


def test_skill_md_runtime_size_boundary(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    frontmatter = _skill_md("Size Boundary").encode("utf-8")
    exact = frontmatter + b"x" * (project_skills.MAX_SKILL_MD_BYTES - len(frontmatter))
    accepted = _post_skill(
        client, project["id"], _zip({"SKILL.md": exact}), endpoint="/preview"
    )
    assert accepted.status_code == 200, accepted.data

    oversized = exact + b"x"
    rejected = _post_skill(
        client, project["id"], _zip({"SKILL.md": oversized}), endpoint="/preview"
    )
    assert rejected.status_code == 400
    assert rejected.get_json()["code"] == "archive_too_large"
    assert "1,000,000-byte" in rejected.get_json()["error"]


@pytest.mark.parametrize("indicator", ["|", ">", "|-", ">+"])
def test_multiline_frontmatter_scalars_are_rejected(monkeypatch, tmp_path, indicator):
    client, project = _client_and_project(monkeypatch, tmp_path)
    skill_md = f"---\nname: Block Skill\ndescription: {indicator}\n  Unsupported text\n---\n"
    response = _post_skill(
        client, project["id"], _zip({"SKILL.md": skill_md}), endpoint="/preview"
    )
    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_metadata"
    assert "single-line" in response.get_json()["error"]


def test_import_preserves_only_safe_executable_bits(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    response = _post_skill(client, project["id"], _zip(
        {"SKILL.md": _skill_md("Executable Skill"), "scripts/run.sh": "#!/bin/sh\n"},
        modes={"scripts/run.sh": 0o6755},
    ))
    assert response.status_code == 201, response.data
    script = Path(project["code_dir"]) / ".claude" / "skills" / "executable-skill" / "scripts" / "run.sh"
    installed_mode = stat.S_IMODE(script.stat().st_mode)
    assert installed_mode & 0o111 == 0o111
    assert installed_mode & 0o7000 == 0


def test_non_zip_invalid_zip_and_duplicate_are_clear(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    not_zip = _post_skill(client, project["id"], io.BytesIO(b"text"), filename="SKILL.md")
    assert not_zip.status_code == 400
    assert not_zip.get_json()["code"] == "zip_required"
    corrupt = _post_skill(client, project["id"], io.BytesIO(b"not a zip"))
    assert corrupt.status_code == 400
    assert corrupt.get_json()["code"] == "invalid_zip"

    entries = {"SKILL.md": _skill_md()}
    assert _post_skill(client, project["id"], _zip(entries)).status_code == 201
    duplicate = _post_skill(client, project["id"], _zip(entries))
    assert duplicate.status_code == 409
    assert duplicate.get_json()["code"] == "duplicate_skill"


def test_listing_removal_and_persistence_across_client_state(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    empty = client.get(f"/api/projects/{project['id']}/skills").get_json()
    assert empty["skills"] == []
    assert _post_skill(client, project["id"], _zip({"SKILL.md": _skill_md()})).status_code == 201

    fresh_client = app_module.app.test_client()
    listed = fresh_client.get(f"/api/projects/{project['id']}/skills").get_json()
    assert listed["skills"][0]["name"] == "Single Cell Analysis"
    assert listed["skills"][0]["description"] == "Analyze single-cell datasets reproducibly."

    removed = fresh_client.delete(
        f"/api/projects/{project['id']}/skills/single-cell-analysis"
    )
    assert removed.status_code == 200
    assert not (Path(project["code_dir"]) / ".claude" / "skills" / "single-cell-analysis").exists()
    missing = fresh_client.delete(
        f"/api/projects/{project['id']}/skills/single-cell-analysis"
    )
    assert missing.status_code == 404
    assert missing.get_json()["code"] == "skill_not_found"


def test_supported_legacy_project_uses_its_trusted_code_directory(monkeypatch, tmp_path):
    legacy_disk = tmp_path / "legacy-disk"
    legacy_code = tmp_path / "legacy-project" / "code"
    legacy_code.mkdir(parents=True)
    legacy_disk.mkdir()
    record = {
        "id": "legacy-project-id",
        "display_name": "Legacy Project",
        "url_slug": "legacy-project",
        "project_root": str(legacy_code.parent),
        "code_dir": str(legacy_code),
        "checkpoint_dir": str(legacy_code.parent / "checkpoint"),
        "readme_path": str(legacy_code / "README.md"),
    }
    (legacy_disk / "v2_projects.json").write_text(
        json.dumps({"projects": [record]}), encoding="utf-8"
    )
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: legacy_disk)
    client = app_module.app.test_client()

    empty = client.get("/api/projects/legacy-project-id/skills")
    assert empty.status_code == 200
    assert empty.get_json()["skills"] == []
    imported = _post_skill(
        client, "legacy-project-id", _zip({"SKILL.md": _skill_md("Legacy Helper")})
    )
    assert imported.status_code == 201
    assert (legacy_code / ".claude" / "skills" / "legacy-helper" / "SKILL.md").is_file()


def test_removal_identifier_cannot_escape_skill_root(monkeypatch, tmp_path):
    _client, project = _client_and_project(monkeypatch, tmp_path)
    outside = Path(project["code_dir"]) / "keep.txt"
    outside.write_text("keep", encoding="utf-8")
    with pytest.raises(project_skills.SkillImportError) as error:
        project_skills.remove_project_skill(project, "../keep")
    assert error.value.code == "invalid_skill_id"
    assert outside.read_text(encoding="utf-8") == "keep"


def test_symlinked_skill_root_cannot_escape_project(monkeypatch, tmp_path):
    _client, project = _client_and_project(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    claude_dir = Path(project["code_dir"]) / ".claude"
    claude_dir.mkdir()
    (claude_dir / "skills").symlink_to(outside, target_is_directory=True)

    with pytest.raises(project_skills.SkillImportError) as error:
        project_skills.validate_project_skill(
            project, _zip({"SKILL.md": _skill_md()}), "skill.zip"
        )
    assert error.value.code == "invalid_project"
    assert list(outside.iterdir()) == []


def test_symlinked_code_directory_is_rejected_without_outside_write(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    code = Path(project["code_dir"])
    outside = tmp_path / "outside-code"
    outside.mkdir()
    shutil.rmtree(code)
    code.symlink_to(outside, target_is_directory=True)

    listed = client.get(f"/api/projects/{project['id']}/skills")
    assert listed.status_code == 409
    assert listed.get_json()["code"] == "invalid_project"
    response = _post_skill(client, project["id"], _zip({"SKILL.md": _skill_md()}))
    assert response.status_code == 409
    assert response.get_json()["code"] == "invalid_project"
    assert list(outside.iterdir()) == []


def test_missing_code_directory_is_rejected_and_not_recreated(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    code = Path(project["code_dir"])
    shutil.rmtree(code)

    response = _post_skill(client, project["id"], _zip({"SKILL.md": _skill_md()}))
    assert response.status_code == 409
    assert response.get_json()["code"] == "invalid_project"
    assert not code.exists()


def test_remove_rejects_symlinked_code_without_deleting_outside(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    code = Path(project["code_dir"])
    outside = tmp_path / "outside-code"
    outside_skill = outside / ".claude" / "skills" / "single-cell-analysis"
    outside_skill.mkdir(parents=True)
    (outside_skill / "SKILL.md").write_text(_skill_md(), encoding="utf-8")
    shutil.rmtree(code)
    code.symlink_to(outside, target_is_directory=True)

    response = client.delete(
        f"/api/projects/{project['id']}/skills/single-cell-analysis"
    )
    assert response.status_code == 409
    assert response.get_json()["code"] == "invalid_project"
    assert (outside_skill / "SKILL.md").is_file()


def test_import_and_remove_are_blocked_only_while_round_is_running(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "_v2_project_research_is_running", lambda _project: True)
    blocked = _post_skill(client, project["id"], _zip({"SKILL.md": _skill_md()}))
    assert blocked.status_code == 409
    assert blocked.get_json()["code"] == "round_running"

    monkeypatch.setattr(app_module, "_v2_project_research_is_running", lambda _project: False)
    assert _post_skill(client, project["id"], _zip({"SKILL.md": _skill_md()})).status_code == 201
    monkeypatch.setattr(app_module, "_v2_project_research_is_running", lambda _project: True)
    blocked_remove = client.delete(
        f"/api/projects/{project['id']}/skills/single-cell-analysis"
    )
    assert blocked_remove.status_code == 409
    assert (Path(project["code_dir"]) / ".claude" / "skills" / "single-cell-analysis").is_dir()
    monkeypatch.setattr(app_module, "_v2_project_research_is_running", lambda _project: False)
    assert client.delete(
        f"/api/projects/{project['id']}/skills/single-cell-analysis"
    ).status_code == 200


def test_skills_page_and_navigation_render(monkeypatch, tmp_path):
    client, project = _client_and_project(monkeypatch, tmp_path)
    html = client.get("/projects/skills-project/skills").get_data(as_text=True)
    assert 'aria-current="page">Skills</a>' in html
    assert "No custom skills yet" in html
    assert "Import Skill" in html
    assert html.count('id="skills-import-open"') == 1
    assert 'id="skills-empty-import"' not in html
    assert '<h1 class="v2-title" id="skills-heading">Skills</h1>' in html
    assert 'class="v2-skills-intro-row"' in html
    assert "Add specialized skills for the execution agent to use in this project." in html
    assert "while exploring this project" not in html
    assert "execution_skills_path" not in html
    assert project["id"] in html
    assert html.index(">Home</a>") < html.index("Hypothesis Studio")
    assert html.index("Hypothesis Studio") < html.index("Round Workspace")
    assert html.index("Round Workspace") < html.index(">Skills</a>")
    assert html.index(">Skills</a>") < html.index("Audit Agent")


def test_skills_client_preserves_error_codes_and_waits_for_initial_status():
    static_root = Path(app_module.__file__).parent
    script = (static_root / "static" / "project_skills.js").read_text(encoding="utf-8")
    template = (static_root / "templates" / "project_skills.html").read_text(encoding="utf-8")
    styles = (static_root / "static" / "style.css").read_text(encoding="utf-8")

    assert "class ApiError extends Error" in script
    assert "this.code = code || null" in script
    assert "error.code !== 'round_running'" in script
    assert "await loadSkills()" in script
    assert "const disabled = !statusResolved || running" in script
    assert 'id="skills-import-open" type="button" disabled' in template
    assert 'id="skills-empty-import"' not in template
    assert 'id="skill-import-dropzone" tabindex="0" role="button"' in template
    assert 'id="skill-import-file" type="file" accept=".zip,application/zip" hidden' in template
    assert "webkitdirectory" not in template
    assert " multiple" not in template
    assert "elements.dropzone.addEventListener('click'" in script
    assert "elements.dropzone.addEventListener('keydown'" in script
    assert "elements.dropzone.addEventListener('dragover'" in script
    assert "elements.dropzone.addEventListener('drop', dropFiles)" in script
    assert "files.length !== 1" in script
    assert "Choose exactly one .zip skill package." in script
    assert "previewSelectedFiles(event.dataTransfer && event.dataTransfer.files)" in script
    assert ".v2-dropzone.is-dragover" in styles
    assert ".v2-skills-intro-row" in styles
    assert ".v2-skill-import-modal > .v2-modal-head .v2-modal-close" in styles


def test_v2_principal_titles_share_home_size_and_skills_home_offset():
    static_root = Path(app_module.__file__).parent / "static"
    styles = (static_root / "style.css").read_text(encoding="utf-8")
    audit_styles = (static_root / "project_audit_agent.css").read_text(encoding="utf-8")
    studio_styles = (static_root / "hypothesis_studio.css").read_text(encoding="utf-8")

    assert "--v2-main-title-size: 2.9rem" in styles
    assert "body.v2 { --v2-main-title-size: 2.1rem; }" in styles
    shared_title_rule = styles[
        styles.index("body.v2 .v2-title,", styles.index("/* ---- Project identity ---- */")):
        styles.index("}", styles.index("body.v2 .v2-title,", styles.index("/* ---- Project identity ---- */")))
    ]
    assert "body.v2 .aa-intro-heading" in shared_title_rule
    assert "body.v2 .hs-welcome-heading" in shared_title_rule
    assert "font-size: var(--v2-main-title-size)" in shared_title_rule

    audit_heading_rule = audit_styles[
        audit_styles.index(".aa-intro-heading {"):
        audit_styles.index("}", audit_styles.index(".aa-intro-heading {"))
    ]
    studio_heading_rule = studio_styles[
        studio_styles.index(".hs-welcome-heading {"):
        studio_styles.index("}", studio_styles.index(".hs-welcome-heading {"))
    ]
    assert "font-size:" not in audit_heading_rule
    assert "font-size:" not in studio_heading_rule

    assert ".v2-page-skills .v2-main { max-width: 920px; }" in styles
    assert ".v2-skills-page { padding: 0 0 72px; }" in styles
    assert ".v2-skills-page { padding-top:" not in styles


@pytest.mark.parametrize("_repeat", range(5))
def test_skill_import_serializes_with_round_launch_transition(monkeypatch, tmp_path, _repeat):
    _client, project = _client_and_project(monkeypatch, tmp_path)
    backend_root = tmp_path / "backend"
    backend_python = backend_root / ".venv" / "bin" / "python"
    launcher = backend_root / "scripts" / "run_hb_sherlock.py"
    backend_python.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")
    launcher.write_text("", encoding="utf-8")
    environment = {
        "ANTHROPIC_API_KEY": "test-secret",
        "DISCOVERYAGENT_BACKEND_ROOT": str(backend_root),
        "DISCOVERYAGENT_BACKEND_PYTHON": str(backend_python),
        "PATH": "",
    }

    popen_entered = threading.Event()
    allow_spawn = threading.Event()
    mutation_waiting = threading.Event()
    mutation_done = threading.Event()
    launch_result = {}
    mutation_result = {}

    class RunningProcess:
        pid = 54321

        def poll(self):
            return None

    def blocking_popen(*_args, **_kwargs):
        popen_entered.set()
        assert allow_spawn.wait(5)
        return RunningProcess()

    original_guard = local_round_runner.round_state_guard

    @contextmanager
    def observed_round_guard():
        mutation_waiting.set()
        with original_guard():
            yield

    def launch():
        try:
            launch_result["value"] = local_round_runner.start_round(project, env=environment)
        except Exception as exc:  # pragma: no cover - asserted below
            launch_result["error"] = exc

    def mutate():
        mutation_client = app_module.app.test_client()
        mutation_result["response"] = _post_skill(
            mutation_client, project["id"], _zip({"SKILL.md": _skill_md()})
        )
        mutation_done.set()

    local_round_runner._PROCESSES.clear()
    monkeypatch.setattr(local_round_runner.subprocess, "Popen", blocking_popen)
    monkeypatch.setattr(local_round_runner, "_monitor_process", lambda *_args: None)
    monkeypatch.setattr(local_round_runner, "_pid_matches_state", lambda _state: True)
    monkeypatch.setattr(local_round_runner, "round_state_guard", observed_round_guard)

    launch_thread = threading.Thread(target=launch)
    mutation_thread = threading.Thread(target=mutate)
    launch_thread.start()
    assert popen_entered.wait(5)
    mutation_thread.start()
    assert mutation_waiting.wait(5)
    assert not mutation_done.is_set()
    allow_spawn.set()
    launch_thread.join(5)
    mutation_thread.join(5)

    assert not launch_thread.is_alive()
    assert not mutation_thread.is_alive()
    assert "error" not in launch_result
    assert launch_result["value"]["status"] == "starting"
    response = mutation_result["response"]
    assert response.status_code == 409
    assert response.get_json()["code"] == "round_running"
    assert not (Path(project["code_dir"]) / ".claude" / "skills").exists()
    local_round_runner._PROCESSES.clear()


def test_project_deletion_prevents_waiting_import_from_recreating_root(monkeypatch, tmp_path):
    _client, project = _client_and_project(monkeypatch, tmp_path)
    project_root = Path(project["project_root"])
    trash_root = tmp_path / "test-trash"
    trash_root.mkdir()
    deletion_holds_lock = threading.Event()
    allow_delete = threading.Event()
    import_started = threading.Event()
    responses = {}

    def blocking_trash(target):
        deletion_holds_lock.set()
        assert allow_delete.wait(5)
        shutil.move(str(target), str(trash_root / target.name))

    def delete_project():
        responses["delete"] = app_module.app.test_client().delete(
            f"/api/projects/{project['id']}"
        )

    def import_skill():
        import_started.set()
        responses["import"] = _post_skill(
            app_module.app.test_client(), project["id"], _zip({"SKILL.md": _skill_md()})
        )

    monkeypatch.setattr(app_module, "_v2_move_to_trash", blocking_trash)
    delete_thread = threading.Thread(target=delete_project)
    import_thread = threading.Thread(target=import_skill)
    delete_thread.start()
    assert deletion_holds_lock.wait(5)
    import_thread.start()
    assert import_started.wait(5)
    allow_delete.set()
    delete_thread.join(5)
    import_thread.join(5)

    assert not delete_thread.is_alive()
    assert not import_thread.is_alive()
    assert responses["delete"].status_code == 200
    assert responses["import"].status_code == 404
    assert not project_root.exists()
    assert (trash_root / project_root.name).is_dir()
