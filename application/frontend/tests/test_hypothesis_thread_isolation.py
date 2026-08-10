"""Per-hypothesis refinement-thread context-isolation invariants (V2).

These prove the three audited invariants:

  1. STABLE THREAD IDENTITY  — removing a hypothesis and reindexing the
     survivors cannot attach the wrong historical thread after a restart,
     because durable attribution is by a stable thread id, not display index.
  2. GENERATION-TIME BASELINE — every thread clones the same V1 baseline
     captured once at generation, even a thread first opened after another
     hypothesis was heavily revised.
  3. SHARED-AGENT CONCURRENCY — the per-session operation lock serializes the
     save/install/call/persist/restore swap on the one shared HypothesisGenAgent
     so overlapping requests cannot interleave message state.

Plus the lifecycle rules: single-hypothesis regeneration clears only that
thread; full-set regeneration clears all threads.
"""

import threading
import time

from da_selection_ui import app as app_module
from da_selection_ui import hypothesis_history
from da_selection_ui import select_hypothesis as SH


def _hyp(idx, extra=""):
    return (
        f"**Title**: Title {idx}\n"
        f"**Hypothesis**: Statement {idx} {extra}\n"
        f"**Justification**: Justification {idx}."
    )


class _FakeAgent:
    def __init__(self):
        self.messages = []
        self.system_prompt = "SYS"
        self.llm_config = type("cfg", (), {"api_provider": "anthropic", "model_name": "m"})()
        self.last_response_citations = []

    def reset_conversation(self):
        self.messages = []

    def add_user_message(self, c):
        self.messages.append({"role": "user", "content": c})

    def add_assistant_message(self, c):
        self.messages.append({"role": "assistant", "content": c})


def _make_generator(tmp_path, hyps):
    g = SH.InteractiveHypothesisGenerator.__new__(SH.InteractiveHypothesisGenerator)
    g.hypothesis_gen_agent = _FakeAgent()
    g.hypotheses = list(hyps)
    g.hypothesis_chat_threads = {}
    g.hypothesis_thread_ids = []
    g.hypothesis_state_ids = []
    g._active_thread_key = None
    g._thread_swap_lock = threading.RLock()
    g._refinement_baseline_snapshot = None
    g._hypothesis_chat_context_fingerprint = None
    g.task_description = "Objective X"
    g.dataset_description = "README Y"
    g.memory_report = None
    g.round_report = None
    g.round_report_summary = None
    g.deep_research_background = None
    g.hypothesis_literature_context = None
    g.audit_only_mode = False
    g.checkpoint_dir = tmp_path
    g.interaction_log = []
    g.log_interaction = lambda *a, **k: None
    return g


def _thread_texts(g, key):
    return " ".join(m["content"] for m in g.hypothesis_chat_threads.get(key, []))


# ---------------------------------------------------------------------------
# 2. GENERATION-TIME BASELINE SNAPSHOT
# ---------------------------------------------------------------------------

def test_thread_opened_after_revision_uses_generation_time_baseline(tmp_path):
    g = _make_generator(tmp_path, [_hyp(1, "ORIGINAL"), _hyp(2), _hyp(3)])
    # Generation captures the baseline once.
    snapshot = g.capture_refinement_baseline()
    assert "ORIGINAL" in snapshot and "Statement 2" in snapshot

    # Heavily revise H1 in place (as edit/regenerate would mutate self.hypotheses).
    g.hypotheses[0] = _hyp(1, "REVISED-HEAVILY-with-new-mechanisms")

    app_module._ensure_hypothesis_thread_ids(g)
    key2 = app_module._hypothesis_thread_key_for_index(g, 2)
    # First time H2 is opened — its baseline must be the generation-time snapshot,
    # NOT a rebuild from the mutated (revised) hypotheses.
    with g.use_hypothesis_thread(key2):
        baseline_msg = g.hypothesis_gen_agent.messages[0]["content"]
    assert baseline_msg == snapshot
    assert "REVISED-HEAVILY" not in baseline_msg
    assert "ORIGINAL" in baseline_msg


def test_restored_baseline_snapshot_is_preferred_over_rebuild(tmp_path):
    g = _make_generator(tmp_path, [_hyp(1)])
    g._refinement_baseline_snapshot = "FROZEN GENERATION BASELINE"
    # Even though current hypotheses differ, the snapshot wins.
    g.hypotheses[0] = _hyp(1, "changed")
    assert g._refinement_baseline() == "FROZEN GENERATION BASELINE"
    # Without a snapshot it falls back to a rebuild.
    g._refinement_baseline_snapshot = None
    assert "Statement 1" in g._refinement_baseline()


