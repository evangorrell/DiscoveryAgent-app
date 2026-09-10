import json
import os
from pathlib import Path

import pytest

from da_selection_ui import app as app_module


FIGURE_REFERENCE = "figures/final_synthesis_dashboard.png"
FIGURE_BYTES = b"composite-dashboard"
CAPTIONS = [
    "Regional Vulnerability Heatmap (Panel A of Dashboard)",
    "Lewy Body × AD Synergy (Panel C of Dashboard)",
    "Microglial Colocalization Reversal (Panel E of Dashboard)",
]


def _report_markdown(title):
    figures = "\n\n".join(
        f"![{caption}]({FIGURE_REFERENCE})" for caption in CAPTIONS
    )
    return f"# {title}\n\n{figures}\n"


def _clear_artifact_cache():
    with app_module._ROUND_WORKSPACE_CACHE_LOCK:
        app_module._ROUND_WORKSPACE_ARTIFACT_CACHE.clear()


def _figures_project(monkeypatch, tmp_path, layout="custom/readable-project"):
    project = tmp_path / layout
    rounds = project / "checkpoint" / "checkpoints"
    round_dir = rounds / "round_0"
    code = project / "code"
    figure = code / FIGURE_REFERENCE
    figure.parent.mkdir(parents=True)
    round_dir.mkdir(parents=True)
    figure.write_bytes(FIGURE_BYTES)

    reports = {
        "memory_report.md": _report_markdown("Memory Report"),
        "memory_report_summary.md": _report_markdown("Memory Report Summary"),
    }
    for name, markdown in reports.items():
        (round_dir / name).write_text(markdown, encoding="utf-8")
    (round_dir / "config.json").write_text(
        json.dumps({
            "round_idx": 0,
            "config": {
                "working_directory": "/remote/project/code",
                "log_dir": "/remote/run",
                "checkpoint_base_dir": "/remote/run/checkpoints",
            },
        }),
        encoding="utf-8",
    )

    record = {
        "id": f"figures-{layout.replace('/', '-')}",
        "url_slug": "figures-project",
        "project_root": str(project),
        "checkpoint_dir": str(round_dir),
        "code_dir": "code" if layout.startswith("legacy/") else str(code),
    }
    monkeypatch.setattr(
        app_module, "_round_workspace_checkpoint_dir_for_session", lambda: round_dir
    )
    monkeypatch.setattr(app_module, "_active_project_record", lambda: record)
    monkeypatch.setattr(
        app_module, "_round_workspace_round_status",
        lambda *_args, **_kwargs: "completed",
    )
    monkeypatch.setattr(app_module, "_v2_round_status", lambda _info: "completed")
    monkeypatch.setattr(app_module, "_get_checkpoint_base", lambda: rounds)
    _clear_artifact_cache()
    return {
        "project": project,
        "rounds": rounds,
        "round_dir": round_dir,
        "code": code,
        "figure": figure,
        "reports": reports,
    }


def _authenticated_client():
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["authenticated"] = True
    return client


def _execution_output(registry):
    return next(
        source for source in registry.values()
        if source.get("kind") == "execution-output"
    )


def _tree_paths(node):
    paths = {node.get("path")}
    for child in node.get("children", []):
        paths.update(_tree_paths(child))
    return paths


