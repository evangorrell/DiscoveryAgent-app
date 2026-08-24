"""Read-only Hypothesis Studio round-history endpoints.

The sidebar surfaces each round's hypothesis cycle from artifacts that already
exist on disk (generated_hypotheses.json, selected_hypothesis.md). These tests
lock in that the endpoints read those files, mark exactly one round current,
parse the stored hypothesis sections, and never fabricate a transcript.

Temporary filesystem roots only; no live LLM, no S3.
"""
import json

from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    return client


def _create(client, name="Optic Nerve Regeneration"):
    return client.post(
        "/api/projects",
        data={"project_name": name, "task_description": "Goal.", "dataset_context": "Ctx."},
        content_type="multipart/form-data",
    ).get_json()


def _round_root(tmp_path):
    proj = app_module._v2_load_projects()[0]
    from pathlib import Path
    ckpt = Path(proj["checkpoint_dir"])
    return ckpt.parent if ckpt.name.startswith("round_") else ckpt


HYP = ("**Title**: Axon growth\n**Hypothesis**: RGC survival depends on mTOR reactivation.\n"
       "**Justification**: Prior work links mTOR to regeneration.\n**Plan**: Compare mTOR-high vs low cells.")


def _seed_round(root, n, generated=None, selected=None, report=False):
    rd = root / f"round_{n}"
    rd.mkdir(parents=True, exist_ok=True)
    if generated is not None:
        rd.joinpath("generated_hypotheses.json").write_text(
            json.dumps({"hypotheses": generated, "hypothesis_state_ids": [f"s{i}" for i in range(len(generated))]})
        )
    if selected is not None:
        rd.joinpath("selected_hypothesis.md").write_text(selected)
    if report:
        rd.joinpath("round_report.md").write_text("# Round Report\n\nFindings.")
    return rd


def test_history_lists_rounds_and_marks_one_current(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client)
    root = _round_root(tmp_path)
    _seed_round(root, 0, generated=[HYP, "**Hypothesis**: Two."], selected="**Hypothesis**: RGC survival.", report=True)
    # round_1's report completes the investigation driven by round_0's set;
    # its generated set is the new, still-exploring Studio Round 2.
    _seed_round(root, 1, generated=[HYP], report=True)

    data = client.get("/api/hypothesis_history").get_json()
    assert data["success"] is True
    labels = {r["round_index"]: r for r in data["rounds"]}
    assert set(labels) == {0, 1}
    assert labels[0]["status"] == "completed" and labels[0]["has_selected"] is True
    assert labels[1]["status"] == "exploring" and labels[1]["has_selected"] is False
    # Exactly one current round, and it is the resolved active cycle.
    current = [r for r in data["rounds"] if r["is_current"]]
    assert len(current) == 1
    assert current[0]["round_index"] == data["current_round_index"]


def test_history_round_parses_generated_and_selected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client)
    root = _round_root(tmp_path)
    _seed_round(root, 0, generated=[HYP], selected="**Hypothesis**: RGC survival depends on mTOR.", report=True)

    data = client.get("/api/hypothesis_history/0").get_json()
    assert data["success"] is True
    gen = data["generated_hypotheses"]
    assert len(gen) == 1
    assert gen[0]["title"] == "Axon growth"
    assert "mTOR" in gen[0]["hypothesis"]
    assert gen[0]["plan"]  # plan section parsed
    assert "mTOR" in (data["selected_hypothesis"]["hypothesis"] or "")
    # Ordered transcript of substantive events: the generated set, then the
    # closing selection. Chat turns aren't stored yet, so none are fabricated.
    kinds = [e["kind"] for e in data["transcript"]]
    assert kinds == ["hypotheses", "selection"]
    assert data["transcript"][-1]["hypothesis"]["hypothesis"]  # closing selection event


def test_history_round_ignores_legacy_pre_generation_transcript(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client)
    root = _round_root(tmp_path)
    rd = _seed_round(root, 0, generated=[HYP], selected="**Hypothesis**: RGC survival.", report=True)
    # Legacy pre-generation interview artifacts are no longer part of the
    # Hypothesis Studio lifecycle.
    rd.joinpath("hypothesis_transcript.json").write_text(json.dumps({"messages": [
        {"role": "user", "content": "What should we look at next?"},
        {"role": "assistant", "content": "Let's focus on mTOR reactivation."},
    ]}))

    data = client.get("/api/hypothesis_history/0").get_json()
    kinds = [e["kind"] for e in data["transcript"]]
    assert kinds == ["hypotheses", "selection"]


def test_history_transcript_preserves_revision_ordering(monkeypatch, tmp_path):
    # A durable event log with a revision must round-trip through the endpoint in
    # order: generated set -> scientist request message -> revised hypothesis ->
    # closing selection. Old versions are never reconstructed from current state.
    from da_selection_ui import hypothesis_history as hh
    client = _client(monkeypatch, tmp_path)
    _create(client)
    root = _round_root(tmp_path)
    rd = _seed_round(root, 0, generated=[HYP], selected="**Hypothesis**: RGC survival.", report=True)

    before = {"index": 2, "hypothesis": "H2 v1", "state_id": "s2-v1"}
    after = {"index": 2, "hypothesis": "H2 v2 focused", "state_id": "s2-v2"}
    hh.append_events(rd, [
        hh.hypothesis_set_generated_event([{"index": 1, "hypothesis": "H1"}, before]),
        hh.user_message_event("Make hypothesis 2 more focused on poised-but-unfired loci.",
                              hh.PHASE_HYPOTHESIS_DISCUSSION),
        hh.hypothesis_revised_event(2, before, after),
        hh.hypothesis_selected_event(after),
    ])

    data = client.get("/api/hypothesis_history/0").get_json()
    kinds = [e["kind"] for e in data["transcript"]]
    assert kinds == ["hypotheses", "message", "hypothesis_revised", "selection"]
    # The scientist's exact request is preserved directly above the revision.
    assert "poised-but-unfired" in data["transcript"][1]["content"]
    # The revision carries the new (active) version, not the original.
    rev = data["transcript"][2]
    assert rev["hypothesis_index"] == 2
    assert rev["after"]["hypothesis"] == "H2 v2 focused"
    assert rev["before"]["hypothesis"] == "H2 v1"


def test_history_unknown_round_404(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _create(client)
    assert client.get("/api/hypothesis_history/9").status_code == 404


def test_history_available_without_auth(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()  # unauthenticated
    assert client.get("/api/hypothesis_history").status_code == 200
    assert client.get("/api/hypothesis_history/0").status_code == 404