def test_refinement_baselines_do_not_label_items_with_internal_indexes(tmp_path):
    g = _make_generator(tmp_path, [_hyp(1), _hyp(2)])
    baseline = g.capture_refinement_baseline()
    assert "### Hypothesis 1" not in baseline
    assert "### Hypothesis 2" not in baseline
    assert baseline.count("### Generated hypothesis") == 2

    g._refinement_baseline_snapshot = "### Hypothesis 7\nlegacy content"
    sanitized = g._refinement_baseline()
    assert "Hypothesis 7" not in sanitized
    assert sanitized.startswith("### Generated hypothesis")


# ---------------------------------------------------------------------------
# 1. STABLE THREAD IDENTITY across remove + reindex + restart
# ---------------------------------------------------------------------------

def test_remove_reindex_and_restart_preserves_correct_threads(tmp_path, monkeypatch):
    round_dir = tmp_path
    monkeypatch.setattr(app_module, "_studio_history_round_dir", lambda gen: round_dir)

    g = _make_generator(tmp_path, [_hyp(1), _hyp(2), _hyp(3)])
    app_module._ensure_hypothesis_thread_ids(g)
    t1, t2, t3 = list(g.hypothesis_thread_ids)

    # Discussion recorded for H2 (index 2) and H3 (index 3): durable messages are
    # tagged with the stable thread id of the hypothesis they concern.
    app_module._studio_record_chat(g, "about H2 thread B", "reply about H2", 2)
    app_module._studio_record_chat(g, "about H3 thread C", "reply about H3", 3)

    # Remove H1: drop slot 0 from hypotheses and the parallel thread-id list.
    del g.hypotheses[0]
    del g.hypothesis_thread_ids[0]
    app_module._ensure_hypothesis_thread_ids(g)
    assert g.hypothesis_thread_ids == [t2, t3]

    # --- Simulate a process restart: brand-new generator, threads all lost,
    #     thread ids reloaded from persistence (order [t2, t3]). ---
    g2 = _make_generator(tmp_path, [_hyp(2), _hyp(3)])
    g2.hypothesis_thread_ids = [t2, t3]
    monkeypatch.setattr(app_module, "_studio_history_round_dir", lambda gen: round_dir)

    # H2 is now display index 1 -> stable key t2 -> thread B.
    assert app_module._hypothesis_thread_key_for_index(g2, 1) == t2
    msgs_b = app_module._restore_hypothesis_thread_messages(g2, t2)
    joined_b = " ".join(m["content"] for m in msgs_b)
    assert "thread B" in joined_b and "thread C" not in joined_b

    # H3 is now display index 2 -> stable key t3 -> thread C.
    assert app_module._hypothesis_thread_key_for_index(g2, 2) == t3
    msgs_c = app_module._restore_hypothesis_thread_messages(g2, t3)
    joined_c = " ".join(m["content"] for m in msgs_c)
    assert "thread C" in joined_c and "thread B" not in joined_c

    # The removed hypothesis's old thread (t1) must never attach to new index 1.
    assert app_module._restore_hypothesis_thread_messages(g2, t1) == []


def test_h1_and_h2_isolated_across_restart(tmp_path, monkeypatch):
    round_dir = tmp_path
    monkeypatch.setattr(app_module, "_studio_history_round_dir", lambda gen: round_dir)

    g = _make_generator(tmp_path, [_hyp(1), _hyp(2)])
    app_module._ensure_hypothesis_thread_ids(g)
    t1, t2 = list(g.hypothesis_thread_ids)

    # Ten messages refining H1, then one about H2.
    for i in range(5):
        app_module._studio_record_chat(g, f"H1 question {i}", f"H1 answer {i}", 1)
    app_module._studio_record_chat(g, "H2 question", "H2 answer", 2)

    # Restart.
    g2 = _make_generator(tmp_path, [_hyp(1), _hyp(2)])
    g2.hypothesis_thread_ids = [t1, t2]
    monkeypatch.setattr(app_module, "_studio_history_round_dir", lambda gen: round_dir)

    restored_h1 = app_module._restore_hypothesis_thread_messages(g2, t1)
    restored_h2 = app_module._restore_hypothesis_thread_messages(g2, t2)
    assert len(restored_h1) == 10  # 5 Q + 5 A
    assert all("H2" not in m["content"] for m in restored_h1)
    assert len(restored_h2) == 2
    assert all("H1" not in m["content"] for m in restored_h2)


# ---------------------------------------------------------------------------
# lifecycle: single regenerate clears only that thread; full regenerate all
# ---------------------------------------------------------------------------

