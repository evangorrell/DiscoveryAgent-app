"""Studio round-binding invariant: every current Hypothesis Studio operation uses
the SAME active Studio storage directory (product Round N -> storage round_{N-1}),
never splitting the generator and the durable history across two round dirs.

Covers: current-round resolution, generator restore preferring the active round's
generated_hypotheses.json and falling back to that round's durable history, and
that persistence follows the generator's (active) checkpoint_dir — not the
project registry's original round_0. No paid model calls.
"""
import json
import types
from pathlib import Path

from da_selection_ui import app as app_module
from da_selection_ui import hypothesis_history
from da_selection_ui import select_hypothesis as SH


def _hyp(idx, extra=""):
    return f"**Title**: Title {idx}\n**Hypothesis**: Statement {idx} {extra}\n**Justification**: Because {idx}."


def _bare_generator(checkpoint_dir):
    """A real InteractiveHypothesisGenerator with just the attributes the restore
    / persist helpers touch (no agent, no LLM)."""
    g = SH.InteractiveHypothesisGenerator.__new__(SH.InteractiveHypothesisGenerator)
    g.checkpoint_dir = Path(checkpoint_dir)
    g.hypotheses = []
    g.hypothesis_state_ids = []
    g.hypothesis_thread_ids = []
    g._refinement_baseline_snapshot = None
    g.hypothesis_model_name = "test-model"
    g.hypothesis_config = types.SimpleNamespace(llm_config=None)
    g.interaction_log = []
    g.log_interaction = lambda *a, **k: None
    return g


def _make_multi_round_project(tmp_path, monkeypatch, completed=3):
    """Create a V2 project and mark round_0..round_{completed} complete (each with a
    round_report.md), so the latest completed autonomous round is round_{completed}
    and the current Studio round is product Round {completed+1}, stored there."""
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    client.post("/api/projects", data={"project_name": "Multi", "task_description": "Goal.",
                                        "dataset_context": "Ctx."}, content_type="multipart/form-data")
    reg = app_module._v2_load_projects()[0]
    ckpt = Path(reg["checkpoint_dir"])          # .../checkpoints/round_0
    root = ckpt.parent                          # .../checkpoints
    for n in range(0, completed + 1):
        rd = root / f"round_{n}"
        rd.mkdir(parents=True, exist_ok=True)
        rd.joinpath("round_report.md").write_text(f"# Round {n} Report\n", encoding="utf-8")
        rd.joinpath("memory_report.md").write_text(f"# Memory {n}\n", encoding="utf-8")
    return client, reg, root


# ---------------------------------------------------------------------------
# 1. current Studio round resolution
# ---------------------------------------------------------------------------

def test_current_studio_round_is_latest_completed(monkeypatch, tmp_path):
    client, reg, root = _make_multi_round_project(tmp_path, monkeypatch, completed=3)
    resolved = app_module._v2_resolve_current_studio_checkpoint_dir(Path(reg["checkpoint_dir"]))
    assert resolved == root / "round_3"
    # Product Round N is stored in round_{N-1}: round_3 -> Round 4.
    assert app_module._v2_target_round_number(3) == 4


def test_single_round_project_resolves_to_round_0(monkeypatch, tmp_path):
    """No regression for the pre-/single-round case: a project with only round_0
    (no completed report) resolves to round_0."""
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    client.post("/api/projects", data={"project_name": "Solo", "task_description": "Goal.",
                                        "dataset_context": "Ctx."}, content_type="multipart/form-data")
    reg = app_module._v2_load_projects()[0]
    ckpt = Path(reg["checkpoint_dir"])
    assert app_module._v2_resolve_current_studio_checkpoint_dir(ckpt) == ckpt


# ---------------------------------------------------------------------------
# 2. /api/initialize binds the generator to the active Studio round
# ---------------------------------------------------------------------------

def test_initialize_binds_generator_to_active_studio_round(monkeypatch, tmp_path):
    client, reg, root = _make_multi_round_project(tmp_path, monkeypatch, completed=3)
    slug = reg["url_slug"]
    client.get(f"/projects/{slug}/hypotheses")  # activate the project in the session

    captured = {}

    def fake_create_generator(checkpoint_dir, *a, **k):
        captured["checkpoint_dir"] = checkpoint_dir
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(app_module, "create_generator", fake_create_generator)
    client.post("/api/initialize", json={})
    # The registry checkpoint_dir is round_0, but generation must bind to round_3.
    assert Path(captured["checkpoint_dir"]).name == "round_3"


# ---------------------------------------------------------------------------
# 3. restore prefers the active round's generated_hypotheses.json, then history
# ---------------------------------------------------------------------------

