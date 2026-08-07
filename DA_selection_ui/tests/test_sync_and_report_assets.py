from datetime import datetime, timezone
from pathlib import Path

from da_selection_ui import app as app_module
from da_selection_ui.select_hypothesis import InteractiveHypothesisGenerator
from da_selection_ui.utils import pull_s3 as pull_s3_module


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


def test_s3_uri_project_prefix_uses_project_relative_cache_path(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)

    assert app_module._disk_root_for_prefix("s3://bucket/project") == tmp_path / "project"
    assert app_module._checkpoint_sync_root("s3://bucket/project") == tmp_path / "project" / "checkpoint"
    assert app_module._checkpoint_prefix_from_raw("s3://bucket/project") == ("bucket", "project/checkpoint/")


def test_pull_s3_accepts_full_s3_uri_for_code_sync(monkeypatch, tmp_path):
    captured = {}

    class FakeObject:
        key = "project/code/reports/figures/example.png"
        size = 8
        e_tag = '"etag-1"'
        last_modified = datetime(2026, 5, 29, tzinfo=timezone.utc)

    class FakeObjects:
        def filter(self, Prefix):
            captured["prefix"] = Prefix
            return [FakeObject()]

    class FakeBucket:
        objects = FakeObjects()

        def download_file(self, key, target_path):
            captured["download"] = (key, target_path)
            Path(target_path).write_bytes(b"fake-png")

    class FakeS3:
        def Bucket(self, bucket_name):
            captured["bucket"] = bucket_name
            return FakeBucket()

    class FakeBoto3:
        def resource(self, service_name, config=None):
            assert service_name == "s3"
            return FakeS3()

    monkeypatch.setattr(pull_s3_module, "boto3", FakeBoto3())
    monkeypatch.setattr(pull_s3_module, "Config", None)
    monkeypatch.setattr(pull_s3_module, "TransferConfig", None)
    monkeypatch.setenv("DISK_PATH", str(tmp_path))
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)

    pull_s3_module.sync_checkpoint_code_from_s3_to_disk(
        "s3://bucket/project",
        include_code=True,
        include_checkpoint=False,
    )

    assert captured["bucket"] == "bucket"
    assert captured["prefix"] == "project/code/"
    assert captured["download"] == (
        "project/code/reports/figures/example.png",
        str(tmp_path / "project" / "code" / "reports" / "figures" / "example.png"),
    )
    assert (tmp_path / "project" / "code" / ".s3sync.json").exists()


def test_compute_s3_sync_status_waits_for_completion_marker(monkeypatch, tmp_path):
    _build_project_tree(tmp_path, rounds=(0,))
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    monkeypatch.setattr(app_module, "boto3", object())
    monkeypatch.setattr(app_module, "DISABLE_S3_SYNC", False)

    calls = []

    def fake_start(digest, checkpoint_s3_prefix, target_root=None):
        calls.append((digest, checkpoint_s3_prefix, target_root))

    monkeypatch.setattr(app_module, "start_sequential_checkpoint_sync", fake_start)

    with app_module.app.test_request_context("/api/s3_sync_status"):
        app_module.session["authenticated"] = True
        app_module.session["project_key_digest"] = "digest-1"
        app_module.session["checkpoint_s3_prefix"] = "project"
        app_module.session["project_config"] = app_module._derive_project_config("project")
        app_module.session["checkpoint_sync_requested_at"] = "2026-04-15T12:00:00Z"

        payload = app_module._compute_s3_sync_status()
        assert payload["required"] is True
        assert payload["state"] == "pending"
        assert calls == [("digest-1", "project", str(tmp_path / "project" / "checkpoint"))]

        marker = app_module._sync_completion_marker_path("project")
        marker.write_text('{"finished_at":"2026-04-15T12:00:05Z"}', encoding="utf-8")
        app_module.session["checkpoint_sync_requested_at"] = "2026-04-15T12:00:00Z"

        payload = app_module._compute_s3_sync_status()
        assert payload["required"] is False
        assert payload["state"] == "not_required"
        assert "checkpoint_sync_requested_at" not in app_module.session


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