def test_figures_prefix_resolves_three_captions_in_full_and_summary_and_serves(
    monkeypatch, tmp_path
):
    fixture = _figures_project(monkeypatch, tmp_path)
    before = {
        name: (fixture["round_dir"] / name).read_bytes()
        for name in fixture["reports"]
    }
    client = _authenticated_client()

    resolved_urls = set()
    for report_name in ("memory_report.md", "memory_report_summary.md"):
        response = client.get(
            "/api/round_workspace_markdown",
            query_string={"round": 0, "path": report_name},
        )
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["artifact_role"] == "memory_report"
        assert [figure["alt"] for figure in payload["figures"]] == CAPTIONS
        assert all(figure["available"] is True for figure in payload["figures"])
        report_urls = {figure["url"] for figure in payload["figures"]}
        assert len(report_urls) == 1
        resolved_urls.update(report_urls)
        assert payload["display_text"].count("rw-report-figure:rw_fig_") == 3

    assert len(resolved_urls) == 1
    artifact_response = client.get(resolved_urls.pop())
    assert artifact_response.status_code == 200
    assert artifact_response.data == FIGURE_BYTES
    assert artifact_response.content_type.startswith("image/png")

    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(0)
        output = _execution_output(registry)
        descriptor = output["files"][FIGURE_REFERENCE]
        assert descriptor["target"] == fixture["figure"].resolve()
        assert FIGURE_REFERENCE in _tree_paths(app_module._round_workspace_code_tree())

    code_response = client.get(
        "/api/round_workspace_code_artifact",
        query_string={"path": FIGURE_REFERENCE},
    )
    assert code_response.status_code == 200
    assert code_response.data == FIGURE_BYTES
    for name, original in before.items():
        assert (fixture["round_dir"] / name).read_bytes() == original


def test_reports_figures_prefix_from_archived_runs_resolves_and_serves(
    monkeypatch, tmp_path
):
    fixture = _figures_project(monkeypatch, tmp_path)
    reference = "reports/figures/relay_summary.png"
    archived_figure = fixture["code"] / reference
    archived_figure.parent.mkdir(parents=True, exist_ok=True)
    archived_figure.write_bytes(b"archived-relay-summary")
    (fixture["round_dir"] / "memory_report.md").write_text(
        f"# Memory Report\n\n![Relay summary]({reference})\n",
        encoding="utf-8",
    )
    _clear_artifact_cache()

    client = _authenticated_client()
    response = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 0, "path": "memory_report.md"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["display_text"] == (
        "# Memory Report\n\n![Relay summary](rw-report-figure:rw_fig_0)\n"
    )
    assert payload["figures"][0]["available"] is True
    served = client.get(payload["figures"][0]["url"])
    assert served.status_code == 200
    assert served.data == b"archived-relay-summary"

    with app_module.app.test_request_context("/"):
        output = _execution_output(app_module._round_workspace_artifact_registry(0))
        assert output["files"][reference]["target"] == archived_figure.resolve()


@pytest.mark.parametrize(
    "layout",
    [
        "application-data/projects/managed-project",
        "custom/readable-project",
        "legacy/discovery-agent-project",
    ],
)
def test_figures_prefix_uses_registered_code_root_for_supported_storage_layouts(
    monkeypatch, tmp_path, layout
):
    fixture = _figures_project(monkeypatch, tmp_path, layout=layout)
    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(0)
        output = _execution_output(registry)
    assert output["files"][FIGURE_REFERENCE]["target"] == fixture["figure"].resolve()


def test_checkpoint_local_figure_keeps_precedence_over_registered_code_figure(
    monkeypatch, tmp_path
):
    fixture = _figures_project(monkeypatch, tmp_path)
    checkpoint_figure = fixture["round_dir"] / FIGURE_REFERENCE
    checkpoint_figure.parent.mkdir(parents=True)
    checkpoint_figure.write_bytes(b"checkpoint-composite")
    _clear_artifact_cache()
    client = _authenticated_client()

    response = client.get(
        "/api/round_workspace_markdown",
        query_string={"round": 0, "path": "memory_report.md"},
    )
    assert response.status_code == 200
    figures = response.get_json()["figures"]
    assert len(figures) == 3
    assert all(
        figure["url"].startswith(
            "/checkpoint_assets/round_0/figures/final_synthesis_dashboard.png"
        )
        for figure in figures
    )
    served = client.get(figures[0]["url"])
    assert served.status_code == 200
    assert served.data == b"checkpoint-composite"