def test_single_regeneration_clears_only_that_thread(tmp_path):
    g = _make_generator(tmp_path, [_hyp(1), _hyp(2), _hyp(3)])
    g.capture_refinement_baseline()
    app_module._ensure_hypothesis_thread_ids(g)

    keys = {i: app_module._hypothesis_thread_key_for_index(g, i) for i in (1, 2, 3)}
    for i, key in keys.items():
        with g.use_hypothesis_thread(key):
            g.hypothesis_gen_agent.add_user_message(f"note {i}")

    old_key2 = keys[2]
    app_module._regenerate_reset_hypothesis_thread(g, 2)

    # H2's old thread is gone and its thread id rotated.
    assert old_key2 not in g.hypothesis_chat_threads
    new_key2 = app_module._hypothesis_thread_key_for_index(g, 2)
    assert new_key2 != old_key2

    # H1 and H3 threads are untouched.
    assert keys[1] in g.hypothesis_chat_threads
    assert keys[3] in g.hypothesis_chat_threads
    assert "note 1" in _thread_texts(g, keys[1])
    assert "note 3" in _thread_texts(g, keys[3])

    # Re-opening H2 yields a clean baseline-only thread (no old "note 2").
    with g.use_hypothesis_thread(new_key2):
        joined = " ".join(m["content"] for m in g.hypothesis_gen_agent.messages)
    assert "note 2" not in joined


def test_regeneration_rotates_thread_id_so_restart_does_not_resurrect(tmp_path, monkeypatch):
    round_dir = tmp_path
    monkeypatch.setattr(app_module, "_studio_history_round_dir", lambda gen: round_dir)

    g = _make_generator(tmp_path, [_hyp(1), _hyp(2)])
    app_module._ensure_hypothesis_thread_ids(g)
    old_key2 = app_module._hypothesis_thread_key_for_index(g, 2)

    # Pre-regeneration discussion tagged with the OLD thread id.
    app_module._studio_record_chat(g, "stale H2 talk", "stale reply", 2)

    # Regenerate H2: rotate its stable thread id.
    app_module._regenerate_reset_hypothesis_thread(g, 2)
    new_key2 = app_module._hypothesis_thread_key_for_index(g, 2)

    # The regenerated hypothesis's fresh thread (new id) collects nothing from
    # the discarded thread; only the old id would, and it is no longer mapped.
    assert app_module._restore_hypothesis_thread_messages(g, new_key2) == []
    assert "stale H2 talk" in " ".join(
        m["content"] for m in app_module._restore_hypothesis_thread_messages(g, old_key2)
    )


def test_full_regeneration_clears_all_threads(tmp_path):
    g = _make_generator(tmp_path, [_hyp(1), _hyp(2), _hyp(3)])
    g.capture_refinement_baseline()
    app_module._ensure_hypothesis_thread_ids(g)
    for i in (1, 2, 3):
        key = app_module._hypothesis_thread_key_for_index(g, i)
        with g.use_hypothesis_thread(key):
            g.hypothesis_gen_agent.add_user_message(f"note {i}")
    assert g.hypothesis_chat_threads

    # A full regeneration replaces the whole set: threads dropped, new baseline.
    g.clear_all_hypothesis_threads()
    app_module._reset_hypothesis_thread_ids(g)
    g.hypotheses = [_hyp(1, "brand new set")]
    g.capture_refinement_baseline()

    assert g.hypothesis_chat_threads == {}
    assert "brand new set" in g._refinement_baseline_snapshot


# ---------------------------------------------------------------------------
# 3. SHARED-AGENT CONCURRENCY
# ---------------------------------------------------------------------------

def test_use_thread_restores_messages_even_on_exception(tmp_path):
    g = _make_generator(tmp_path, [_hyp(1)])
    g.capture_refinement_baseline()
    app_module._ensure_hypothesis_thread_ids(g)
    key = app_module._hypothesis_thread_key_for_index(g, 1)
    sentinel = [{"role": "assistant", "content": "outer"}]
    g.hypothesis_gen_agent.messages = sentinel
    try:
        with g.use_hypothesis_thread(key):
            assert g.hypothesis_gen_agent.messages is g.hypothesis_chat_threads[key]
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    # The shared agent's messages are restored, and the active-thread guard clears.
    assert g.hypothesis_gen_agent.messages is sentinel
    assert g._active_thread_key is None


