from pathlib import Path

from da_selection_ui import app as app_module
from da_selection_ui.select_hypothesis import InteractiveHypothesisGenerator


def _build_project_tree(base: Path, prefix: str = "project", rounds=(0,)) -> Path:
    root = base / prefix
    code_dir = root / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "README.txt").write_text("dataset readme", encoding="utf-8")
    image_path = code_dir / "reports" / "figures" / "example.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfakepng")

    checkpoints_root = root / "checkpoint" / "checkpoints"
    for round_index in rounds:
        round_dir = checkpoints_root / f"round_{round_index}"
        round_dir.mkdir(parents=True, exist_ok=True)
        (round_dir / "config.json").write_text('{"config": {}}', encoding="utf-8")
        (round_dir / "memory_report.md").write_text(
            "![Example](reports/figures/example.png)",
            encoding="utf-8",
        )
    return root


def test_get_project_config_rederives_to_latest_round(monkeypatch, tmp_path):
    _build_project_tree(tmp_path, rounds=(0,))
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)

    with app_module.app.test_request_context("/"):
        session_cfg = app_module._derive_project_config("project")
        assert session_cfg["checkpoint_dir"].endswith("round_0")

        app_module.session["checkpoint_s3_prefix"] = "project"
        app_module.session["project_config"] = dict(session_cfg)

        _build_project_tree(tmp_path, rounds=(0, 1))
        refreshed = app_module._get_project_config()

        assert refreshed["checkpoint_dir"].endswith("round_1")
        assert app_module.session["project_config"]["checkpoint_dir"].endswith("round_1")


def test_report_and_serve_asset_routes_work_without_generator(monkeypatch, tmp_path):
    _build_project_tree(tmp_path, rounds=(0,))
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)

    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["checkpoint_s3_prefix"] = "project"
        flask_session["project_config"] = app_module._derive_project_config("project")

    response = client.get("/report_assets/code/reports/figures/example.png")
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")

    response = client.get("/api/serve_asset", query_string={"path": "reports/figures/example.png"})
    assert response.status_code == 200
    assert response.data.startswith(b"\x89PNG")


def test_configured_research_paper_pdf_overrides_latest(monkeypatch, tmp_path):
    root = _build_project_tree(tmp_path, rounds=(0,))
    checkpoint_dir = root / "checkpoint" / "checkpoints" / "round_0"
    latest_dir = root / "checkpoint" / "research_paper_20260518_120000"
    latest_dir.mkdir(parents=True)
    (latest_dir / "research_paper.pdf").write_bytes(b"latest")
    manual_dir = root / "checkpoint" / "research_paper_manual_0515"
    manual_dir.mkdir(parents=True)
    manual_pdf = manual_dir / "research_paper.pdf"
    manual_pdf.write_bytes(b"manual")

    resolved = app_module._resolve_research_paper_pdf_path(
        None,
        checkpoint_dir,
        "research_paper_manual_0515",
    )

    assert resolved == manual_pdf


def test_generator_resolves_configured_research_paper_path(tmp_path):
    root = _build_project_tree(tmp_path, rounds=(0,))
    checkpoint_dir = root / "checkpoint" / "checkpoints" / "round_0"
    latest_dir = root / "checkpoint" / "research_paper_20260518_120000"
    latest_dir.mkdir(parents=True)
    (latest_dir / "research_paper.md").write_text("latest paper", encoding="utf-8")
    manual_dir = root / "checkpoint" / "research_paper_manual_0515"
    manual_dir.mkdir(parents=True)
    manual_md = manual_dir / "research_paper.md"
    manual_md.write_text("manual paper", encoding="utf-8")

    generator = InteractiveHypothesisGenerator(
        str(checkpoint_dir),
        register_signal_handlers=False,
        research_paper_path="research_paper_manual_0515",
    )

    assert generator._resolve_configured_research_paper() == manual_md
