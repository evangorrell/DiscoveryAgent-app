"""The demo opens at guidance and resets without altering archival evidence."""
import importlib.util
import json
from pathlib import Path

import pytest

from da_selection_ui import app as app_module

spec = importlib.util.spec_from_file_location(
    "prepare_gw_cd4_demo", Path(__file__).resolve().parents[3] / "tools/prepare_gw_cd4_demo.py"
)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


def test_demo_initial_guidance_and_recoverable_reset(tmp_path, monkeypatch):
    # Ordinary copies avoid requiring APFS in the test environment.
    monkeypatch.setattr(demo.sys, "platform", "linux")
    source = tmp_path / "archive"
    code = source / "code_study"
    (code / "data").mkdir(parents=True)
    (code / "data/README.txt").write_text("Primary human CD4 T cell perturbation atlas.")
    rounds = source / "logs_study/checkpoints"
    for index in (0, 1, 12):
        rd = rounds / f"round_{index}"
        (rd / "query_001").mkdir(parents=True)
        (rd / "config.json").write_text(json.dumps({"config": {
            "dataset_name": "gw_cd4", "task_description": "Investigate relay genes",
        }}))
        (rd / "memory_report.md").write_text("Archived empirical memory")
    (rounds / "round_0/round_report_summary.md").write_text("Exploration summary")
    last = rounds / "round_12"
    (last / "round_report.md").write_text("Real Round 12 findings")
    (last / "generated_hypotheses.json").write_text('{"hypotheses": ["Placeholder"]}')
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    data = tmp_path / "demo"
    demo.prepare(source, data)
    project = json.loads((data / "projects.json").read_text())["projects"][0]
    active = Path(project["checkpoint_dir"]).parent / "round_12"
    assert app_module._v2_project_availability(project)["available"]
    assert app_module._v2_first_round_gate_state(active.parent) is None
    assert app_module._resolve_active_monitor_round(active) == active
    assert app_module._classify_round_state(active) == "round_complete_needs_review"
    assert app_module._v2_latest_hypothesis_set_index(active.parent) is None
    assert not (active / "generated_hypotheses.json").exists()
    assert (active / "round_report.md").read_text() == "Real Round 12 findings"
    assert "archived report unavailable" in (active.parent / "round_1/round_report.md").read_text()
    assert json.loads((active / "config.json").read_text())["config"]["use_deep_research_for_hypothesis_generation"] is False
    assert list((data / "studio-backups").rglob("generated_hypotheses.json"))

    # Relaunch preserves the session; explicit reset archives it, including
    # guidance which otherwise leaks into the next rehearsal.
    (active / "selected_hypothesis.md").write_text("Scientist's selected direction")
    (active / "next_round_instructions.md").write_text("Avoid causal claims")
    (active / "hypothesis_studio_history.json").write_text('{"events": []}')
    demo.prepare(source, data)
    assert (active / "selected_hypothesis.md").exists()
    demo.prepare(source, data, reset=True)
    for name in ("selected_hypothesis.md", "next_round_instructions.md", "hypothesis_studio_history.json"):
        assert not (active / name).exists()
        assert list((data / "studio-backups").rglob(name))
    assert {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()} == before

    (active.parent / "round_13").mkdir()
    with pytest.raises(ValueError, match="execution has started"):
        demo.prepare(source, data, reset=True)


def test_demo_refuses_to_reset_unrecognized_directory(tmp_path):
    data = tmp_path / "demo"
    (data / "projects/gw-cd4-demo").mkdir(parents=True)
    with pytest.raises(ValueError, match="unrecognized existing directory"):
        demo.prepare(tmp_path / "source", data, reset=True)