def test_restore_reconstructs_active_set_from_history_when_json_missing(tmp_path):
    rd = tmp_path / "round_3"
    rd.mkdir(parents=True)
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([
            {"index": 1, "full_text": _hyp(1), "state_id": "sid1"},
            {"index": 2, "full_text": _hyp(2), "state_id": "sid2"},
        ]),
        # A later revision of hypothesis 1 must win (active-version rule).
        hypothesis_history.hypothesis_revised_event(
            1, {"index": 1, "full_text": _hyp(1)},
            {"index": 1, "full_text": _hyp(1, "REVISED"), "state_id": "sid1b"}),
    ])
    gen = _bare_generator(rd)

    restored = app_module._restore_generated_hypotheses_if_available(gen)
    assert restored is not None and len(restored) == 2
    # The revised hypothesis 1 and untouched hypothesis 2, with history state ids.
    assert "REVISED" in gen.hypotheses[0]
    assert restored[0]["state_id"] == "sid1b"
    assert restored[1]["state_id"] == "sid2"
    assert gen.hypothesis_state_ids == ["sid1b", "sid2"]


def test_restore_prefers_generated_json_over_history(tmp_path):
    rd = tmp_path / "round_3"
    rd.mkdir(parents=True)
    # History says one thing...
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{"index": 1, "full_text": _hyp(1, "FROM_HISTORY")}]),
    ])
    # ...but the operational cache (json) is authoritative when present.
    (rd / "generated_hypotheses.json").write_text(json.dumps({
        "hypotheses": [_hyp(1, "FROM_JSON")],
        "hypothesis_state_ids": ["json-sid"],
    }), encoding="utf-8")
    gen = _bare_generator(rd)
    restored = app_module._restore_generated_hypotheses_if_available(gen)
    assert restored is not None and len(restored) == 1
    assert "FROM_JSON" in gen.hypotheses[0]


def test_restore_keeps_provisional_selection_editable_before_launch(tmp_path):
    rd = tmp_path / "round_3"
    rd.mkdir(parents=True)
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{"index": 1, "full_text": _hyp(1)}]),
    ])
    (rd / "selected_hypothesis.md").write_text("**Hypothesis**: chosen", encoding="utf-8")
    gen = _bare_generator(rd)
    restored = app_module._restore_generated_hypotheses_if_available(gen)
    assert restored is not None and len(restored) == 1


def test_restore_refuses_committed_set_after_target_round_starts(tmp_path):
    rd = tmp_path / "round_3"
    rd.mkdir(parents=True)
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{"index": 1, "full_text": _hyp(1)}]),
    ])
    (rd / "selected_hypothesis.md").write_text("**Hypothesis**: chosen", encoding="utf-8")
    (tmp_path / "round_4").mkdir()
    gen = _bare_generator(rd)
    assert app_module._restore_generated_hypotheses_if_available(gen) is None


# ---------------------------------------------------------------------------
# 4. persistence follows the generator's active checkpoint_dir (round_3)
# ---------------------------------------------------------------------------

def test_persist_writes_to_active_round_not_round_0(tmp_path):
    root = tmp_path / "checkpoints"
    round0 = root / "round_0"
    round3 = root / "round_3"
    round0.mkdir(parents=True)
    round3.mkdir(parents=True)

    gen = _bare_generator(round3)
    gen.hypotheses = [_hyp(1), _hyp(2)]
    gen.hypothesis_state_ids = ["sid1", "sid2"]
    gen.hypothesis_thread_ids = ["thr1", "thr2"]
    gen.persist_generated_hypotheses()

    assert (round3 / "generated_hypotheses.json").exists()
    assert (round3 / "generated_hypotheses.md").exists()
    assert not (round0 / "generated_hypotheses.json").exists()
    payload = json.loads((round3 / "generated_hypotheses.json").read_text())
    assert payload["hypothesis_state_ids"] == ["sid1", "sid2"]


def test_studio_history_round_dir_matches_active_round(monkeypatch, tmp_path):
    client, reg, root = _make_multi_round_project(tmp_path, monkeypatch, completed=3)
    slug = reg["url_slug"]
    client.get(f"/projects/{slug}/hypotheses")  # activate project in session
    gen = _bare_generator(root / "round_3")
    with app_module.app.test_request_context():
        # Re-activate inside this request context so session lookups resolve.
        from flask import session as flask_session
        flask_session["authenticated"] = True
        flask_session["v2_active_project_id"] = reg["id"]
        flask_session["project_config"] = {"checkpoint_dir": reg["checkpoint_dir"]}
        history_dir = app_module._studio_history_round_dir(gen)
    assert Path(history_dir).name == "round_3"