def test_figures_prefix_rejects_missing_fuzzy_hidden_nonregular_and_escapes(
    monkeypatch, tmp_path
):
    fixture = _figures_project(monkeypatch, tmp_path)
    figures = fixture["code"] / "figures"
    outside = tmp_path / "outside-secret.png"
    outside.write_bytes(b"outside")
    (figures / "linked.png").symlink_to(outside)
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "secret.png").write_bytes(b"outside-dir")
    (figures / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
    (figures / ".hidden.png").write_bytes(b"hidden")
    (figures / "directory.png").mkdir()
    os.mkfifo(figures / "pipe.png")
    (figures / "final_synthesis_dashboard-v20.png").write_bytes(b"near-match")
    (figures / "note.txt").write_text("not an image", encoding="utf-8")

    unsafe = "\n".join([
        "![missing](figures/missing.png)",
        "![fuzzy](figures/final_synthesis_dashboard-v2.png)",
        "![symlink](figures/linked.png)",
        "![symlink-dir](figures/linked-dir/secret.png)",
        "![hidden](figures/.hidden.png)",
        "![directory](figures/directory.png)",
        "![nonregular](figures/pipe.png)",
        "![traversal](figures/../outside-secret.png)",
        f"![outside]({outside})",
        "![historical](/old/machine/figures/final_synthesis_dashboard.png)",
        "![unsupported](figures/note.txt)",
    ])
    for name in fixture["reports"]:
        (fixture["round_dir"] / name).write_text(unsafe, encoding="utf-8")
    _clear_artifact_cache()

    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(0)
        assert not any(
            source.get("kind") == "execution-output"
            for source in registry.values()
        )

    response = _authenticated_client().get(
        "/api/round_workspace_markdown",
        query_string={"round": 0, "path": "memory_report.md"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["figures"]
    assert all(figure["available"] is False for figure in payload["figures"])
    assert all(figure["url"] is None for figure in payload["figures"])
    assert str(outside) not in payload["display_text"]
    assert "/old/machine/" not in payload["display_text"]
    assert "figures/note.txt" in payload["display_text"]


def test_figures_prefix_wrong_case_is_rejected_when_filesystem_distinguishes_case(
    monkeypatch, tmp_path
):
    fixture = _figures_project(monkeypatch, tmp_path)
    exact = fixture["code"] / "figures" / "CaseSensitive.png"
    exact.write_bytes(b"case-sensitive")
    wrong_reference = "figures/casesensitive.png"
    if (fixture["code"] / wrong_reference).exists():
        pytest.skip("filesystem uses case-insensitive path lookup")

    markdown = f"![wrong case]({wrong_reference})"
    for name in fixture["reports"]:
        (fixture["round_dir"] / name).write_text(markdown, encoding="utf-8")
    _clear_artifact_cache()
    with app_module.app.test_request_context("/"):
        registry = app_module._round_workspace_artifact_registry(0)
        assert not any(
            wrong_reference in (source.get("files") or {})
            for source in registry.values()
        )


def test_historical_absolute_figures_stay_rejected_while_existing_output_mapping_works(
    monkeypatch, tmp_path
):
    fixture = _figures_project(monkeypatch, tmp_path)
    historical_output = fixture["code"] / "outputs" / "historical.png"
    historical_output.parent.mkdir(parents=True)
    historical_output.write_bytes(b"historical-output")
    markdown = "\n".join([
        "![supported](/old/machine/outputs/historical.png)",
        "![rejected](/old/machine/figures/final_synthesis_dashboard.png)",
    ])
    for name in fixture["reports"]:
        (fixture["round_dir"] / name).write_text(markdown, encoding="utf-8")
    _clear_artifact_cache()

    with app_module.app.test_request_context("/"):
        output = _execution_output(
            app_module._round_workspace_artifact_registry(0)
        )
    assert set(output["files"]) == {"outputs/historical.png"}

    response = _authenticated_client().get(
        "/api/round_workspace_markdown",
        query_string={"round": 0, "path": "memory_report.md"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["figures"]) == 1
    assert payload["figures"][0]["alt"] == "supported"
    assert payload["figures"][0]["available"] is True
    assert "**Figure unavailable:** rejected" in payload["display_text"]
