"""Focused Project Home memory-report figure regressions."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from da_selection_ui import app as app_module


OPTIC_NERVE_MEMORY_FIGURES = (
    ("Combined TF Cassette Summary", "analysis_output/combined_TF_cassette_summary.png"),
    ("Tian 2022 Binary Support Heatmap", "outputs/analysis_tf_cassette/tian2022_binary_support_heatmap.png"),
    ("Tian 2022 Support Score Barplot", "outputs/analysis_tf_cassette/tian2022_support_score_barplot.png"),
    ("Jacobi TF Co-expression Stacked Bar", "outputs/analysis_tf_cassette/jacobi2022_tf_coexpression_stacked_bar.png"),
    ("Jacobi TF Expression Violins", "outputs/analysis_tf_cassette/jacobi2022_tf_expression_violins.png"),
    ("Jacobi TF LFC ONC7d Bars", "analysis_output/jacobi2022_TF_LFC_ONC7d_bars.png"),
    ("KLF6a Deep Dive", "outputs/analysis_tf_cassette/fig13_klf6a_deepdive.png"),
    ("Step 4 Effect Sizes", "outputs/analysis_tf_cassette/fig9_step4_effect_sizes.png"),
    ("Step 5 Inj7 vs Uninj7", "outputs/analysis_tf_cassette/fig10_step5_inj7_vs_uninj7.png"),
    ("Step 3 Simpson's Paradox LFC", "outputs/analysis_tf_cassette/step3_simpsons_paradox_lfc.png"),
    ("Sustainment Analysis", "outputs/figures/ali2025_sustainment_scatter.png"),
    ("Sustainment Barplots", "outputs/figures/ali2025_sustainment_barplots.png"),
    ("Sustainment Heatmap", "outputs/figures/ali2025_sustainment_heatmap.png"),
    ("Discordance Scan Summary", "outputs/figures/discordance_scan_summary.png"),
    ("Top 30 Poised Candidates", "outputs/figures/top30_poised_candidates_bars.png"),
    ("Validation Cancer Risk", "outputs/figures/validation_part1_cancer_risk.png"),
    ("Jacobi Intervention FC", "outputs/figures/validation_part2_jacobi_intervention.png"),
    ("Tran 2019 Heatmap", "outputs/figures/validation_part3_tran_timecourse_heatmap.png"),
    ("ATAC Peak Classification", "outputs/figures/mechanistic_link_per_gene_peaks.png"),
    ("Subtype Expression Heatmap", "outputs/figures/tran2019_rgc_subtype_specificity.png"),
    ("Time Course Line Plots", "outputs/figures/tran2019_rgc_onc_timecourse.png"),
    ("TF Co-localization Heatmap", "outputs/figures/mechanistic_link_TF_colocalization_heatmap.png"),
    ("ATF3 Footprint at Rgs3/Vangl2", "outputs/figures/mechanistic_link_atf3_footprint_rgs3_vangl2.png"),
)


def _memory_figure_project(monkeypatch, tmp_path):
    project = tmp_path / "project"
    round_dir = project / "checkpoint" / "checkpoints" / "round_2"
    code_dir = project / "code"
    figure = code_dir / "outputs" / "figures" / "validation_cancer_risk.png"
    round_dir.mkdir(parents=True)
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"trusted-figure")
    (round_dir / "round_report.md").write_text(
        "# Round 2\n\n## Round Report\n\nCompleted research.",
        encoding="utf-8",
    )
    (round_dir / "memory_report.md").write_text(
        "# Memory\n\n## Accumulated Report\n\n"
        "The cumulative study now contains enough substantive scientific context "
        "to render the current understanding on Project Home.\n\n"
        "![Validation Cancer Risk]"
        "(/lustre/projects/optic-nerve/outputs/figures/validation_cancer_risk.png)\n",
        encoding="utf-8",
    )
    (round_dir / "config.json").write_text(
        json.dumps({
            "round_idx": 2,
            "config": {
                "working_directory": "/old/local/optic-nerve/code",
                "log_dir": "/old/local/optic-nerve/checkpoint",
                "checkpoint_base_dir": "/old/local/optic-nerve/checkpoint/checkpoints",
            },
        }),
        encoding="utf-8",
    )
    record = {
        "id": "memory-figure-project",
        "checkpoint_dir": str(round_dir),
        "code_dir": str(code_dir),
    }
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    monkeypatch.setattr(
        app_module, "_round_workspace_checkpoint_dir_for_session", lambda: round_dir
    )
    monkeypatch.setattr(app_module, "_active_project_record", lambda: record)
    with app_module._ROUND_WORKSPACE_CACHE_LOCK:
        app_module._ROUND_WORKSPACE_ARTIFACT_CACHE.clear()
    return round_dir, figure


def _authenticated_client():
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["checkpoint_s3_prefix"] = "project"
        flask_session["project_config"] = app_module._derive_project_config("project")
        flask_session["project_label"] = "Optic Nerve Regeneration"
    return client


def _clear_artifact_cache():
    with app_module._ROUND_WORKSPACE_CACHE_LOCK:
        app_module._ROUND_WORKSPACE_ARTIFACT_CACHE.clear()


def _asset_response(client, url):
    parsed = urlsplit(url)
    return client.get(parsed.path + "?" + parsed.query)


def test_memory_report_returns_same_trusted_figure_model_as_round_reports(
    monkeypatch, tmp_path
):
    _round_dir, figure = _memory_figure_project(monkeypatch, tmp_path)
    client = _authenticated_client()

    response = client.get("/api/project_state")
    assert response.status_code == 200
    understanding = response.get_json()["research_understanding"]

    assert understanding["source_round"] == 2
    assert understanding["figures"] == [{
        "token": "rw_fig_0",
        "alt": "Validation Cancer Risk",
        "available": True,
        "url": understanding["figures"][0]["url"],
    }]
    assert understanding["figures"][0]["url"].startswith(
        "/api/round_workspace_artifact?"
    )
    assert "![Validation Cancer Risk](rw-report-figure:rw_fig_0)" in (
        understanding["full_markdown"]
    )
    assert "/lustre/" not in json.dumps(understanding)

    asset_response = client.get(urlsplit(understanding["figures"][0]["url"]).path + "?" + urlsplit(understanding["figures"][0]["url"]).query)
    assert asset_response.status_code == 200
    assert asset_response.data == figure.read_bytes()


def test_historical_analysis_output_figures_map_beneath_registered_code_root(
    monkeypatch, tmp_path
):
    round_dir, existing_figure = _memory_figure_project(monkeypatch, tmp_path)
    code_dir = existing_figure.parents[2]
    direct = code_dir / "analysis_output" / "combined_TF_cassette_summary.png"
    nested = code_dir / "analysis_output" / "nested" / "figure.png"
    direct.parent.mkdir(parents=True)
    nested.parent.mkdir(parents=True)
    direct.write_bytes(b"direct-analysis-output")
    nested.write_bytes(b"nested-analysis-output")
    (round_dir / "memory_report.md").write_text(
        "# Memory\n\n## Accumulated Report\n\n"
        "Current findings have enough scientific context for the Project Home report.\n\n"
        "![Combined TF Cassette Summary]"
        "(/lustre/old/project/analysis_output/combined_TF_cassette_summary.png)\n\n"
        "![Nested Analysis Figure]"
        "(/lustre/old/project/analysis_output/nested/figure.png)\n",
        encoding="utf-8",
    )
    _clear_artifact_cache()

    client = _authenticated_client()
    response = client.get("/api/project_state")
    assert response.status_code == 200
    understanding = response.get_json()["research_understanding"]
    assert [figure["alt"] for figure in understanding["figures"]] == [
        "Combined TF Cassette Summary",
        "Nested Analysis Figure",
    ]
    assert all(figure["available"] for figure in understanding["figures"])
    assert _asset_response(client, understanding["figures"][0]["url"]).data == direct.read_bytes()
    assert _asset_response(client, understanding["figures"][1]["url"]).data == nested.read_bytes()
    assert "/lustre/" not in json.dumps(understanding)


def test_analysis_output_mapping_fails_closed_for_unsafe_or_missing_targets(
    monkeypatch, tmp_path
):
    round_dir, existing_figure = _memory_figure_project(monkeypatch, tmp_path)
    code_dir = existing_figure.parents[2]
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    symlink = code_dir / "analysis_output" / "escaped.png"
    symlink.parent.mkdir(parents=True)
    symlink.symlink_to(outside)
    other_project_figure = tmp_path / "other-project" / "code" / "analysis_output" / "cross.png"
    other_project_figure.parent.mkdir(parents=True)
    other_project_figure.write_bytes(b"cross-project")
    markdown = (
        "# Memory\n\n## Accumulated Report\n\n"
        "Current findings have enough scientific context for the Project Home report.\n\n"
        "![Missing](/lustre/old/project/analysis_output/missing.png)\n\n"
        "![Traversal](/lustre/old/project/analysis_output/../outside.png)\n\n"
        "![Symlink Escape](/lustre/old/project/analysis_output/escaped.png)\n\n"
        f"![Cross Project]({other_project_figure.as_posix()})\n\n"
        "![Unsupported](/lustre/old/project/analysis_output/data.txt)\n"
    )
    (round_dir / "memory_report.md").write_text(markdown, encoding="utf-8")
    _clear_artifact_cache()

    client = _authenticated_client()
    response = client.get("/api/project_state")
    assert response.status_code == 200
    understanding = response.get_json()["research_understanding"]
    figures = {figure["alt"]: figure for figure in understanding["figures"]}
    assert figures["Missing"]["available"] is False
    assert figures["Symlink Escape"]["available"] is False
    assert figures["Cross Project"]["available"] is False
    assert "Traversal" not in figures
    assert all(figure["url"] is None for figure in figures.values())
    assert str(outside) not in json.dumps(understanding)
    assert str(other_project_figure) not in json.dumps(understanding)
    registry = app_module._build_round_workspace_artifact_registry(2)
    registered_paths = {
        path
        for source in registry.values()
        for path in (source.get("files") or {})
    }
    assert "analysis_output/escaped.png" not in registered_paths
    assert "analysis_output/data.txt" not in registered_paths


def test_existing_outputs_and_images_historical_mappings_remain_available(
    monkeypatch, tmp_path
):
    round_dir, output_figure = _memory_figure_project(monkeypatch, tmp_path)
    run_figure = round_dir.parents[1] / "images" / "historical.png"
    run_figure.parent.mkdir(parents=True)
    run_figure.write_bytes(b"historical-run-image")
    (round_dir / "memory_report.md").write_text(
        "# Memory\n\n## Accumulated Report\n\n"
        "Current findings have enough scientific context for the Project Home report.\n\n"
        "![Output Figure](/lustre/old/project/outputs/figures/validation_cancer_risk.png)\n\n"
        "![Run Image](/lustre/old/project/images/historical.png)\n",
        encoding="utf-8",
    )
    _clear_artifact_cache()

    client = _authenticated_client()
    understanding = client.get("/api/project_state").get_json()["research_understanding"]
    assert [figure["available"] for figure in understanding["figures"]] == [True, True]
    assert _asset_response(client, understanding["figures"][0]["url"]).data == output_figure.read_bytes()
    assert _asset_response(client, understanding["figures"][1]["url"]).data == run_figure.read_bytes()


def test_all_known_optic_nerve_memory_figures_map_when_exact_files_exist(
    monkeypatch, tmp_path
):
    round_dir, existing_figure = _memory_figure_project(monkeypatch, tmp_path)
    code_dir = existing_figure.parents[2]
    historical_root = "/lustre/isaac24/scratch/egorrel1/discoveryagent_runs/optic_nerve_data"
    markdown_images = []
    for index, (caption, relative) in enumerate(OPTIC_NERVE_MEMORY_FIGURES):
        target = code_dir.joinpath(*Path(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"figure-{index}".encode("ascii"))
        markdown_images.append(f"![{caption}]({historical_root}/{relative})")
    (round_dir / "memory_report.md").write_text(
        "# Memory\n\n## Accumulated Report\n\n"
        "Current findings have enough scientific context for the Project Home report.\n\n"
        + "\n\n".join(markdown_images)
        + "\n",
        encoding="utf-8",
    )
    _clear_artifact_cache()

    client = _authenticated_client()
    understanding = client.get("/api/project_state").get_json()["research_understanding"]
    assert len(understanding["figures"]) == 23
    assert [figure["alt"] for figure in understanding["figures"]] == [
        caption for caption, _relative in OPTIC_NERVE_MEMORY_FIGURES
    ]
    assert all(figure["available"] for figure in understanding["figures"])
    assert all(_asset_response(client, figure["url"]).status_code == 200 for figure in understanding["figures"])
    assert "/lustre/" not in json.dumps(understanding)

    audit_markdown = "\n\n".join(markdown_images)
    with app_module.app.test_request_context("/"):
        audit_display, audit_figures = app_module._prepare_audit_chat_figures(
            audit_markdown
        )
    assert len(audit_figures) == 23
    assert [figure["alt"] for figure in audit_figures] == [
        caption for caption, _relative in OPTIC_NERVE_MEMORY_FIGURES
    ]
    assert all(figure["available"] for figure in audit_figures)
    assert "/lustre/" not in audit_display


def test_memory_figure_resolution_fails_closed_without_leaking_paths(
    monkeypatch, tmp_path
):
    round_dir, _figure = _memory_figure_project(monkeypatch, tmp_path)
    with app_module.app.test_request_context("/"):
        missing, missing_figures = app_module._prepare_round_workspace_report_figures(
            2,
            "",
            "memory_report.md",
            "![Missing](/lustre/projects/optic-nerve/outputs/figures/missing.png)",
            "memory_report",
        )
        secret, secret_figures = app_module._prepare_round_workspace_report_figures(
            2,
            "",
            "memory_report.md",
            "![Secret](/etc/private.png)",
            "memory_report",
        )
        traversal, traversal_figures = app_module._prepare_round_workspace_report_figures(
            2,
            "",
            "memory_report.md",
            "![Traversal](../../outputs/figures/validation_cancer_risk.png)",
            "memory_report",
        )

    assert "/lustre/" not in missing
    assert missing_figures == [{
        "token": "rw_fig_0", "alt": "Missing", "available": False, "url": None,
    }]
    assert secret == "**Figure unavailable:** Secret"
    assert secret_figures == []
    assert traversal == "**Figure unavailable:** Traversal"
    assert traversal_figures == []
    assert round_dir.exists()


def test_memory_and_round_reports_load_one_shared_collapsible_figure_renderer():
    root = Path(app_module.app.root_path)
    static_root = Path(app_module.app.static_folder)
    home_template = (root / "templates" / "project_home.html").read_text(encoding="utf-8")
    workspace_template = (root / "templates" / "round_workspace.html").read_text(
        encoding="utf-8"
    )
    home_js = (static_root / "project_home.js").read_text(encoding="utf-8")
    workspace_js = (static_root / "round_workspace.js").read_text(encoding="utf-8")
    figure_js = (static_root / "report_figures.js").read_text(encoding="utf-8")

    assert "filename='report_figures.js'" in home_template
    assert "filename='report_figures.js'" in workspace_template
    assert "v2-prose v2-report-markdown" in home_js
    assert "rw-markdown rw-markdown-document v2-report-markdown" in workspace_js
    assert "ReportFigures.renderReportMarkdown(content, fullMarkdown" in home_js
    assert "ReportFigures.renderReportMarkdown(element, text, figures)" in workspace_js
    assert "const details = node('details', 'rw-embedded-figure')" in figure_js
    assert "details.open = false" in figure_js
    assert "title ? 'Figure: ' + title" in figure_js
    assert "Figure unavailable" in figure_js