def test_per_session_lock_serializes_thread_swaps(tmp_path):
    """With the existing per-session operation lock held, two overlapping
    operations on the ONE shared agent never observe each other's thread in
    hypothesis_gen_agent.messages."""
    g = _make_generator(tmp_path, [_hyp(1), _hyp(2)])
    g.capture_refinement_baseline()
    app_module._ensure_hypothesis_thread_ids(g)
    key1 = app_module._hypothesis_thread_key_for_index(g, 1)
    key2 = app_module._hypothesis_thread_key_for_index(g, 2)

    lock = app_module._get_session_operation_lock("concurrency-session")
    violations = []

    def op(key, marker):
        # Mirrors with_session_operation_lock wrapping the whole handler.
        with lock:
            with g.use_hypothesis_thread(key):
                g.hypothesis_gen_agent.add_user_message(marker)
                time.sleep(0.02)  # widen the interleave window
                msgs = g.hypothesis_gen_agent.messages
                if msgs is not g.hypothesis_chat_threads[key]:
                    violations.append(("wrong-thread", marker))
                if msgs[-1]["content"] != marker:
                    violations.append(("interleaved", marker))

    threads = [
        threading.Thread(target=op, args=(key1, "H1-only")),
        threading.Thread(target=op, args=(key2, "H2-only")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert violations == []
    # Each thread accumulated only its own marker.
    assert _thread_texts(g, key1).count("H1-only") == 1
    assert "H2-only" not in _thread_texts(g, key1)
    assert _thread_texts(g, key2).count("H2-only") == 1
    assert "H1-only" not in _thread_texts(g, key2)


def test_generator_lock_serializes_thread_swaps_without_external_lock(tmp_path):
    """The per-generator swap lock alone (no external session lock) prevents two
    overlapping operations from interleaving state in the shared agent's
    messages — the guarantee is local to the generator."""
    g = _make_generator(tmp_path, [_hyp(1), _hyp(2)])
    g.capture_refinement_baseline()
    app_module._ensure_hypothesis_thread_ids(g)
    key1 = app_module._hypothesis_thread_key_for_index(g, 1)
    key2 = app_module._hypothesis_thread_key_for_index(g, 2)
    violations = []

    def op(key, marker):
        # NOTE: no external lock here — only use_hypothesis_thread's own lock.
        with g.use_hypothesis_thread(key):
            g.hypothesis_gen_agent.add_user_message(marker)
            time.sleep(0.02)
            msgs = g.hypothesis_gen_agent.messages
            if msgs is not g.hypothesis_chat_threads[key]:
                violations.append(("wrong-thread", marker))
            if msgs[-1]["content"] != marker:
                violations.append(("interleaved", marker))

    threads = [
        threading.Thread(target=op, args=(key1, "H1-only")),
        threading.Thread(target=op, args=(key2, "H2-only")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert violations == []
    assert "H2-only" not in _thread_texts(g, key1)
    assert "H1-only" not in _thread_texts(g, key2)


def test_mutating_endpoints_carry_the_appropriate_operation_lock():
    """Agent work stays session-serialized; card mutations also carry a card
    lock so a literal manual edit can bypass unrelated agent work safely."""
    import re

    source = open(app_module.__file__, encoding="utf-8").read()
    for name in ("api_chat", "api_ask"):
        # The @with_session_operation_lock decorator must sit directly above the
        # def (allowing the @app.route line between them).
        m = re.search(
            r"@with_session_operation_lock\([^)]*\)\s*(?:@[^\n]*\s*)*def " + name + r"\b",
            source,
        )
        assert m is not None, f"{name} missing @with_session_operation_lock"

    for name in ("api_edit", "api_regenerate", "api_regenerate_guidance"):
        m = re.search(
            r"@with_hypothesis_card_operation_lock\([^\n]*\)\s*(?:@[^\n]*\s*)*def " + name + r"\b",
            source,
        )
        assert m is not None, f"{name} missing @with_hypothesis_card_operation_lock"

    assert "direct_manual_is_independent=True" in source
    assert "serialize=not independent_manual" in source
    for name in ("api_remove_hypothesis", "api_select"):
        m = re.search(
            r"@with_independent_session_operation\([^)]*\)\s*(?:@[^\n]*\s*)*def " + name + r"\b",
            source,
        )
        assert m is not None, f"{name} missing @with_independent_session_operation"


def test_literal_manual_edit_bypasses_an_unrelated_session_agent_lock():
    from flask import session

    session_id = "manual-edit-concurrency"
    session_lock = app_module._get_session_operation_lock(session_id)
    manual_completed = threading.Event()
    agent_completed = threading.Event()

    @app_module.with_hypothesis_card_operation_lock(
        "test_edit", direct_manual_is_independent=True
    )
    def probe():
        return "ok"

    def request_edit(payload, completed):
        with app_module.app.test_request_context("/api/edit", method="POST", json=payload):
            session["session_id"] = session_id
            probe()
            completed.set()

    session_lock.acquire()
    try:
        manual_thread = threading.Thread(
            target=request_edit,
            args=({"index": 1, "manual_hypothesis": "manual H1"}, manual_completed),
        )
        agent_thread = threading.Thread(
            target=request_edit,
            args=({"index": 2, "feedback": "revise H2"}, agent_completed),
        )
        manual_thread.start()
        agent_thread.start()

        assert manual_completed.wait(0.5)
        assert not agent_completed.wait(0.05)
    finally:
        session_lock.release()

    manual_thread.join(timeout=1)
    agent_thread.join(timeout=1)
    assert agent_completed.is_set()
