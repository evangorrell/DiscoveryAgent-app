"""Durable Hypothesis Studio conversation history (hypothesis_studio_history.json).

Covers the module-level event log and the write-side instrumentation of the
generate/chat/edit/regenerate/select routes, plus the read-side reconciliation
(durable events, backward-compatible reconstruction, round mapping). All model
calls are mocked; no paid calls, no S3.
"""
import json
from pathlib import Path
import threading
from contextlib import contextmanager

import pytest

from da_selection_ui import app as app_module
from da_selection_ui import hypothesis_history
from da_selection_ui.select_hypothesis import InteractiveHypothesisGenerator


HYP1 = ("**Title**: Axon growth\n**Hypothesis**: RGC survival depends on mTOR reactivation.\n"
        "**Justification**: Prior work links mTOR to regeneration.\n**Plan**: Compare mTOR-high vs low.")
HYP2 = ("**Title**: Inflammation\n**Hypothesis**: Microglial state gates regeneration.\n"
        "**Justification**: Scdata suggests a microglial axis.")
HYP3 = ("**Title**: Metabolism\n**Hypothesis**: Mitochondrial state predicts axon survival.\n"
        "**Justification**: Energetic resilience may preserve injured neurons.")
PLAIN_HYP = ("Title: Plain label title\n\nHypothesis: Plain label hypothesis statement.\n\n"
             "Justification: Plain label rationale.\n\nPlan: Plain label plan.")
LONG_TITLE_HYP = (
    "**Title**: ATF3 acts as a locus-specific transcriptional repressor at poised regeneration "
    "gene enhancers via co-occupancy with repressive chromatin marks, and its repressive "
    "targets can be distinguished from its activating targets by quantitative features of "
    "ATF3 binding and local regulatory context.\n\n"
    "**Hypothesis**: ATF3 represses poised regeneration enhancers in injured RGCs.\n\n"
    "**Justification**: The generated title is intentionally sentence-like.\n\n"
    "**Plan**: Test ATF3-bound enhancers."
)


# --- fakes -------------------------------------------------------------------

class FakeAgent:
    def __init__(self):
        self.messages = []
        self.last_response_citations = []


class FakeGenerator:
    def __init__(self, checkpoint_dir):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.hypotheses = []
        self.hypothesis_state_ids = []
        self.hypothesis_thread_ids = []
        self.hypothesis_chat_threads = {}
        self.used_thread_ids = []
        self.hypothesis_gen_agent = FakeAgent()
        self.citation_documents = []
        self.last_generation_citations = []
        self.final_selection = {}
        self.task_description = "Objective"
        self.dataset_description = "Ctx"
        self.memory_report = None
        self.ask_reply = "Because mTOR drives axon regrowth."
        self.edit_calls = []
        self.regen_calls = []
        self.saved_agent_guidance = None
        self.hypothesis_config = type("C", (), {"n_candidate_hypotheses": 2,
                                                "llm_config": type("L", (), {"model_name": "m"})()})()

    # generation
    def generate_candidates(self, n, guidance=None, **kwargs):
        self.hypotheses = [HYP1, HYP2][:n] or [HYP1]
        return self.hypotheses

    def validate_checkpoint(self):
        return True

    def load_checkpoint(self):
        return True

    def initialize_agent(self):
        return True

    def clear_persisted_generated_hypotheses(self):
        pass

    def persist_generated_hypotheses(self):
        pass

    # parsing / sanitizing
    def _sanitize_hypothesis_text(self, t, title_override=None):
        text = (t or "").strip()
        if title_override is None:
            return text
        sections = self.parse_hypothesis_sections(text)
        if not any(sections.values()):
            return text
        sections["title"] = title_override
        parts = []
        if sections.get("title"):
            parts.append(f"**Title**: {sections['title']}")
        if sections.get("primary_factor"):
            parts.append(f"**Primary factor**: {sections['primary_factor']}")
        if sections.get("interpretation"):
            parts.append(f"**Interpretation**: {sections['interpretation']}")
        if sections.get("hypothesis"):
            parts.append(f"**Hypothesis**: {sections['hypothesis']}")
        if sections.get("justification"):
            parts.append(f"**Justification**: {sections['justification']}")
        if sections.get("plan"):
            parts.append(f"**Plan**: {sections['plan']}")
        sanitized = "\n\n".join(parts).strip()
        return sanitized if sanitized else text

    def parse_hypothesis_sections(self, text):
        return app_module._parse_hypothesis_sections_standalone(text or "")

    # chat
    def ask_agent_question(self, question, hypothesis_index=None, version_reference_context=None):
        self.last_version_reference_context = version_reference_context
        self.last_asked_hypothesis_text = (
            self.hypotheses[int(hypothesis_index) - 1]
            if hypothesis_index is not None and 1 <= int(hypothesis_index) <= len(self.hypotheses)
            else None
        )
        return self.ask_reply

    def clear_hypothesis_thread(self, thread_key):
        self.hypothesis_chat_threads.pop(thread_key, None)

    @contextmanager
    def use_hypothesis_thread(self, thread_key, restored_messages=None):
        self.used_thread_ids.append(thread_key)
        if thread_key not in self.hypothesis_chat_threads:
            self.hypothesis_chat_threads[thread_key] = list(restored_messages or [])
        yield self.hypothesis_chat_threads[thread_key]

    # edit / regenerate
    def edit_hypothesis(self, idx, feedback=None):
        self.edit_calls.append({"idx": idx, "feedback": feedback})
        if getattr(self, "edit_returns_none", False):
            return None
        self.hypotheses[idx - 1] = HYP1.replace("mTOR reactivation", "mTOR reactivation (revised)")
        return self.hypotheses[idx - 1]

    def regenerate_hypothesis(self, idx, feedback=None):
        self.regen_calls.append({"idx": idx, "feedback": feedback})
        if getattr(self, "regen_returns_none", False):
            return None
        self.hypotheses[idx - 1] = HYP1.replace("mTOR reactivation", "PTEN deletion")
        return self.hypotheses[idx - 1]

    # select
    def save_selection_raw(self, output_content, agent_guidance=None, username=None):
        self.saved_agent_guidance = agent_guidance
        self.final_selection = {"final_hypothesis": output_content, "output_path": "[test]"}
        (self.checkpoint_dir / "selected_hypothesis.md").write_text(output_content, encoding="utf-8")
        return "[test]"

    def save_session_log(self):
        return "[log]"

    def apply_scientist_feedback(self, text, feedback):
        return text


def _round_dir(tmp_path, n=0):
    rd = tmp_path / f"round_{n}"
    rd.mkdir(parents=True, exist_ok=True)
    return rd


def _wire(monkeypatch, gen):
    with app_module.REMOVED_HYPOTHESIS_STATE_IDS_LOCK:
        app_module.REMOVED_HYPOTHESIS_STATE_IDS.pop("sess", None)
    monkeypatch.setattr(app_module, "get_generator", lambda: gen)
    monkeypatch.setattr(app_module, "get_or_restore_generator", lambda: gen)
    monkeypatch.setattr(app_module, "_is_audit_only_mode", lambda: False)
    monkeypatch.setattr(app_module, "_is_audit_mode", lambda: False)
    monkeypatch.setattr(app_module, "_write_interaction_log", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "log_memory", lambda *a, **k: 0.0)
    monkeypatch.setattr(app_module, "_check_project_readiness",
                        lambda gen=None: {"task_description": "Objective", "project_context": "Ctx", "missing": None})
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
        s["session_id"] = "sess"
    return client


def _wire_generate(monkeypatch):
    monkeypatch.setattr(app_module, "_begin_generation", lambda sid, **kwargs: "tok")
    monkeypatch.setattr(app_module, "_generation_attempt_id", lambda sid, tok, **kwargs: "tok")
    monkeypatch.setattr(app_module, "_bind_generation_generator", lambda sid, tok, gen, **kwargs: True)
    monkeypatch.setattr(app_module, "_claim_generation_result", lambda sid, tok, gen, **kwargs: True)
    monkeypatch.setattr(app_module, "_end_generation", lambda sid, tok, **kwargs: None)
    monkeypatch.setattr(app_module, "_is_generation_cancelled", lambda sid, tok, **kwargs: False)
    monkeypatch.setattr(app_module, "_reset_hypothesis_state_ids", lambda g: [f"s{i}" for i in range(len(g.hypotheses))])
    seq = {"n": 0}
    def _inc():
        seq["n"] += 1
        return seq["n"]
    monkeypatch.setattr(app_module, "_increment_generation_id", _inc)


# ============================================================================
# Module-level: durable event log
# ============================================================================

def test_load_missing_history_is_empty(tmp_path):
    assert hypothesis_history.load_history(tmp_path) == {"version": 1, "events": []}


def test_append_assigns_sequence_and_id(tmp_path):
    hypothesis_history.append_events(tmp_path, [
        hypothesis_history.user_message_event("hi", "hypothesis_discussion"),
        hypothesis_history.assistant_message_event("hello", "hypothesis_discussion"),
    ])
    events = hypothesis_history.load_history(tmp_path)["events"]
    assert [e["sequence"] for e in events] == [1, 2]
    assert all(e["id"] for e in events)
    assert all(e["phase"] == "hypothesis_discussion" for e in events)


def test_append_is_atomic_no_partial_file(tmp_path):
    # A successful append leaves exactly one valid JSON file and no temp files.
    hypothesis_history.append_events(tmp_path, [hypothesis_history.user_message_event("x", "hypothesis_discussion")])
    files = list(tmp_path.iterdir())
    assert [f.name for f in files] == [hypothesis_history.HISTORY_FILENAME]
    json.loads((tmp_path / hypothesis_history.HISTORY_FILENAME).read_text())  # parses cleanly


# ============================================================================
# Generation events
# ============================================================================

def test_generation_appends_generated_set(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)

    resp = client.post("/api/generate", json={"num_hypotheses": 2, "guidance": "Focus X"})
    assert resp.status_code == 200 and resp.get_json()["success"] is True

    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == ["hypothesis_set_generated"]
    # Snapshot preserves exactly what the scientist saw.
    assert len(events[0]["hypotheses"]) == 2
    assert "mTOR" in events[0]["hypotheses"][0]["full_text"]
    assert events[0]["hypotheses"][0]["hypothesis_thread_id"] == gen.hypothesis_thread_ids[0]


def test_generate_valid_project_has_no_readiness_gate(monkeypatch, tmp_path):
    """A valid V2 project (Objective + Context required at creation) generates
    without any duplicate objective/context readiness gate at generation time."""
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)  # readiness resolves cleanly (missing=None)
    _wire_generate(monkeypatch)

    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    body = resp.get_json()
    assert resp.status_code == 200 and body["success"] is True
    assert body.get("code") != "project_setup_incomplete"




def test_hypothesis_generation_prompt_requires_concise_card_titles():
    source = (Path(app_module.__file__).parent.parent / "da_selection_ui" / "select_hypothesis.py").read_text(encoding="utf-8")
    air_source = (Path(app_module.__file__).parent.parent / "AIR" / "hyp_agents" / "hypothesis_gen_agent.py").read_text(encoding="utf-8")
    # The Title field must be described as a concise display title with the
    # preferred 10-14 / hard 18 word contract, explicitly NOT the full claim.
    assert "preferred 10-14 words" in source
    assert "hard maximum 18 words" in source
    assert "not the card title" in source
    assert "preferred 10-14 words" in air_source
    assert "hard maximum 18 words" in air_source
    assert "not a full hypothesis sentence" in air_source

def test_generate_forwards_optional_guidance_and_allows_empty(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    calls = []

    def generate_candidates(n, guidance=None, **kwargs):
        calls.append({"n": n, "guidance": guidance})
        gen.hypotheses = [HYP1]
        return gen.hypotheses

    gen.generate_candidates = generate_candidates
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)

    resp = client.post("/api/generate", json={"num_hypotheses": 1, "guidance": "Prefer causal tests."})
    assert resp.status_code == 200 and resp.get_json()["success"] is True
    assert calls[-1] == {"n": 1, "guidance": "Prefer causal tests."}

    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    assert resp.status_code == 200 and resp.get_json()["success"] is True
    assert calls[-1] == {"n": 1, "guidance": None}


def test_generate_legacy_incomplete_project_shows_friendly_message(monkeypatch, tmp_path):
    """A legacy/incomplete project missing Objective/Context gets a friendly
    'project setup incomplete' message — not the old technical missing_* codes."""
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)
    # Simulate a legacy project that reaches generation without required fields.
    monkeypatch.setattr(app_module, "_check_project_readiness",
                        lambda g=None: {"task_description": "", "project_context": "", "missing": "task_description"})
    _wire_generate(monkeypatch)

    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    body = resp.get_json()
    assert resp.status_code == 400 and body["success"] is False
    assert body["code"] == "project_setup_incomplete"
    assert body["code"] not in ("missing_task_description", "missing_project_context")


def test_generate_blocked_before_round_0_lifecycle_guard(monkeypatch, tmp_path):
    """Defense-in-depth: a direct /api/generate for an active V2 project still in
    the pristine pre-Round-0 state is rejected with the lifecycle error, before a
    generator is even built."""
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)
    # Active V2 project, pristine (no autonomous round has started).
    monkeypatch.setattr(app_module, "_active_project_record",
                        lambda: {"id": "p", "checkpoint_dir": str(rd)})
    monkeypatch.setattr(app_module, "_v2_no_round_has_started", lambda root: True)

    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    body = resp.get_json()
    assert resp.status_code == 400 and body["success"] is False
    assert body["code"] == "initial_exploration_required"
    # Lifecycle error, not a setup error: no old technical codes, friendly copy.
    txt = json.dumps(body)
    assert "missing_task_description" not in txt and "missing_project_context" not in txt
    assert "project_setup_incomplete" not in txt
    assert "Round 0" in body["message"]


def test_generate_allowed_after_round_0_completes(monkeypatch, tmp_path):
    """The lifecycle guard passes only after Round 0's completion artifact exists."""
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)  # readiness resolves cleanly (missing=None)
    _wire_generate(monkeypatch)
    monkeypatch.setattr(app_module, "_active_project_record",
                        lambda: {"id": "p", "checkpoint_dir": str(rd)})
    monkeypatch.setattr(app_module, "_v2_no_round_has_started", lambda root: False)
    rd.joinpath("round_report.md").write_text("# Round 0 complete", encoding="utf-8")

    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    body = resp.get_json()
    assert resp.status_code == 200 and body["success"] is True
    assert body.get("code") != "initial_exploration_required"


def test_generate_ordering_lifecycle_guard_precedes_setup_incomplete(monkeypatch, tmp_path):
    """Ordering: pristine pre-Round-0 reports the lifecycle error; only once past
    initial exploration does a legacy/incomplete project report setup_incomplete."""
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)
    monkeypatch.setattr(app_module, "_active_project_record",
                        lambda: {"id": "p", "checkpoint_dir": str(rd)})
    # A legacy/incomplete project (missing objective) that is ALSO pristine:
    # the lifecycle guard wins.
    monkeypatch.setattr(app_module, "_check_project_readiness",
                        lambda g=None: {"task_description": "", "project_context": "", "missing": "task_description"})
    monkeypatch.setattr(app_module, "_v2_no_round_has_started", lambda root: True)
    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    assert resp.get_json()["code"] == "initial_exploration_required"

    # A completed first round with missing setup -> setup_incomplete.
    monkeypatch.setattr(app_module, "_v2_no_round_has_started", lambda root: False)
    rd.joinpath("round_report.md").write_text("# Round 0 complete", encoding="utf-8")
    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    assert resp.get_json()["code"] == "project_setup_incomplete"


def test_generate_lifecycle_guard_skipped_for_non_v2_session(monkeypatch, tmp_path):
    """The guard is a no-op outside the V2 project lifecycle (no active project
    record), so legacy single-project sessions are unaffected."""
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)
    monkeypatch.setattr(app_module, "_active_project_record", lambda: None)
    # Even if the predicate helper's inner check would say "pristine", no active
    # V2 project means the guard does not apply.
    monkeypatch.setattr(app_module, "_v2_no_round_has_started", lambda root: True)
    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    body = resp.get_json()
    assert resp.status_code == 200 and body["success"] is True
    assert body.get("code") != "initial_exploration_required"


def test_second_generation_appends_new_set_without_overwrite(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)

    client.post("/api/generate", json={"num_hypotheses": 1})
    # A later whole-set generation.
    gen.generate_candidates = lambda n, guidance=None, **k: setattr(gen, "hypotheses", [HYP2]) or [HYP2]
    client.post("/api/generate", json={"num_hypotheses": 1})

    sets = [e for e in hypothesis_history.load_history(rd)["events"]
            if e["type"] == "hypothesis_set_generated"]
    assert len(sets) == 2
    assert "mTOR" in sets[0]["hypotheses"][0]["full_text"]
    assert "Microglial" in sets[1]["hypotheses"][0]["full_text"]
    # Interview is frozen at most once (none here — no interview existed).
    intents = [e for e in hypothesis_history.load_history(rd)["events"] if e.get("phase") == "intent"]
    assert intents == []


def test_generation_rewrites_sentence_titles_before_persisting(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)

    normalizer = object.__new__(InteractiveHypothesisGenerator)
    normalized = normalizer._normalize_hypothesis_card_title(LONG_TITLE_HYP)
    gen.generate_candidates = (
        lambda n, guidance=None, **k:
        setattr(gen, "hypotheses", [normalized]) or gen.hypotheses
    )

    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    data = resp.get_json()

    assert resp.status_code == 200 and data["success"] is True
    title = data["hypotheses"][0]["title"]
    assert title == "ATF3 locus-specific transcriptional repressor at poised regeneration gene enhancers"
    # Derived card labels honor the preferred (<=14) and hard (<=18) word budget.
    assert len(title.split()) <= 14
    assert "via co-occupancy" not in title

    events = hypothesis_history.load_history(rd)["events"]
    assert events[-1]["hypotheses"][0]["title"] == title


def test_generation_plain_section_labels_still_returns_structured_fields(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.generate_candidates = lambda n, guidance=None, **k: setattr(gen, "hypotheses", [PLAIN_HYP]) or [PLAIN_HYP]
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)

    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    data = resp.get_json()

    assert resp.status_code == 200 and data["success"] is True
    assert data["hypotheses"][0]["title"] == "Plain label title"
    assert data["hypotheses"][0]["hypothesis"] == "Plain label hypothesis statement."
    assert data["hypotheses"][0]["justification"] == "Plain label rationale."
    assert data["hypotheses"][0]["plan"] == "Plain label plan."

    events = hypothesis_history.load_history(rd)["events"]
    assert events[-1]["type"] == "hypothesis_set_generated"
    assert events[-1]["hypotheses"][0]["title"] == "Plain label title"


# ============================================================================
# Post-generation chat events
# ============================================================================

def test_chat_records_user_and_assistant(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1, HYP2]
    client = _wire(monkeypatch, gen)

    resp = client.post("/api/chat", json={"message": "Why mTOR?", "mode": "hypothesis", "hypothesis_index": 1})
    assert resp.status_code == 200 and resp.get_json()["success"] is True

    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == ["user_message", "assistant_message"]
    assert events[0]["phase"] == "hypothesis_discussion"
    assert events[0]["hypothesis_index"] == 1  # focused index retained
    assert events[1]["content"] == gen.ask_reply


def test_chat_failure_does_not_append(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1]
    gen.ask_reply = None  # simulate a failed/empty response
    client = _wire(monkeypatch, gen)

    resp = client.post("/api/chat", json={"message": "hmm", "mode": "hypothesis"})
    assert resp.get_json()["success"] is False
    assert hypothesis_history.load_history(rd)["events"] == []


class CapturingHypothesisAgent:
    def __init__(self):
        self.messages = []
        self.system_prompt = "system"
        self.last_response_citations = []
        self.llm_config = type("L", (), {"model_name": "test-model", "api_provider": "anthropic"})()
        self.captured_api_params = None

    def reset_conversation(self):
        self.messages = []

    def add_user_message(self, content):
        self.messages.append({"role": "user", "content": content})

    def add_user_message_with_documents(self, text, documents=None):
        self.add_user_message(text)

    def add_assistant_message(self, content):
        self.messages.append({"role": "assistant", "content": content})

    def _call_api_with_retry(self, api_params, track_citations=False, documents=None):
        self.captured_api_params = dict(api_params)
        self.captured_api_params["messages"] = [dict(m) for m in api_params.get("messages", [])]
        return "Grounded response using existing project context."


def _chat_generator_for_context_test(tmp_path):
    gen = app_module.InteractiveHypothesisGenerator.__new__(app_module.InteractiveHypothesisGenerator)
    gen.checkpoint_dir = tmp_path
    gen.hypothesis_gen_agent = CapturingHypothesisAgent()
    gen.hypothesis_config = type("C", (), {"max_tokens": 2000})()
    gen.hypotheses = [HYP1, HYP2]
    gen.hypothesis_state_ids = ["s1", "s2"]
    gen.dataset_description = "Project Context / README: optic nerve regeneration dataset bundle."
    gen.task_description = "Research Objective: identify testable RGC regeneration mechanisms."
    gen.memory_report = "Memory: Round 3 found resilient RGC subtypes and mTOR-linked programs."
    gen.round_report = "CURRENT ROUND REPORT SHOULD NOT BE SENT TO HYPOTHESIS CHAT."
    gen.deep_research_background = "DEEP RESEARCH BACKGROUND SHOULD NOT BE REQUIRED."
    gen.hypothesis_literature_context = None
    gen.audit_only_mode = False
    gen.enable_citations = False
    gen.citation_documents = []
    gen.qa_history = []
    gen.interaction_log = []
    gen.last_answer_citations = []
    gen._hypothesis_chat_context_fingerprint = None
    return gen


def test_post_generation_chat_seeds_context_after_refresh(tmp_path):
    gen = _chat_generator_for_context_test(tmp_path)
    assert gen.hypothesis_gen_agent.messages == []  # refresh/restored worker shape

    reply = gen.ask_agent_question("I don't like those hypotheses, generate again")

    assert reply == "Grounded response using existing project context."
    sent = gen.hypothesis_gen_agent.captured_api_params["messages"]
    joined = "\n\n".join(str(m["content"]) for m in sent)
    assert "## Research Objective" in joined
    assert "identify testable RGC regeneration mechanisms" in joined
    assert "## Project Context / README" in joined
    assert "optic nerve regeneration dataset bundle" in joined
    assert "## Memory Report" in joined
    assert "resilient RGC subtypes" in joined
    assert "## Active Generated Hypotheses" in joined
    assert "Microglial state gates regeneration" in joined
    assert "CURRENT ROUND REPORT SHOULD NOT BE SENT" not in joined
    assert "I don't like those hypotheses, generate again" in joined


def test_agent_prompt_places_conditional_stored_version_context_before_user_question(tmp_path):
    gen = _chat_generator_for_context_test(tmp_path)
    gen.ask_agent_question(
        "Compare version 2 to version 1.",
        hypothesis_index=1,
        version_reference_context="Version 1 (stored exact text):\nORIGINAL EXACT HYPOTHESIS",
    )

    sent = gen.hypothesis_gen_agent.captured_api_params["messages"]
    joined = "\n\n".join(str(m["content"]) for m in sent)
    assert "ORIGINAL EXACT HYPOTHESIS" in joined
    assert joined.index("Stored version context requested by the user") < joined.index("User question:")
    assert joined.rstrip().endswith("Compare version 2 to version 1.")


def test_focused_post_generation_chat_includes_active_hypothesis_after_refresh(tmp_path):
    gen = _chat_generator_for_context_test(tmp_path)

    gen.ask_agent_question("What is the real distinction?", hypothesis_index=2)

    sent = gen.hypothesis_gen_agent.captured_api_params["messages"]
    last_user = sent[-1]["content"]
    assert "this hypothesis, titled “Inflammation”" in last_user
    assert "Hypothesis 2" not in last_user
    assert "Card numbers are temporary visible positions" in last_user
    assert "“version N” means this hypothesis’s regeneration version" in last_user
    assert "“round N” means the research round" in last_user
    assert "Microglial state gates regeneration" in last_user
    assert "What is the real distinction?" in last_user


def test_focused_edit_and_regenerate_prompts_never_expose_internal_index(tmp_path):
    edit_gen = _chat_generator_for_context_test(tmp_path)
    edit_gen.edit_hypothesis(2, feedback="Refine version 2 without changing round 4 context.")
    edit_prompt = edit_gen.hypothesis_gen_agent.captured_api_params["messages"][-1]["content"]
    assert "rewrite this hypothesis, titled “Inflammation”" in edit_prompt
    assert "Hypothesis 2" not in edit_prompt
    assert "Card numbers are temporary visible positions" in edit_prompt
    assert "version 2" in edit_prompt and "round 4" in edit_prompt

    regenerate_gen = _chat_generator_for_context_test(tmp_path)
    regenerate_gen.regenerate_hypothesis(2, feedback="Try a different version 3 for round 5.")
    regenerate_prompt = regenerate_gen.hypothesis_gen_agent.captured_api_params["messages"][-1]["content"]
    assert "rewrite this hypothesis, titled “Inflammation”" in regenerate_prompt
    assert "Hypothesis 2" not in regenerate_prompt
    assert "Card numbers are temporary visible positions" in regenerate_prompt
    assert "version 3" in regenerate_prompt and "round 5" in regenerate_prompt


# ============================================================================
# Edit / regenerate before-after events; stale/failed do not append
# ============================================================================

def _seed_generated(gen):
    gen.hypotheses = [HYP1]
    gen.hypothesis_state_ids = ["sid-1"]


def test_edit_appends_before_after(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    _seed_generated(gen)
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")

    resp = client.post("/api/edit", json={"index": 1, "state_id": "sid-1", "feedback": "Tighten it"})
    assert resp.status_code == 200 and resp.get_json()["success"] is True

    # An agent-driven edit records the exact scientist request, then a
    # version-defining mutation using the regeneration history mechanism.
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == [
        "user_message", "hypothesis_regenerated", "assistant_message",
    ]
    assert events[0]["content"] == "Tighten it" and events[0]["phase"] == "hypothesis_discussion"
    assert events[0]["hypothesis_index"] == 1
    ev = events[1]
    assert ev["hypothesis_index"] == 1
    assert "mTOR reactivation" in ev["before"]["full_text"]
    assert "(revised)" in ev["after"]["full_text"]
    assert events[2]["content"] == "Created Version 2."
    assert resp.get_json()["created_version_number"] == 2


def test_regenerate_appends_before_after(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    _seed_generated(gen)
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")

    resp = client.post("/api/regenerate", json={"index": 1, "state_id": "sid-1", "feedback": "Try another mechanism"})
    assert resp.status_code == 200 and resp.get_json()["success"] is True

    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == ["user_message", "hypothesis_regenerated"]
    assert events[0]["content"] == "Try another mechanism"
    assert "PTEN deletion" in events[1]["after"]["full_text"]
    assert events[1]["before"]["hypothesis_thread_id"] != events[1]["after"]["hypothesis_thread_id"]
    assert events[1]["after"]["hypothesis_thread_id"] == gen.hypothesis_thread_ids[0]


def test_repeated_regenerations_preserve_distinct_content_thread_pairs(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    _seed_generated(gen)
    initial_thread = "thr_" + "a" * 32
    gen.hypothesis_thread_ids = [initial_thread]
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")

    first = client.post("/api/regenerate", json={
        "index": 1, "state_id": "sid-1", "feedback": "First alternative",
    }).get_json()
    second = client.post("/api/regenerate", json={
        "index": 1, "state_id": first["hypothesis"]["state_id"],
        "feedback": "Second alternative",
    }).get_json()

    assert first["success"] is True and second["success"] is True
    regenerations = [event for event in hypothesis_history.load_history(rd)["events"]
                     if event["type"] == "hypothesis_regenerated"]
    assert len(regenerations) == 2
    thread_b = regenerations[0]["after"]["hypothesis_thread_id"]
    thread_c = regenerations[1]["after"]["hypothesis_thread_id"]
    assert len({initial_thread, thread_b, thread_c}) == 3
    assert regenerations[1]["before"]["hypothesis_thread_id"] == thread_b
    assert gen.hypothesis_thread_ids == [thread_c]


def test_failed_regeneration_does_not_create_history_result(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    _seed_generated(gen)
    gen.regen_returns_none = True
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")

    resp = client.post("/api/regenerate", json={
        "index": 1, "state_id": "sid-1", "feedback": "Try another mechanism",
    })

    assert resp.get_json()["success"] is False
    assert hypothesis_history.load_history(rd)["events"] == []


def test_stale_edit_does_not_append(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    _seed_generated(gen)
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")

    # Wrong state_id, no manual draft -> stale rejection, no mutation.
    resp = client.post("/api/edit", json={"index": 1, "state_id": "WRONG", "feedback": "x"})
    body = resp.get_json()
    assert body["success"] is False and body.get("stale_selection") is True
    assert hypothesis_history.load_history(rd)["events"] == []


# ============================================================================
# Selection event (closing substantive event)
# ============================================================================

def test_selection_appends_exact_hypothesis(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1]
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_get_project_round_from_disk", lambda: 0)
    monkeypatch.setattr(app_module, "CLI_DRY_RUN", False)

    final = "**Hypothesis**: RGC survival depends on mTOR.\n\n**Justification**: Prior work.\n\n**Plan**: Compare."
    resp = client.post("/api/select", json={"selection": "custom", "custom_source": "direct_custom", "final_text": final})
    assert resp.status_code == 200 and resp.get_json()["success"] is True

    events = hypothesis_history.load_history(rd)["events"]
    assert events[-1]["type"] == "hypothesis_selected"
    sel = events[-1]["hypothesis"]
    assert "mTOR" in sel["hypothesis"]
    assert sel["plan"] and "Compare" in sel["plan"]


def test_v2_selection_review_omits_execution_guidance():
    root = Path(app_module.app.root_path)
    template = root / app_module.app.template_folder / "hypothesis_studio.html"
    javascript = Path(app_module.app.static_folder) / "hypothesis_studio.js"
    stylesheet = Path(app_module.app.static_folder) / "hypothesis_studio.css"

    html = template.read_text(encoding="utf-8")
    js = javascript.read_text(encoding="utf-8")
    css = stylesheet.read_text(encoding="utf-8")

    assert "Execution guidance (optional)" not in html
    assert "Add any instructions you want the execution agent" not in html
    assert 'id="hs-review-guidance"' not in html
    assert "reviewGuidance" not in js
    assert "agent_guidance" not in js
    assert ".hs-review-guidance" not in css
    assert "selection: h.index" in js
    assert "final_text: h.full_text || h.hypothesis" in js


def test_selection_review_uses_neutral_dialog_focus_treatment():
    stylesheet = Path(app_module.app.static_folder) / "hypothesis_studio.css"
    css = stylesheet.read_text(encoding="utf-8")

    assert ".hs-review:focus," in css
    assert ".hs-review:focus-visible" in css
    assert "outline: 1px solid var(--v2-border);" in css


def test_hypothesis_chat_markdown_has_scoped_reading_rhythm():
    stylesheet = Path(app_module.app.static_folder) / "hypothesis_studio.css"
    css = stylesheet.read_text(encoding="utf-8")

    scope = ".hs-thread .hs-turn-assistant .hs-turn-body"
    assert f"{scope} {{" in css
    assert "line-height: 1.65;" in css
    assert f"{scope} h1," in css
    assert "margin: 1.5rem 0 0.5rem;" in css
    assert f"{scope} p {{" in css
    assert "margin: 0 0 0.75rem;" in css
    assert f"{scope} .hs-markdown-section-label {{" in css
    assert "margin-top: 1.15rem;" in css
    assert f"{scope} ul," in css
    assert f"{scope} blockquote {{" in css
    assert f"{scope} table {{" in css
    assert f"{scope} pre {{" in css

    # The shared prose renderer remains untouched; this rhythm is chat-only.
    shared = (Path(app_module.app.static_folder) / "style.css").read_text(encoding="utf-8")
    assert scope not in shared


def test_selection_api_accepts_optional_execution_guidance(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1]
    client = _wire(monkeypatch, gen)
    _allow_test_selection(monkeypatch)

    without_guidance = client.post("/api/select", json={
        "selection": "custom",
        "custom_source": "direct_custom",
        "final_text": HYP1,
    })
    assert without_guidance.status_code == 200
    assert without_guidance.get_json()["success"] is True
    assert gen.saved_agent_guidance is None

    guidance = "Prioritize lineage tracing and include negative controls."
    with_guidance = client.post("/api/select", json={
        "selection": "custom",
        "custom_source": "direct_custom",
        "final_text": HYP1,
        "agent_guidance": guidance,
    })
    assert with_guidance.status_code == 200
    assert with_guidance.get_json()["success"] is True
    assert gen.saved_agent_guidance == guidance


def test_existing_selection_contract_persists_execution_guidance(tmp_path):
    generator = InteractiveHypothesisGenerator.__new__(InteractiveHypothesisGenerator)
    generator.checkpoint_dir = tmp_path
    generator.output_file = None
    generator.final_selection = None

    guidance = "Compare both sexes and preserve blinded analysis."
    selected = "**Hypothesis**: A defined perturbation improves regeneration."
    output_path = generator.save_selection_raw(selected, guidance, username="scientist")

    assert Path(output_path).read_text(encoding="utf-8").endswith(
        f"**Overall Research Feedback**: {guidance}\n"
    )
    assert (tmp_path / "next_round_instructions.md").read_text(encoding="utf-8") == guidance + "\n"
    assert generator.final_selection["agent_guidance"] == guidance
    assert generator.final_selection["agent_guidance_path"] == str(
        tmp_path / "next_round_instructions.md"
    )


def test_remove_and_select_do_not_wait_for_the_shared_llm_lock(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1, HYP2]
    gen.hypothesis_state_ids = ["sid-1", "sid-2"]
    client = _wire(monkeypatch, gen)
    _allow_test_selection(monkeypatch)
    session_lock = app_module._get_session_operation_lock("sess")

    def run_request(endpoint, payload, result, completed):
        response = client.post(endpoint, json=payload)
        result.append(response.get_json())
        completed.set()

    for endpoint, payload in (
        ("/api/select", {
            "selection": 1, "selection_state_id": "sid-1", "final_text": HYP1,
        }),
        ("/api/remove_hypothesis", {"index": 2, "state_id": "sid-2"}),
    ):
        completed = threading.Event()
        result = []
        session_lock.acquire()
        try:
            worker = threading.Thread(
                target=run_request, args=(endpoint, payload, result, completed)
            )
            worker.start()
            assert completed.wait(0.75), f"{endpoint} waited behind the LLM lock"
        finally:
            session_lock.release()
        worker.join(timeout=1)
        assert result and result[0]["success"] is True


def test_llm_result_reconciles_by_stable_identity_after_concurrent_removal(tmp_path):
    gen = FakeGenerator(_round_dir(tmp_path))
    gen.hypotheses = [HYP1, HYP2, HYP3]
    gen.hypothesis_state_ids = ["sid-1", "sid-2", "sid-3"]
    operation = app_module._capture_hypothesis_operation_state(gen, 2)
    revised = HYP2.replace("Microglial state", "Revised microglial state")

    # H1 is removed while the legacy LLM method still writes to its original
    # numeric slot 2, temporarily overwriting H3.
    del gen.hypotheses[0]
    del gen.hypothesis_state_ids[0]
    gen.hypotheses[1] = revised

    resolved = app_module._reconcile_hypothesis_operation_result(gen, operation, revised)
    assert resolved == 1
    assert gen.hypotheses == [revised, HYP3]


def test_llm_result_cannot_overwrite_neighbor_after_its_target_is_removed(tmp_path):
    gen = FakeGenerator(_round_dir(tmp_path))
    gen.hypotheses = [HYP1, HYP2, HYP3]
    gen.hypothesis_state_ids = ["sid-1", "sid-2", "sid-3"]
    operation = app_module._capture_hypothesis_operation_state(gen, 2)
    revised = HYP2.replace("Microglial state", "Revised microglial state")

    del gen.hypotheses[1]
    del gen.hypothesis_state_ids[1]
    gen.hypotheses[1] = revised

    resolved = app_module._reconcile_hypothesis_operation_result(gen, operation, revised)
    assert resolved is None
    assert gen.hypotheses == [HYP1, HYP3]


def test_selecting_another_hypothesis_before_launch_replaces_selection(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1, HYP2]
    gen.hypothesis_state_ids = ["sid-1", "sid-2"]
    client = _wire(monkeypatch, gen)
    _allow_test_selection(monkeypatch)

    first = client.post("/api/select", json={
        "selection": 1, "selection_state_id": "sid-1", "final_text": HYP1,
    })
    second = client.post("/api/select", json={
        "selection": 2, "selection_state_id": "sid-2", "final_text": HYP2,
    })

    assert first.get_json()["success"] is True
    assert second.get_json()["success"] is True
    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == HYP2
    selections = [
        event for event in hypothesis_history.load_history(rd)["events"]
        if event["type"] == hypothesis_history.TYPE_HYPOTHESIS_SELECTED
    ]
    assert len(selections) == 2
    assert selections[-1]["hypothesis"]["full_text"] == HYP2
    assert selections[-1]["hypothesis"]["index"] == 2


def _pending_selection_workspace(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1, HYP2, HYP3]
    gen.hypothesis_state_ids = ["sid-1", "sid-2", "sid-3"]
    gen.hypothesis_thread_ids = ["thr_" + char * 32 for char in ("a", "b", "c")]
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([
            app_module._studio_hypothesis_snapshot(gen, index)
            for index in range(1, 4)
        ]),
    ])
    client = _wire(monkeypatch, gen)
    _allow_test_selection(monkeypatch)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")
    selected = client.post("/api/select", json={
        "selection": 1,
        "selection_state_id": "sid-1",
        "final_text": HYP1,
    })
    assert selected.status_code == 200 and selected.get_json()["success"] is True
    return rd, gen, client


def test_pending_selection_locks_only_selected_card_server_side(monkeypatch, tmp_path):
    rd, gen, client = _pending_selection_workspace(monkeypatch, tmp_path)
    original_selection = rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8")

    selected_mutations = (
        ("/api/edit", {"index": 1, "state_id": "sid-1", "manual_hypothesis": HYP3}),
        ("/api/edit", {"index": 1, "state_id": "sid-1", "feedback": "Revise A."}),
        ("/api/edit", {
            "index": 1,
            "state_id": "sid-1",
            "feedback": "Revise historical A.",
            "action": "edit_regeneration_version",
            "regeneration_position": 0,
            "regeneration_result_id": "a" * 32,
        }),
        ("/api/regenerate", {"index": 1, "state_id": "sid-1", "feedback": "Regenerate A."}),
        ("/api/regenerate_guidance", {"index": 1, "state_id": "sid-1"}),
        ("/api/remove_hypothesis", {"index": 1, "state_id": "sid-1"}),
    )
    for endpoint, payload in selected_mutations:
        response = client.post(endpoint, json=payload)
        assert response.status_code == 409
        assert response.get_json()["code"] == "pending_selected_hypothesis_locked"
        assert response.get_json()["selected_hypothesis_index"] == 1

    # Manual edit, LLM edit, and individual regeneration remain available on
    # the other cards while A's exact persisted snapshot stays untouched.
    manual_b = client.post("/api/edit", json={
        "index": 2, "state_id": "sid-2",
        "manual_hypothesis": HYP2.replace("Microglial state", "A microglial state"),
    })
    assert manual_b.status_code == 200 and manual_b.get_json()["success"] is True
    state_b = manual_b.get_json()["hypothesis"]["state_id"]

    llm_c = client.post("/api/edit", json={
        "index": 3, "state_id": "sid-3", "feedback": "Make C more specific.",
    })
    assert llm_c.status_code == 200 and llm_c.get_json()["success"] is True
    state_c = llm_c.get_json()["hypothesis"]["state_id"]

    regenerate_b = client.post("/api/regenerate", json={
        "index": 2, "state_id": state_b, "feedback": "Try another B mechanism.",
    })
    assert regenerate_b.status_code == 200 and regenerate_b.get_json()["success"] is True
    regenerate_c = client.post("/api/regenerate_guidance", json={
        "index": 3, "state_id": state_c,
    })
    assert regenerate_c.status_code == 200 and regenerate_c.get_json()["success"] is True

    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == original_selection


def test_pending_selection_blocks_full_set_regeneration(monkeypatch, tmp_path):
    _rd, gen, client = _pending_selection_workspace(monkeypatch, tmp_path)
    _wire_generate(monkeypatch)
    before = list(gen.hypotheses)

    response = client.post("/api/generate", json={"num_hypotheses": 3})

    assert response.status_code == 409
    assert response.get_json()["code"] == "pending_selection_blocks_full_regeneration"
    assert response.get_json()["selected_hypothesis_index"] == 1
    assert gen.hypotheses == before


def test_reselection_moves_pending_card_lock_and_overwrites_authority(monkeypatch, tmp_path):
    rd, gen, client = _pending_selection_workspace(monkeypatch, tmp_path)

    reselection = client.post("/api/select", json={
        "selection": 2,
        "selection_state_id": "sid-2",
        "final_text": HYP2,
    })
    assert reselection.status_code == 200 and reselection.get_json()["success"] is True
    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == HYP2

    # A is unlocked immediately; B now owns the pending snapshot lock.
    edit_a = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1",
        "manual_hypothesis": HYP1.replace("mTOR reactivation", "mTOR signaling"),
    })
    assert edit_a.status_code == 200 and edit_a.get_json()["success"] is True

    edit_b = client.post("/api/edit", json={
        "index": 2, "state_id": "sid-2",
        "manual_hypothesis": HYP2.replace("Microglial state", "A microglial state"),
    })
    assert edit_b.status_code == 409
    assert edit_b.get_json()["code"] == "pending_selected_hypothesis_locked"
    assert edit_b.get_json()["selected_hypothesis_index"] == 2


def test_started_round_locks_selected_and_unselected_cards(monkeypatch, tmp_path):
    rd, _gen, client = _pending_selection_workspace(monkeypatch, tmp_path)
    with client.session_transaction() as session_state:
        session_state["v2_active_project_id"] = "project-1"
        session_state["project_config"] = {"checkpoint_dir": str(rd)}
    rd.parent.joinpath("round_1").mkdir()

    for index, state_id in ((1, "sid-1"), (2, "sid-2"), (3, "sid-3")):
        response = client.post("/api/edit", json={
            "index": index,
            "state_id": state_id,
            "manual_hypothesis": HYP3,
        })
        assert response.status_code == 409
        assert response.get_json()["code"] == "hypothesis_round_locked"


def test_pending_lock_follows_selected_card_when_another_card_is_removed(monkeypatch, tmp_path):
    rd, _gen, client = _pending_selection_workspace(monkeypatch, tmp_path)
    reselection = client.post("/api/select", json={
        "selection": 2,
        "selection_state_id": "sid-2",
        "final_text": HYP2,
    })
    assert reselection.status_code == 200

    removed_a = client.post("/api/remove_hypothesis", json={
        "index": 1,
        "state_id": "sid-1",
    })
    assert removed_a.status_code == 200 and removed_a.get_json()["success"] is True
    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == HYP2

    # B moved from slot 2 to slot 1 and remains the locked selection. C moved
    # to slot 2 and remains mutable.
    locked_b = client.post("/api/edit", json={
        "index": 1,
        "state_id": "sid-2",
        "manual_hypothesis": HYP2.replace("Microglial state", "A microglial state"),
    })
    assert locked_b.status_code == 409
    assert locked_b.get_json()["code"] == "pending_selected_hypothesis_locked"
    assert locked_b.get_json()["selected_hypothesis_index"] == 1

    editable_c = client.post("/api/edit", json={
        "index": 2,
        "state_id": "sid-3",
        "manual_hypothesis": HYP3.replace("Mitochondrial state", "Mitochondrial resilience"),
    })
    assert editable_c.status_code == 200 and editable_c.get_json()["success"] is True


def test_pending_lock_survives_late_selected_version_and_repeated_removals(monkeypatch, tmp_path):
    rd, gen, client = _pending_selection_workspace(monkeypatch, tmp_path)
    reselection = client.post("/api/select", json={
        "selection": 2,
        "selection_state_id": "sid-2",
        "final_text": HYP2,
    })
    assert reselection.status_code == 200

    # Model an edit that began before selection and completed afterward. The
    # selected artifact remains Version 1, while the card lineage now displays
    # a newer active version with a different state id and text.
    before = app_module._studio_hypothesis_snapshot(gen, 2)
    revised = HYP2.replace("Microglial state", "Revised microglial state")
    gen.hypotheses[1] = revised
    gen.hypothesis_state_ids[1] = "sid-2-v2"
    after = app_module._studio_hypothesis_snapshot(gen, 2)
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_revised_event(2, before, after),
    ])

    removed_c = client.post("/api/remove_hypothesis", json={
        "index": 3,
        "state_id": "sid-3",
    })
    assert removed_c.status_code == 200 and removed_c.get_json()["success"] is True
    removed_a = client.post("/api/remove_hypothesis", json={
        "index": 1,
        "state_id": "sid-1",
    })
    assert removed_a.status_code == 200 and removed_a.get_json()["success"] is True

    # The selected lineage is now the only remaining card at slot 1. It must
    # remain locked immediately even though its active version no longer text-
    # matches the exact Version 1 artifact in selected_hypothesis.md.
    locked_selected = client.post("/api/remove_hypothesis", json={
        "index": 1,
        "state_id": "sid-2-v2",
    })
    assert locked_selected.status_code == 409
    assert locked_selected.get_json()["code"] == "pending_selected_hypothesis_locked"
    assert locked_selected.get_json()["selected_hypothesis_index"] == 1
    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == HYP2


def test_round_launch_failure_after_selection_preserves_pending_selection(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    rd.joinpath("round_report.md").write_text("# Complete", encoding="utf-8")
    rd.joinpath("memory_report.md").write_text("# Understanding", encoding="utf-8")
    rd.joinpath("generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": [{"index": 1, "state_id": "sid-1", "full_text": HYP1}]}),
        encoding="utf-8",
    )
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1]
    gen.hypothesis_state_ids = ["sid-1"]
    _wire(monkeypatch, gen)  # install generator and logging fakes; keep the active-project client above
    _allow_test_selection(monkeypatch)

    selected = client.post("/api/select", json={
        "selection": 1,
        "selection_state_id": "sid-1",
        "final_text": HYP1,
    })
    assert selected.status_code == 200
    assert selected.get_json()["success"] is True
    before_launch = rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8")

    def fail_launch(_project, options=None):
        raise app_module.local_round_runner.RoundLaunchError(
            "anthropic_api_key_missing",
            "Anthropic API key not detected. Set ANTHROPIC_API_KEY and try again.",
        )

    monkeypatch.setattr(app_module.local_round_runner, "start_round", fail_launch)
    launch = client.post(
        "/api/rounds/start",
        json={"project_id": app_module._v2_load_projects()[0]["id"]},
    )

    assert launch.status_code == 400
    assert launch.get_json()["code"] == "anthropic_api_key_missing"
    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == before_launch
    assert not (rd.parent / "round_1").exists()


def test_selection_is_final_substantive_event(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1]
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)
    monkeypatch.setattr(app_module, "_get_project_round_from_disk", lambda: 0)
    monkeypatch.setattr(app_module, "CLI_DRY_RUN", False)

    client.post("/api/generate", json={"num_hypotheses": 1})
    client.post("/api/chat", json={"message": "why?", "mode": "hypothesis"})
    client.post("/api/select", json={"selection": "custom", "custom_source": "direct_custom",
                                     "final_text": "**Hypothesis**: X\n\n**Justification**: Y"})
    types = [e["type"] for e in hypothesis_history.load_history(rd)["events"]]
    assert types[-1] == "hypothesis_selected"
    assert "hypothesis_set_generated" in types and "assistant_message" in types


# ============================================================================
# Ordering survives refresh / process recreation
# ============================================================================

def test_ordering_survives_reload(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    client = _wire(monkeypatch, gen)
    _wire_generate(monkeypatch)
    client.post("/api/generate", json={"num_hypotheses": 1})
    client.post("/api/chat", json={"message": "q1", "mode": "hypothesis"})
    # Fresh load from disk (simulating a new worker) preserves sequence order.
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert [e["type"] for e in events] == ["hypothesis_set_generated", "user_message", "assistant_message"]


# ============================================================================
# Read API — durable events, backward compat, round mapping, isolation
# ============================================================================

def _project_client(monkeypatch, tmp_path):
    """Authenticated client with a real V2 project (for the read endpoints,
    which resolve the checkpoint dir from the active project config)."""
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    client.post("/api/projects", data={"project_name": "P", "task_description": "Goal.",
                                        "dataset_context": "Ctx."}, content_type="multipart/form-data")
    return client


def _project_round_root(tmp_path):
    # Managed registry paths are intentionally portable/relative on disk.
    # Resolve through the production loader rather than interpreting a raw
    # registry string against pytest's process working directory.
    proj = app_module._v2_load_projects()[0]
    ckpt = Path(proj["checkpoint_dir"])
    return ckpt.parent if ckpt.name.startswith("round_") else ckpt


def _project_slug(tmp_path):
    return app_module._v2_load_projects()[0]["url_slug"]


def test_studio_page_gates_until_round_zero_completes(monkeypatch, tmp_path):
    """The Studio differentiates pristine and started-but-incomplete Round 0,
    then opens the normal workspace only after Round 0 completes."""
    client = _project_client(monkeypatch, tmp_path)
    slug = _project_slug(tmp_path)
    captured = {}

    def fake_render_template(template_name, **kwargs):
        captured['template'] = template_name
        captured.update(kwargs)
        return "OK"

    monkeypatch.setattr(app_module, "render_template", fake_render_template)

    # Brand-new project, no Round 0 activity -> locked (blocking overlay).
    resp = client.get(f"/projects/{slug}/hypotheses")
    assert resp.status_code == 200
    assert captured['template'] == 'hypothesis_studio.html'
    assert captured['initial_exploration'] is True
    assert captured['first_round_gate'] == 'not_started'

    # Starting Round 0 changes the callout, but does not unlock Studio.
    rd0 = _project_round_root(tmp_path) / "round_0"
    (rd0 / "query_001").mkdir(parents=True, exist_ok=True)
    captured.clear()
    resp = client.get(f"/projects/{slug}/hypotheses")
    assert resp.status_code == 200
    assert captured['initial_exploration'] is True
    assert captured['first_round_gate'] == 'incomplete'

    # After Round 0 completes -> normal studio (initial_exploration False).
    rd0.joinpath("round_report.md").write_text("# Round Report\n\nFindings.")
    rd0.joinpath("memory_report.md").write_text("# Memory\n\nUnderstanding.")
    captured.clear()
    resp = client.get(f"/projects/{slug}/hypotheses")
    assert resp.status_code == 200
    assert captured['initial_exploration'] is False
    assert captured['first_round_gate'] is None


def test_incomplete_first_round_overlay_copy_and_destinations(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    slug = _project_slug(tmp_path)
    rd0 = _project_round_root(tmp_path) / "round_0"
    (rd0 / "round_activity.jsonl").write_text(
        json.dumps({"event_type": "round_started"}) + "\n", encoding="utf-8"
    )

    response = client.get(f"/projects/{slug}/hypotheses")
    html = response.get_data(as_text=True)
    blocking = html[html.index('class="v2-modal-overlay v2-blocking-overlay"'):]

    assert response.status_code == 200
    assert "Finish your first round" in blocking
    assert (
        "Finish your first round before using Hypothesis Studio to choose what "
        "DiscoveryAgent investigates next."
    ) in blocking
    assert "View round workspace" in blocking
    assert f'/rounds/?project={slug}' in blocking
    assert "Back to project home" in blocking
    assert f'/projects/{slug}' in blocking


def test_running_round_opens_studio_as_read_only_history(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    slug = _project_slug(tmp_path)
    root = _project_round_root(tmp_path)
    round_zero = root / "round_0"
    round_zero.joinpath("round_report.md").write_text("# Complete", encoding="utf-8")
    round_zero.joinpath("memory_report.md").write_text("# Understanding", encoding="utf-8")
    round_zero.joinpath("generated_hypotheses.json").write_text(
        json.dumps({"hypotheses": [{"title": "H", "hypothesis": "Test H"}]}),
        encoding="utf-8",
    )
    round_one = root / "round_1"
    round_one.mkdir()
    (round_one / "query_001").mkdir()
    captured = {}

    def fake_render_template(template_name, **kwargs):
        captured["template"] = template_name
        captured.update(kwargs)
        return "OK"

    monkeypatch.setattr(app_module, "render_template", fake_render_template)
    response = client.get(f"/projects/{slug}/hypotheses")

    assert response.status_code == 200
    assert captured["template"] == "hypothesis_studio.html"
    assert captured["running_round_index"] == 1
    assert captured["initial_exploration"] is False


def test_studio_controller_uses_history_only_during_running_round():
    static = Path(app_module.app.static_folder)
    js = (static / "hypothesis_studio.js").read_text(encoding="utf-8")
    template = (
        Path(app_module.app.root_path)
        / app_module.app.template_folder
        / "hypothesis_studio.html"
    ).read_text(encoding="utf-8")

    assert "runningRoundIndex:" in template
    assert "async function initializeRunningRoundHistory()" in js
    assert "if (CFG.runningRoundIndex != null) initializeRunningRoundHistory();" in js
    assert "state.readOnly = true;" in js
    assert "'Read-only while Round ' + CFG.runningRoundIndex + ' is in progress'" in js


def test_round_workspace_shows_pre_round_overlay_before_round_0(monkeypatch, tmp_path):
    """Round Workspace shows the same blocking pre-Round-0 overlay until Round 0
    has activity; afterward it renders the normal monitor without the overlay."""
    client = _project_client(monkeypatch, tmp_path)
    slug = _project_slug(tmp_path)
    captured = {}

    def fake_render_template(template_name, **kwargs):
        captured['template'] = template_name
        captured.update(kwargs)
        return "OK"

    monkeypatch.setattr(app_module, "render_template", fake_render_template)

    # Brand-new project, no Round 0 activity -> blocking overlay. The numbered
    # round URL activates the project and redirects to the session-scoped
    # /rounds/ workspace, which owns the pre_round_overlay flag.
    resp = client.get(f"/projects/{slug}/rounds/0", follow_redirects=True)
    assert resp.status_code == 200
    assert captured['template'] == 'round_workspace.html'
    assert captured.get('pre_round_overlay') is True

    # After Round 0 has activity -> normal workspace (no pre-round overlay).
    rd0 = _project_round_root(tmp_path) / "round_0"
    (rd0 / "query_001").mkdir(parents=True, exist_ok=True)
    rd0.joinpath("round_report.md").write_text("# Round Report\n\nFindings.", encoding="utf-8")
    captured.clear()
    resp = client.get(f"/projects/{slug}/rounds/0", follow_redirects=True)
    assert captured.get('pre_round_overlay') in (None, False)


def test_generate_direct_call_blocked_on_pristine_v2_project(monkeypatch, tmp_path):
    """End-to-end: a real pristine V2 project, activated in the session, rejects a
    direct POST /api/generate with the lifecycle error — no monkeypatched predicate,
    using the same canonical resolution the Studio page uses."""
    client = _project_client(monkeypatch, tmp_path)
    slug = _project_slug(tmp_path)
    client.get(f"/projects/{slug}/hypotheses")  # activate the project in the session

    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    body = resp.get_json()
    assert resp.status_code == 400
    assert body["code"] == "initial_exploration_required"
    txt = json.dumps(body)
    assert "missing_task_description" not in txt and "missing_project_context" not in txt

    # Activity alone remains blocked.
    rd0 = _project_round_root(tmp_path) / "round_0"
    (rd0 / "query_001").mkdir(parents=True, exist_ok=True)
    client.get(f"/projects/{slug}/hypotheses")
    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    assert resp.get_json()["code"] == "initial_exploration_required"

    # Completion unlocks the same direct call.
    rd0.joinpath("round_report.md").write_text("# R", encoding="utf-8")
    client.get(f"/projects/{slug}/hypotheses")  # re-activate (state re-resolved)
    resp = client.post("/api/generate", json={"num_hypotheses": 1})
    assert resp.get_json().get("code") != "initial_exploration_required"


def test_generation_status_endpoint_reports_in_progress_without_initialize_lock(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    endpoints = {r.endpoint for r in app_module.app.url_map.iter_rules()}
    assert "api_generation_status" in endpoints

    session_id = "status-test-session"
    with client.session_transaction() as sess:
        sess["session_id"] = session_id
        project_scope = (
            f"project-id:{sess['v2_active_project_id']}"
            if sess.get("v2_active_project_id") else "session"
        )

    with app_module.GENERATION_LOCK:
        generation_key = app_module._generation_state_key(session_id, project_scope)
        app_module.GENERATION_STATE[generation_key] = {
            "in_progress": True,
            "cancel_requested": False,
            "token": "tok",
            "attempt_id": "attempt-status",
            "status": "running",
        }

    try:
        resp = client.get("/api/generation_status")
        body = resp.get_json()
    finally:
        with app_module.GENERATION_LOCK:
            app_module.GENERATION_STATE.pop(generation_key, None)

    assert resp.status_code == 200
    assert body["success"] is True
    assert body["in_progress"] is True
    assert body["cancel_requested"] is False


def test_generation_is_visible_while_waiting_for_session_operation_lock():
    session_id = "queued-generation-session"
    operation_lock = app_module._get_session_operation_lock(session_id)
    entered_view = threading.Event()
    finished = threading.Event()

    @app_module.with_generation_operation_lock
    def queued_generation():
        entered_view.set()
        return "ok"

    def invoke():
        with app_module.app.test_request_context("/api/generate", method="POST"):
            app_module.session["session_id"] = session_id
            queued_generation()
        finished.set()

    operation_lock.acquire()
    worker = threading.Thread(target=invoke)
    worker.start()
    try:
        # The view cannot enter until the lock is released, but generation
        # status must already be visible to a newly mounted Studio page.
        for _ in range(100):
            if app_module._generation_status_for_session(session_id)["in_progress"]:
                break
            threading.Event().wait(0.01)
        assert app_module._generation_status_for_session(session_id)["in_progress"] is True
        assert entered_view.is_set() is False
    finally:
        operation_lock.release()
        worker.join(timeout=2)
        with app_module.GENERATION_LOCK:
            app_module.GENERATION_STATE.pop(
                app_module._generation_state_key(session_id, "session"), None
            )
        with app_module.SESSION_OPERATION_LOCKS_GUARD:
            app_module.SESSION_OPERATION_LOCKS.pop(session_id, None)

    assert finished.is_set() is True
    assert entered_view.is_set() is True


def test_cancelled_generation_releases_retry_and_late_result_is_discarded(monkeypatch, tmp_path):
    """A synchronous provider call may return after Cancel, but it no longer owns
    the session generator, persistence, history, selection, or completion state."""
    session_id = "generation-cancel-retry-session"
    scope = "project-id:project-a"
    round_dir = _round_dir(tmp_path)
    selected_path = round_dir / "selected_hypothesis.md"
    selected_path.write_text("authoritative selection", encoding="utf-8")
    a_entered = threading.Event()
    release_a = threading.Event()
    persisted = []

    class ControlledGenerator(FakeGenerator):
        def __init__(self, checkpoint_dir, result, entered=None, release=None):
            super().__init__(checkpoint_dir)
            self.result = result
            self.entered = entered
            self.release = release

        def generate_candidates(self, n, guidance=None, **kwargs):
            if self.entered:
                self.entered.set()
            if self.release:
                assert self.release.wait(3), "test did not release generation A"
            self.hypotheses = [self.result]
            return list(self.hypotheses)

    generation_a = ControlledGenerator(round_dir, HYP1, a_entered, release_a)
    generation_b = ControlledGenerator(round_dir, HYP2)
    app_module.GENERATORS[session_id] = generation_a

    def current_generator():
        generator = app_module.GENERATORS.get(session_id)
        if generator is None:
            app_module.GENERATORS[session_id] = generation_b
            generator = generation_b
        return generator

    monkeypatch.setattr(app_module, "get_or_restore_generator", current_generator)
    monkeypatch.setattr(app_module, "_active_v2_project_pre_round_0", lambda: False)
    monkeypatch.setattr(app_module, "_v2_studio_mutation_lock_response", lambda: None)
    monkeypatch.setattr(app_module, "_studio_pending_full_set_lock_response", lambda gen: None)
    monkeypatch.setattr(
        app_module,
        "_check_project_readiness",
        lambda gen=None: {"task_description": "Objective", "project_context": "Ctx", "missing": None},
    )
    monkeypatch.setattr(
        app_module,
        "_persist_generated_hypotheses",
        lambda gen, **kwargs: persisted.append((gen, list(gen.hypotheses))),
    )

    results = {}

    def invoke_generation(label, attempt_id):
        with app_module.app.test_request_context(
            "/api/generate",
            method="POST",
            json={"num_hypotheses": 1, "attempt_id": attempt_id},
        ):
            app_module.session["session_id"] = session_id
            app_module.session["v2_active_project_id"] = "project-a"
            response = app_module.app.view_functions["api_generate"]()
            results[label] = response.get_json()

    worker_a = threading.Thread(target=invoke_generation, args=("a", "attempt-a"))
    worker_a.start()
    assert a_entered.wait(2)

    # A genuinely active attempt still owns the duplicate-start guard.
    invoke_generation("duplicate", "attempt-duplicate")
    assert results["duplicate"]["error"] == "Generation already in progress"
    assert results["duplicate"]["attempt_id"] == "attempt-a"

    def cancel(attempt_id):
        with app_module.app.test_request_context(
            "/api/cancel_generation", method="POST", json={"attempt_id": attempt_id}
        ):
            app_module.session["session_id"] = session_id
            app_module.session["v2_active_project_id"] = "project-a"
            return app_module.app.view_functions["api_cancel_generation"]().get_json()

    first_cancel = cancel("attempt-a")
    assert first_cancel["cancelled"] is True
    assert first_cancel["status"] == "cancelled"
    terminal = app_module._generation_status_for_session(session_id, scope=scope)
    assert terminal == {
        "in_progress": False,
        "cancel_requested": True,
        "attempt_id": "attempt-a",
        "status": "cancelled",
    }
    # A refreshed Studio learns the same terminal attempt identity from the
    # unlocked status endpoint and can retry without retaining client memory.
    with app_module.app.test_request_context("/api/generation_status", method="GET"):
        app_module.session["session_id"] = session_id
        app_module.session["v2_active_project_id"] = "project-a"
        refreshed_status = app_module.app.view_functions["api_generation_status"]().get_json()
    assert refreshed_status["status"] == "cancelled"
    assert refreshed_status["attempt_id"] == "attempt-a"
    # The same terminal cancellation is idempotent after that refresh.
    assert cancel("attempt-a")["cancelled"] is True

    # B completes through the newly rotated operation lane while A is still
    # physically blocked in its provider call.
    invoke_generation("b", "attempt-b")
    assert worker_a.is_alive()
    assert results["b"]["success"] is True
    assert results["b"]["attempt_id"] == "attempt-b"

    release_a.set()
    worker_a.join(timeout=3)
    assert not worker_a.is_alive()
    assert results["a"]["cancelled"] is True
    assert results["a"]["attempt_id"] == "attempt-a"
    assert app_module.GENERATORS[session_id] is generation_b
    assert persisted == [(generation_b, [HYP2])]
    events = hypothesis_history.load_history(round_dir)["events"]
    assert len(events) == 1
    assert events[0]["type"] == hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED
    assert events[0]["hypotheses"][0]["full_text"] == HYP2
    assert selected_path.read_text(encoding="utf-8") == "authoritative selection"
    final_status = app_module._generation_status_for_session(session_id, scope=scope)
    assert final_status["status"] == "completed"
    assert final_status["attempt_id"] == "attempt-b"

    with app_module.GENERATION_LOCK:
        app_module.GENERATION_STATE.pop(
            app_module._generation_state_key(session_id, scope), None
        )
    with app_module.GENERATION_ID_LOCK:
        app_module.GENERATION_IDS.pop(session_id, None)
    with app_module.SESSION_OPERATION_LOCKS_GUARD:
        app_module.SESSION_OPERATION_LOCKS.pop(session_id, None)
    app_module.GENERATORS.pop(session_id, None)


def test_generation_attempt_identity_scopes_cancel_to_project_and_current_tab():
    session_id = "generation-scope-session"
    scope_a = "project-id:a"
    scope_b = "project-id:b"
    token_a = app_module._begin_generation(session_id, scope=scope_a, attempt_id="tab-a")
    token_b = app_module._begin_generation(session_id, scope=scope_b, attempt_id="tab-b")
    assert token_a and token_b

    cancelled_a = app_module._request_generation_cancel(
        session_id, scope=scope_a, attempt_id="tab-a"
    )
    assert cancelled_a["cancelled"] is True
    assert app_module._generation_status_for_session(session_id, scope=scope_b)["in_progress"] is True

    # A late Cancel from the old tab cannot cancel a newer authoritative attempt.
    token_a2 = app_module._begin_generation(session_id, scope=scope_a, attempt_id="tab-a-new")
    assert token_a2
    stale_cancel = app_module._request_generation_cancel(
        session_id, scope=scope_a, attempt_id="tab-a"
    )
    assert stale_cancel["cancelled"] is False
    assert app_module._generation_status_for_session(session_id, scope=scope_a)["attempt_id"] == "tab-a-new"
    assert app_module._is_generation_cancelled(session_id, token_a, scope=scope_a) is True

    with app_module.GENERATION_LOCK:
        app_module.GENERATION_STATE.pop(app_module._generation_state_key(session_id, scope_a), None)
        app_module.GENERATION_STATE.pop(app_module._generation_state_key(session_id, scope_b), None)


def test_cancel_regeneration_marks_single_hypothesis_regeneration_cancelled(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    endpoints = {r.endpoint for r in app_module.app.url_map.iter_rules()}
    assert "api_cancel_regeneration" in endpoints

    session_id = "regen-cancel-test-session"
    with client.session_transaction() as sess:
        sess["session_id"] = session_id

    with app_module.GENERATION_LOCK:
        app_module.REGENERATION_STATE[session_id] = {
            "in_progress": True,
            "index": 2,
            "token": "regen-token",
            "started_at": 123.0,
            "cancel_requested": False,
        }

    try:
        resp = client.post("/api/cancel_regeneration", json={"index": 2})
        body = resp.get_json()
        status_resp = client.get("/api/generation_status")
        status_body = status_resp.get_json()
    finally:
        with app_module.GENERATION_LOCK:
            app_module.REGENERATION_STATE.pop(session_id, None)

    assert resp.status_code == 200
    assert body == {"success": True, "cancelled": True}
    assert status_body["regeneration"]["in_progress"] is True
    assert status_body["regeneration"]["index"] == 2
    assert status_body["regeneration"]["cancel_requested"] is True


def test_cancel_hypothesis_thread_marks_matching_request_cancelled(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    endpoints = {r.endpoint for r in app_module.app.url_map.iter_rules()}
    assert "api_cancel_hypothesis_thread" in endpoints

    session_id = "hyp-thread-cancel-test-session"
    with client.session_transaction() as sess:
        sess["session_id"] = session_id

    with app_module.HYPOTHESIS_THREAD_CANCEL_LOCK:
        app_module.HYPOTHESIS_THREAD_CANCEL_STATE[session_id] = {
            "active_request_id": "req-1",
            "active_index": 2,
            "active_kind": "discuss",
            "cancel_requested": False,
        }

    try:
        resp = client.post("/api/cancel_hypothesis_thread", json={"request_id": "req-1", "index": 2})
        body = resp.get_json()
        with app_module.HYPOTHESIS_THREAD_CANCEL_LOCK:
            cancel_requested = app_module.HYPOTHESIS_THREAD_CANCEL_STATE[session_id]["cancel_requested"]
    finally:
        with app_module.HYPOTHESIS_THREAD_CANCEL_LOCK:
            app_module.HYPOTHESIS_THREAD_CANCEL_STATE.pop(session_id, None)

    assert resp.status_code == 200
    assert body == {"success": True, "cancelled": True}
    assert cancel_requested is True


def test_studio_render_keeps_pristine_overlay_but_enables_eligible_generation(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    slug = _project_slug(tmp_path)

    resp = client.get(f"/projects/{slug}/hypotheses")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="hs-init" data-hs-action="generate-initial">Initialize workspace</button>' in html
    assert 'class="hs-activity-spinner"' in html
    assert 'class="hs-activity-arrows"' in html
    assert '<circle cx="32" cy="32" r="21"' in html
    assert 'M47.6 46.1A21 21 0 0 1 16.4 46.1' in html
    assert "<span>&rarr;</span>" not in html
    assert 'id="hs-activity-label">Generating hypotheses... This may take a few minutes.</p>' in html
    assert 'id="hs-cancel-generation">Cancel</button>' in html
    assert 'id="hs-chat-scope" hidden' in html
    assert 'id="hs-mode-discuss"' not in html
    assert 'id="hs-mode-revise"' not in html
    assert 'id="hs-focus-chip"' not in html
    assert 'id="hs-welcome" hidden' in html
    assert '<div class="hs-layout" inert>' in html
    assert 'class="v2-modal-overlay v2-blocking-overlay"' in html
    assert 'class="v2-modal v2-blocking-box"' in html
    assert "hypothesis_studio.css') }}" not in html
    assert "hypothesis_studio.css?v=20261102-selected-footer-baseline-1" in html
    assert "hypothesis_layout.js?v=20261001-layout-final-1" in html
    assert "hypothesis_studio.js?v=20261014-manual-draft-1" in html
    assert "hypothesis_events.js?v=20260808-selected-lineage-reindex" in html
    assert "hypothesis_activity.js?v=20260711-unseen-activity" in html
    assert 'Select <span class="v2-arrow" aria-hidden="true">&rarr;</span>' in html
    assert "Use for next round" not in html
    assert 'class="v2-footer"' not in html
    assert 'id="v2-logout"' not in html

    rd0 = _project_round_root(tmp_path) / "round_0"
    rd0.joinpath("round_report.md").write_text("# Round Report\n\nFindings.", encoding="utf-8")
    rd0.joinpath("memory_report.md").write_text("# Memory\n\nUnderstanding.", encoding="utf-8")

    resp = client.get(f"/projects/{slug}/hypotheses")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="hs-init" data-hs-action="generate-initial">Initialize workspace</button>' in html
    assert '<div class="hs-layout" inert>' not in html
    assert 'class="v2-blocking-overlay"' not in html


def test_studio_controller_wires_welcome_cta_to_generate_endpoint():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    css = (Path(app_module.app.static_folder) / "hypothesis_studio.css").read_text(encoding="utf-8")
    html = (Path(app_module.app.root_path) / app_module.app.template_folder / "hypothesis_studio.html").read_text(encoding="utf-8")
    assert "els.init.addEventListener('click'" in js
    assert "apiJson('/api/generate', 'POST', payload)" in js
    assert "apiJson('/api/generation_status', 'GET')" in js
    assert "resumeIfGenerationInProgress()" in js
    assert "function clearStudioSnapshot()" in js
    assert "state.events = [];" in js
    assert "clearStudioSnapshot();" in js
    assert "pollGenerationCompletion(generationEpoch)" in js
    assert "apiJson('/api/cancel_generation', 'POST', {" in js
    assert "attempt_id: attemptId || undefined" in js
    assert "data.success !== false && data.cancelled !== false" in js
    assert "state.generating = false;" in js
    assert "persistStudioSnapshot();" in js
    assert "status.status === 'cancelled' || status.cancel_requested" in js
    assert "generationEpoch !== generationUiEpoch" in js
    assert "attempt_id: attemptId" in js
    assert "activityCancel.addEventListener('click', cancelGeneration)" in js
    assert "function canceledRegenerationKey()" in js
    assert "function isCanceledRegeneration(regeneration)" in js
    assert "rememberCanceledRegeneration(idx)" in js
    assert "apiJson('/api/cancel_regeneration', 'POST', { index: idx })" in js
    assert "if (!isCanceledRegeneration(regen)) return resumeRegenerationInProgress(regen);" in js
    assert "Generating hypotheses... This may take a few minutes." in js
    assert "hs-arrow-cycle" in css
    assert "hs-activity-spinner span:nth-child" not in css
    assert "num_hypotheses: state.count" in js
    assert ".hs-caret { font-size: 1.2em; line-height: 1;" in css
    assert "guidance: guidance || undefined" in js
    assert "const idx = Number(index);" in js
    assert ".hs-card.is-focused.is-selected" in css
    assert ".hs-card.is-focused.is-busy" in css
    assert "inset 4px 0 0 var(--v2-accent)" not in css
    assert ".hs-card-action {" in css
    assert "font-weight: 400;" in css
    assert ".hs-card-grid.is-single { --hs-card-columns: 1; max-width: 720px; margin-inline: auto; }" in css
    assert "primePackedOverflowGrid" not in js  # all card counts use the same measured layout
    assert ".hs-readonly-return {" in css
    assert "window.__hypothesisStudioDebug" in js
    assert "function cardTitle(h)" in js
    assert "extractStructuredTitle(h && (h.full_text || h.formatted_output || h.title))" in js
    assert "titleNeedsRewrite(providedTitle, h)" in js
    assert "function hypothesisMarkdown(h)" in js
    assert "renderMarkdown(hypothesisBodyMarkdown(displayHypothesis)" in js
    assert "state.manualEditDraft = hypothesisBodyMarkdown(h)" in js
    assert "function hasHypotheses() { return deriveCards().length > 0; }" in js
    assert "window.confirm" not in js
    assert "menu.appendChild(removeMenuItem(h));" in js
    assert "window.V2Dialog.confirm" not in js
    assert "function removeMenuItem(h)" in js
    assert "hs-remove-menu-cancel" in js
    assert "hs-remove-menu-delete" in js
    assert "id=\"hs-remove-confirm\"" not in html
    assert ".hs-remove-menu-row.is-confirming" in css
    assert "esc(cardTitle(displayHypothesis))" in js
    assert "state.composerMode === 'edit'" in js
    assert "'Edit Hypothesis ' + state.focusIndex + ' with DiscoveryAgent'" in js
    assert "'Chat About Hypothesis ' + state.focusIndex" in js
    assert "composerMode: state.composerMode" not in js
    assert "state.composerMode = 'chat';" in js
    assert "setBusy(!!(state.busyIndex || hasAnyPendingThread()));" in js
    assert "els.input.addEventListener('change', syncComposerInput);" in js
    assert "els.input.addEventListener('keyup', refreshSendEnabled);" in js
    assert "state.busyIndex === idx && state.busyKind !== null" in js
    assert "isLlmEditActive(state.focusIndex, focusedResultEventId)" in js
    assert "if (state.busy && !pendingHere) return;" not in js
    assert 'class="hs-mode-menu-title">Mode' in html
    assert "if (els.modeEdit) els.modeEdit.addEventListener('click', () => setComposerMode('edit'))" in js
    assert "Stop response" in js
    assert "<rect x=\"5.25\" y=\"5.25\" width=\"9.5\" height=\"9.5\"" in js
    assert ".hs-send.is-stop" in css
    assert ".hs-send.is-stop:hover:not(:disabled) { background: var(--v2-accent-hover); }" in css
    assert "apiJson('/api/chat', 'POST'" in js
    assert "apiJson('/api/edit', 'POST'" in js
    assert "apiJson('/api/remove_hypothesis', 'POST', {" in js
    assert "state_id: removalRequest.stateId" in js
    assert "Regenerate and Remove are card-level actions" in js
    assert "removalBlockedMessage" not in js
    assert "const confirmed = await window.V2Dialog.confirm" not in js
    assert "const activeRemovalRequests = {};" in js
    assert "if (!data.hypotheses.length) returnToGenerationWelcome();" in js
    assert "function returnToGenerationWelcome()" in js
    assert "state.events = [];" in js
    assert "function queueThreadMessage" in js
    assert "queuedThreadMessages: {}" in js
    assert "pausedThreadQueues" not in js
    assert "function promoteNextQueuedThreadMessage" in js
    assert 'id="hs-queued-messages"' in html
    assert ".hs-queued-item" in css
    assert ".hs-queue-" not in css
    assert "function isFocusedThreadPending()" in js
    assert "hasPendingThread(state.focusIndex, currentThreadResultEventId(state.focusIndex))" in js
    assert "function cancelActiveThreadResponse()" in js
    assert "if (isFocusedThreadPending()) {" in js
    assert "submitThreadMessage();" in js
    assert "function stopActiveThreadRequest()" in js
    assert "apiJson('/api/cancel_hypothesis_thread', 'POST'" in js
    assert "content: 'Response stopped.', hypothesisIndex: idx" in js
    assert "request_id: started.clientRequestId" in js
    assert "requestId: requestId || null" in js
    assert "clientRequestId: pending.requestId || null" in js
    assert "HSE.regenerationThread(" in js
    assert "Wait for the active hypothesis response to finish before removing a hypothesis." not in js
    assert "hs-card-regenerating" in js
    assert "const loading = el('div', 'hs-card-regenerating');" in js
    regenerating_card = js[js.index("if (isRegenerating) {"):
                           js.index("// Header: number + title")]
    assert "return card;" in regenerating_card
    assert "card.appendChild(cancel);" in regenerating_card
    assert ".hs-card-regenerating.is-inline" not in css
    assert "Selected to direct this round" not in js
    assert "hs-selected-label" not in css
    assert "if (isSelected) {" in js
    assert "else if (!state.readOnly && !isSelected)" in js
    assert "hs-card-selected-static', 'Selected'" in js
    assert ".hs-card-selected-static" in css
    assert "if (!state.readOnly && !isPendingSelected)" in js
    assert "head.appendChild(buildOverflow(h, displayHypothesis, versionView));" in js
    assert "function pendingSelectedIndex()" in js
    assert "function isPendingSelectedCard(index)" in js
    assert "Selected for ' + (state.viewRoundLabel" not in js
    assert "Select another hypothesis to change your choice." in js
    assert "els.modeEdit.disabled = focusedSelectedLocked" in js
    assert ".hs-mode-option:disabled" in css
    assert "if (isPendingSelectedCard(idx))" in js
    assert "Full-set regeneration is unavailable while a hypothesis is selected" in js
    assert "els.init.disabled = selectionPending" in js
    assert "function regenerationViewFor(h)" in js
    assert "function navigateRegenerationVersion(index, position)" in js
    assert "Version ' + (view.viewedPosition + 1) + ' of ' + view.results.length" in js
    assert "previous.disabled = blocked || view.viewedPosition <= 0" in js
    assert "next.disabled = blocked || view.viewedPosition >= view.results.length - 1" in js
    assert "Viewing an earlier regeneration" not in js
    assert "Discussion history is unavailable" not in js
    assert "Use this version" not in js
    assert "action = 'edit_regeneration_version'" in js
    assert "payload.action = 'edit_regeneration_version'" in js
    assert "payload.regeneration_result_id = versionView.viewedResultEventId" in js
    assert "regeneration_result_id: target.regenerationResultId" in js
    assert "payload.hypothesis_thread_id" not in js
    assert "if (!versionView.isViewingCurrent)" in js
    assert "openReview(displayHypothesis, h, versionView.viewedPosition)" in js
    assert "selection_state_id: target.activeStateId" in js
    assert "controls.appendChild(regenerationVersionControls(h, versionView, true));" in js
    assert ".hs-regeneration-nav" in css
    assert "function hasRecordedSelection()" in js
    assert "return !!latestSelectionSource();" in js
    assert "return !!HSE.latestSelection(state.events);" not in js
    assert "state.readOnly = !!detail.data.read_only;" in js
    assert "state.readOnly = !!detail.data.read_only || hasRecordedSelection();" not in js
    assert "['running', 'stopped', 'completed'].includes(current.status)" in js
    assert "Selection is provisional until the autonomous round is" in js
    assert "Number(state.viewRoundIndex) === Number(state.currentRoundIndex)" in js
    assert "els.returnCurrent.hidden = viewingCurrentRound;" in js
    assert 'id="hs-return-current" hidden>Return to current round' in html
    assert "if (state.readOnly) return false;" in js
    assert "if (!state.readOnly && locallyRestoredFocus != null" in js
    assert "if (state.readOnly) state.focusIndex = null;" in js
    assert "if (state.focusIndex == null) {" in js
    assert "state.focusIndex = selectedIndex || Number(cards[0].index);" in js
    assert "const isFocused = state.focusIndex === idx;" in js
    assert "compactCardColumns" not in js
    assert "supportsCompactCardColumns" not in js
    assert "snap.busyKind === 'regenerate'" not in js
    assert "const hasActiveOperation = !idx || state.generating ||" in js
    assert "isRegeneratingHypothesis(idx) ||" in js
    assert "state.regenerationRequests[idx] = activeRequest;" in js
    assert "delete state.regenerationRequests[idx];" in js
    assert "function hasAnyRegeneration()" in js
    assert "const requestId = ++activeThreadRequestSeq;" in js
    assert "pendingThreadRequests: {}" in js
    assert "const focusedResultEventId = focused" in js
    assert "hasPendingThread(state.focusIndex, focusedResultEventId)" in js
    assert "hasPendingThread(idx, resultEventId) || activeThreadRequest(idx, resultEventId)" in js
    assert "const pendingAny = state.pendingThreadIndex != null;" not in js
    assert "activeThreadRequests[laneKey] = active;" in js
    assert "delete activeThreadRequests[laneKey];" in js
    assert "clearThreadEventsForIndex(idx);" not in js
    assert ".hs-history-note" in css
    assert "text-align: center;" in css
    assert "const surface = el('div', 'hs-hypothesis-surface is-empty')" in js
    assert ".hs-hypothesis-surface.is-empty" in css
    assert ".hs-hypothesis-surface.is-empty .hs-history-note" in css
    assert "top: 25%;" in css
    assert "left: 50%;" in css
    assert "const wasExpanded = !!state.expanded[idx]" in js
    assert "state.expanded = wasExpanded ? { [idx]: true } : {}" in js
    assert "state.manualEditIndex = idx" in js
    assert "manualEditBlock(h)" in js
    assert "ta.focus({ preventScroll: true })" in js
    assert "ta.setSelectionRange(0, 0)" in js
    assert "ta.scrollTop = 0" in js
    assert "if (isManualEditing) {" in js
    assert "} else {\n                body.appendChild(el('div', 'v2-prose hs-hypothesis-markdown'" in js
    assert "modeDiscuss" not in js
    assert "modeRevise" not in js
    assert "/api/hypothesis_intent" not in js


def test_manual_edit_hides_the_normal_sticky_card_controls():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    renderer = js[js.index("function renderCard(h, selectedIndex)"):
                  js.index("function regenerationVersionControls")]

    assert "if (isManualEditing) {" in renderer
    assert "body.appendChild(manualEditBlock(h));" in renderer
    assert "if (!isManualEditing) {\n            const controls = el('div', 'hs-card-controls');" in renderer
    assert renderer.index("body.appendChild(manualEditBlock(h));") < renderer.index("if (!isManualEditing) {")
    assert "card.appendChild(controls);\n        }\n        return card;" in renderer


def test_studio_persists_viewed_regeneration_version_across_navigation():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")

    assert "function normalizedRegenerationPositions(value)" in js
    assert "viewedRegenerationPositions: normalizedRegenerationPositions(data.viewedRegenerationPositions)" in js
    assert "viewedRegenerationPositions: normalizedRegenerationPositions(state.viewedRegenerationPositions)" in js
    assert "state.viewedRegenerationPositions = normalizedRegenerationPositions(snap.viewedRegenerationPositions);" in js
    assert "state.viewedRegenerationPositions = saved.viewedRegenerationPositions || {};" in js
    assert "const locallyRestoredRegenerationPositions = restoredLocal" in js
    assert "state.viewedRegenerationPositions = locallyRestoredRegenerationPositions || {};" in js
    # Persisted positions are advisory: the current regeneration history still
    # bounds-checks them and falls back to its active/latest version if stale.
    assert "viewedPosition < 0 || viewedPosition >= history.results.length" in js
    assert "viewedPosition = history.activePosition;" in js


def test_studio_routes_llm_edits_but_not_manual_edits_through_version_append():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    revise = js[js.index("async function sendRevise"):js.index("// ---- regenerate / manual edit / remove")]
    manual = js[js.index("async function runManualEdit"):js.index("async function runRemove")]

    assert "authoritativeRevisionResponse(data, started, h)" in revise
    assert "await restoreCurrentCardsFromHistory({" in revise
    assert "authoritativeRevision: authoritativeRevision" in revise
    assert "data.created_version_number ||" not in revise
    assert "kind: 'revision'" not in revise
    assert "state.events.push({" not in revise
    terminal_success = revise.index("finishLlmEditRequest(idx, started);")
    assert terminal_success < revise.index("render();", terminal_success)
    assert "source: editingHistorical ? 'historical-edit' : 'revised'" in manual
    assert "resultEventId: versionView.viewedResultEventId" in manual
    assert "data.result_event_id" not in manual
    assert "ev.sourceResultEventId === pending.resultEventId" in js


def test_final_hypothesis_review_uses_select_cta_label():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    html = (Path(app_module.app.root_path) / app_module.app.template_folder / "hypothesis_studio.html").read_text(encoding="utf-8")

    label = 'Select <span class="v2-arrow" aria-hidden="true">&rarr;</span>'
    assert label in html
    assert js.count(label) == 1
    assert "function resetReviewConfirm()" in js
    assert "Use for next round" not in html
    assert "Use for next round" not in js


def test_historical_version_keeps_card_level_regenerate_and_remove_actions():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    overflow = js[js.index("function buildOverflow"):js.index("function menuItem")]
    regenerate = js[js.index("async function runRegenerate"):js.index("function cancelRegenerate")]
    remove = js[js.index("async function runRemove"):js.index("function returnToGenerationWelcome")]

    assert "const regenerateItem = menuItem('Regenerate', () => runRegenerate(h, ''));" in overflow
    assert "menu.appendChild(regenerateItem);" in overflow
    assert "menu.appendChild(removeMenuItem(h));" in overflow
    assert "versionView.isViewingCurrent" not in overflow

    # Both actions target the current card state even when their menu was
    # opened while an older rendered snapshot was visible.
    assert regenerate.index("h = activeMutationHypothesis(h);") < regenerate.index("const payload =")
    assert "request_hypothesis: hypothesisMarkdown(h)" in regenerate
    assert "source: 'regenerated', before: h," in regenerate
    assert "hypothesis: data.hypothesis" in regenerate
    assert "setViewedVersionResultId(responseIndex, createdResultEventId);" in regenerate
    assert remove.index("h = activeMutationHypothesis(h);") < remove.index("apiJson('/api/remove_hypothesis'")
    assert "state.events.push({\n                    kind: 'removal'" in remove


def test_hypothesis_remove_uses_inline_cancel_delete_confirmation():
    static = Path(app_module.app.static_folder)
    js = (static / "hypothesis_studio.js").read_text(encoding="utf-8")
    css = (static / "hypothesis_studio.css").read_text(encoding="utf-8")
    inline = js[js.index("function removeMenuItem(h)"):
                js.index("function closeAllMenus")]

    assert "v2-menu-item-danger hs-remove-menu-trigger" in inline
    assert "removalBlockedMessage" not in inline
    assert "row.classList.add('is-confirming');" in inline
    assert "hs-remove-menu-cancel', 'Cancel'" in inline
    assert "hs-remove-menu-delete', 'Delete'" in inline
    assert "reset();" in inline
    assert inline.index("closeAllMenus();") < inline.index("runRemove(h);")
    assert "window.V2Dialog.confirm" not in inline
    assert ".hs-remove-menu-row.is-confirming" in css
    assert ".hs-card-overflow .hs-remove-menu-actions" in css
    assert "justify-content: flex-end;" in css
    assert "gap: 8px;" in css
    assert "background: #bf463e;" in css


def test_remove_remains_independent_of_chat_edit_and_regeneration_requests():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    inline = js[js.index("function removeMenuItem(h)"):
                js.index("function closeAllMenus")]
    remove = js[js.index("async function runRemove"):
                js.index("function returnToGenerationWelcome")]

    assert "hasAnyPendingThread" not in inline
    assert "hasAnyRegeneration" not in inline
    assert "state.busy" not in inline
    assert "hasAnyPendingThread" not in remove
    assert "hasAnyRegeneration" not in remove
    assert "state.busyIndex" not in remove
    assert "setBusy(" not in remove
    assert "activeRemovalRequests[idx] = removalRequest;" in remove
    assert "state_id: removalRequest.stateId" in remove


def test_regeneration_in_flight_state_is_scoped_to_each_hypothesis():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")

    assert "regenerationRequests: {}" in js
    assert "function regenerationRequest(index)" in js
    assert "function isRegeneratingHypothesis(index)" in js
    assert "isRegeneratingHypothesis(idx) ||\n            hasAnyLlmEditActive(idx);" in js
    assert "(state.busyIndex === idx && state.busyKind !== null)" in js
    assert "state.busyIndex != null ||\n            isRegeneratingHypothesis(idx)" not in js
    assert "state_id: h.state_id" in js
    assert "state.generationId = data.generation_id != null ? data.generation_id : state.generationId;" in js
    assert "state.regenerationRequests[idx] = activeRequest;" in js
    assert "regenerationRequest(idx) !== activeRequest" in js
    assert "const isRegenerating = isRegeneratingHypothesis(idx);" in js
    assert "state.busyIndex = idx;\n        state.busyKind = 'regenerate';" not in js
    assert "readPendingRegenerations().filter" in js


def test_single_regeneration_poll_refreshes_locally_without_global_initialization():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    refresh = js[js.index("async function refreshAfterRegenerationCompletion"):
                 js.index("async function restoreRequestedRoundFromLocation")]
    poll = js[js.index("function pollRegenerationCompletion"):
              js.index("function pollGenerationCompletion")]

    assert "await refreshAfterRegenerationCompletion(index);" in poll
    assert "initialize(" not in poll
    assert "showLoading(" not in poll
    assert "window.location" not in poll

    assert "const priorSurfaceScrollTop" in refresh
    assert "const restored = await restoreCurrentCardsFromHistory({ followNewCurrentIndex: idx });" in refresh
    assert "renderCards({ restoreScrollTop: priorSurfaceScrollTop });" in refresh
    assert "if (Number(state.focusIndex) === idx)" in refresh
    assert "showLoading(" not in refresh
    assert "showError(" not in refresh
    assert "window.location" not in refresh


def test_pending_normal_chat_queues_only_on_the_same_hypothesis():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    submit = js[js.index("async function submitThreadMessage()"):
                js.index("function beginThreadRequest")]

    assert "const pendingHere = hasPendingThread(idx, targetResultEventId) ||" in submit
    assert "!!activeThreadRequest(idx, targetResultEventId);" in submit
    assert "state.composerMode === 'chat' && pendingHere" in submit
    assert "threadRequestKind(idx, targetResultEventId) !== 'discuss'" in submit
    assert "const targetResultEventId = currentThreadResultEventId(idx);" in submit
    assert "queueThreadMessage(idx, message, targetResultEventId)" in submit
    assert "hasAnyPendingThread()" not in submit
    assert "isRegeneratingHypothesis(idx)" in submit
    assert "isLlmEditActive(idx, targetResultEventId)" in submit
    assert "state.composerMode === 'edit' && hasAnyLlmEditActive(idx)" in submit
    assert "await sendRevise(message, idx);" in submit
    assert "await sendDiscuss(message, idx);" in submit


def test_thread_operation_lock_is_scoped_to_exact_hypothesis_version():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    helpers = js[js.index("function threadLaneKey"):
                 js.index("function normalizeQueuedThreadMessages")]
    submit = js[js.index("async function submitThreadMessage"):
                js.index("function beginThreadRequest")]
    begin = js[js.index("function beginThreadRequest"):
               js.index("function finishThreadRequest")]
    discuss = js[js.index("async function sendDiscuss"):
                 js.index("async function sendRevise")]
    revise = js[js.index("async function sendRevise"):
                js.index("// ---- regenerate / manual edit / remove")]

    assert "String(idx) + ':' + String(resultEventId || '')" in helpers
    assert "pendingThreadRequest(idx, targetResultEventId)" in helpers
    assert "activeThreadRequest(idx, targetResultEventId)" in helpers
    assert "sameThreadResultEventId(state.busyResultEventId, targetResultEventId)" in helpers
    assert "const targetResultEventId = currentThreadResultEventId(idx);" in submit
    assert "hasPendingThread(idx, targetResultEventId)" in submit
    assert "activeThreadRequest(idx, targetResultEventId)" in submit
    assert "kind === 'revise' && hasAnyLlmEditActive(idx)" in begin
    assert "index: idx" in begin
    assert "resultEventId: resultEventId || null" in begin
    assert "activeThreadRequests[started.laneKey] !== started.active" in discuss
    assert "activeThreadRequests[started.laneKey] !== started.active" in revise
    assert "payload.regeneration_result_id = versionView.viewedResultEventId" in discuss
    assert "payload.regeneration_result_id = versionView.viewedResultEventId" in revise


def test_hypothesis_chat_fifo_and_llm_edit_lock_advance_context_before_unlock():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    submit = js[js.index("async function submitThreadMessage()"):
                js.index("function beginThreadRequest")]
    begin = js[js.index("function beginThreadRequest"):
               js.index("function finishThreadRequest")]
    discuss = js[js.index("async function sendDiscuss"):
                 js.index("async function sendRevise")]
    revise = js[js.index("async function sendRevise"):
                js.index("// ---- regenerate / manual edit / remove")]
    refresh = js[js.index("function refreshSendEnabled"):
                 js.index("// ---- wiring")]
    wire = js[js.index("function wire()"):
              js.index("document.addEventListener('DOMContentLoaded'")]

    # Edit remains single-flight and its blocked draft is not cleared. Normal
    # Chat takes the separate FIFO branch and clears only after enqueueing.
    read_draft = submit.index("const message = els.input.value.trim();")
    any_version_edit_gate = submit.index(
        "state.composerMode === 'edit' && hasAnyLlmEditActive(idx)"
    )
    edit_gate = submit.index("state.composerMode === 'edit' && pendingHere")
    queue_gate = submit.index("state.composerMode === 'chat' && pendingHere")
    queue_write = submit.index("queueThreadMessage(idx, message, targetResultEventId)")
    clear_draft = submit.index("els.input.value = '';", queue_write)
    assert read_draft < any_version_edit_gate < edit_gate < queue_gate < queue_write < clear_draft
    assert "Another edit is already in progress for this hypothesis." in submit
    assert "hasPendingThread(idx, resultEventId) || activeThreadRequest(idx, resultEventId)" in begin
    assert "kind === 'revise' && hasAnyLlmEditActive(idx)" in begin
    defensive_edit_gate = revise.index("if (hasAnyLlmEditActive(idx))")
    revise_begin = revise.index("const started = beginThreadRequest(")
    accepted_clear = revise.index("els.input.value = '';", revise_begin)
    assert defensive_edit_gate < revise_begin < accepted_clear
    assert "editRequestBlocked || selectedEditLocked || focusedMutationInProgress" in refresh
    assert "if (pendingHere)" in refresh
    assert "Queue message" not in refresh
    assert "if (e.key === 'Enter' && !e.shiftKey && !e.isComposing)" in wire
    assert "submitThreadMessage();" in wire
    assert "queuedThreadMessages: {}" in js
    assert "resultEventId: targetResultEventId" in js
    assert "sameThreadResultEventId(entry.resultEventId, targetResultEventId)" in js
    assert "function promoteNextQueuedThreadMessage(index)" in js
    assert "sendDiscuss(next.text, idx, next.resultEventId);" in js
    assert "const versionView = regenerationViewForResult(active, forcedResultEventId);" in discuss
    assert "queuedThreadMessages: cloneJsonSafe(state.queuedThreadMessages || {}, {})" in js
    assert "state.queuedThreadMessages = normalizeQueuedThreadMessages(" in js
    assert "snap.queuedThreadMessages,\n            currentThreadResultEventId" in js

    # A completed ordinary response enters the transcript before the request
    # lock is released.
    assistant_reply = discuss.index("content: data.reply")
    discuss_finish = discuss.index(
        "finishThreadRequest(idx, started.resultEventId, started.requestId);", assistant_reply
    )
    promote = discuss.index("promoteNextQueuedThreadMessage(idx);", discuss_finish)
    assert assistant_reply < discuss_finish < discuss.index("renderThread();", discuss_finish) < promote

    # The validated response triggers a targeted durable-history refresh before
    # edit ownership is released and the card is repainted.
    refresh_revision = revise.index("await restoreCurrentCardsFromHistory({")
    revise_finish = revise.index("finishLlmEditRequest(idx, started);", refresh_revision)
    assert refresh_revision < revise_finish < revise.index("render();", revise_finish)
    assert "authoritativeRevision: authoritativeRevision" in revise

    # Every chat resolves the active card and its captured version. Historical
    # queued chat therefore keeps its durable result id even if the scientist
    # navigates to another version before the FIFO lane promotes it.
    active = discuss.index("const active = activeMutationHypothesis({ index: idx });")
    version = discuss.index("const versionView = regenerationViewForResult(active, forcedResultEventId);")
    request = discuss.index("const started = beginThreadRequest(")
    assert active < version < request
    assert "Edit this historical result to create a new version before chatting about it." not in discuss
    historical_payload = discuss.index(
        "payload.regeneration_result_id = versionView.viewedResultEventId;"
    )
    network = discuss.index("apiJson('/api/chat', 'POST', payload")
    assert request < historical_payload < network
    assert "resultEventId: data.source_result_event_id || versionView.viewedResultEventId" in discuss
    assert "delete state.viewedRegenerationPositions[idx]" not in discuss


def test_only_llm_edit_locks_regenerate_and_manual_edit_on_the_same_card():
    static = Path(app_module.app.static_folder)
    js = (static / "hypothesis_studio.js").read_text(encoding="utf-8")
    css = (static / "hypothesis_studio.css").read_text(encoding="utf-8")
    manual_block = js[js.index("function manualEditBlock"):
                      js.index("function buildOverflow")]
    overflow = js[js.index("function buildOverflow"):
                  js.index("function menuItem")]
    open_manual = js[js.index("function openManualEdit"):
                     js.index("async function runManualEdit")]
    save_manual = js[js.index("async function runManualEdit"):
                     js.index("async function runRemove")]
    discuss = js[js.index("async function sendDiscuss"):
                 js.index("async function sendRevise")]

    # The overflow action communicates the lock, while the handler repeats it
    # before changing focus/editor state to protect stale rendered menus.
    assert "const llmEditLocked = hasAnyLlmEditActive(Number(h.index));" in overflow
    assert "const mutationLocked = llmEditLocked || isRegeneratingHypothesis(Number(h.index));" in overflow
    assert "regenerateItem.disabled = mutationLocked;" in overflow
    assert "regenerateItem.setAttribute('aria-disabled', mutationLocked ? 'true' : 'false');" in overflow
    assert "manualEditItem.disabled = mutationLocked;" in overflow
    assert "manualEditItem.setAttribute('aria-disabled', mutationLocked ? 'true' : 'false');" in overflow
    guard = open_manual.index("if (hasAnyLlmEditActive(idx)) {")
    assert guard < open_manual.index("state.manualEditIndex = idx;")

    # If the editor was already open, its draft remains visible but cannot be
    # changed or saved until the same card's LLM request completes.
    assert "ta.disabled = llmEditLocked || savePending;" in manual_block
    assert "save.disabled = llmEditLocked || savePending;" in manual_block
    assert "cancelManualEdit(idx);" in manual_block
    assert ".hs-card-overflow .v2-menu-item:disabled" in css

    # Saving is guarded independently and scoped by idx, so another card is
    # not blocked merely because this hypothesis has an in-flight request.
    assert "const mutationLocked = isRegeneratingHypothesis(idx) || hasAnyLlmEditActive(idx) ||" in save_manual
    assert "hasAnyRegeneration()" not in save_manual
    assert "hasAnyPendingThread()" not in save_manual
    assert save_manual.index("if (mutationLocked)") < save_manual.index("const { resp, data } = await apiJson(")

    # Ordinary Chat still repaints card actions, but hasAnyLlmEditActive remains
    # false for its `discuss` kind so those menu items stay available.
    assert discuss.count("renderCards();") >= 3


def test_llm_edit_terminal_paths_release_the_lock_and_reject_late_cancelled_results():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    stop = js[js.index("function stopActiveThreadRequest"):
              js.index("function promoteNextQueuedThreadMessage")]
    revise = js[js.index("async function sendRevise"):
                js.index("// ---- regenerate / manual edit / remove")]
    finish = js[js.index("function finishLlmEditRequest"):
                js.index("async function sendRevise")]

    # Cancellation invalidates the request identity before repainting. A late
    # provider response therefore cannot apply its revision to newer state.
    assert "activeRequest.stopped = true;" in stop
    assert "delete activeThreadRequests[laneKey];" in stop
    assert "sameThreadResultEventId(state.busyResultEventId, resultEventId)" in stop
    assert "state.busyResultEventId = null;" in stop
    assert "started.active.stopped || activeThreadRequests[started.laneKey] !== started.active" in revise

    # Success, removed-card reconciliation, API failure, and network failure
    # all finish the request and clear the scoped revise busy marker.
    assert revise.count("finishLlmEditRequest(idx, started);") == 5
    assert "finishThreadRequest(idx, started.resultEventId, started.requestId);" in finish
    assert "state.busyIndex === idx && state.busyKind === 'revise'" in finish
    assert "state.composerMode = 'chat';" in finish

    # Chat cancellation advances queued Chat; an edit cancellation never
    # promotes a queue that was forbidden while the edit lock was active.
    assert "if (stoppedKind === 'discuss') promoteNextQueuedThreadMessage(idx);" in stop


def test_manual_edit_is_scoped_but_selection_remains_available_during_mutations():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    helper = js[js.index("function hasActiveHypothesisRequest"):
                js.index("function studioViewKey")]
    card = js[js.index("function renderCard"):
              js.index("function regenerationVersionControls")]
    open_review = js[js.index("function openReview"):
                     js.index("function closeReview")]
    confirm_review = js[js.index("async function confirmReview"):
                        js.index("// ---- shell visibility")]

    # H2's LLM request and H3's regeneration must not disable H1. The common
    # predicate considers only the requested card's operation state.
    assert "isRegeneratingHypothesis(idx)" in helper
    assert "Object.values(state.pendingThreadRequests || {}).some" in helper
    assert "Object.values(activeThreadRequests).some" in helper
    assert "state.busyIndex === idx" in helper
    assert "hasAnyRegeneration()" not in helper
    assert "hasAnyPendingThread()" not in helper

    # Selection captures the displayed snapshot and remains actionable during
    # chat, LLM edit, manual save, or another card's regeneration.
    assert "selectBtn.disabled" not in card
    assert "hasAnyRegeneration()" not in card
    assert "hasAnyPendingThread()" not in card

    assert "hasHypothesisContentMutation" not in open_review
    assert "hasHypothesisContentMutation" not in confirm_review
    assert "hasAnyRegeneration()" not in open_review
    assert "hasAnyPendingThread()" not in open_review
    assert "hasAnyRegeneration()" not in confirm_review
    assert "hasAnyPendingThread()" not in confirm_review


def test_manual_save_does_not_disable_other_cards_version_navigation_or_its_cancel_action():
    static = Path(app_module.app.static_folder)
    js = (static / "hypothesis_studio.js").read_text(encoding="utf-8")
    css = (static / "hypothesis_studio.css").read_text(encoding="utf-8")
    controls = js[js.index("function regenerationVersionControls"):
                  js.index("function manualEditBlock")]
    manual = js[js.index("function manualEditBlock"):
                js.index("function buildOverflow")]
    save = js[js.index("async function runManualEdit"):
              js.index("async function runRemove")]

    assert "isManualEditSaving(idx)" in controls
    assert "state.busyIndex" not in controls
    assert "cancelManualEdit(idx);" in manual
    assert "savePending ? 'Saving...' : 'Save Changes'" in manual
    assert "activeManualEditRequests[idx] = activeRequest;" in save
    assert "state.busyIndex = idx" not in save
    assert "state.busyKind = 'manual-edit'" not in save
    sticky = css[css.index(
        ".hs-hypothesis-surface.has-expanded-card .hs-card.is-expanded .hs-manual-edit-actions"
    ):css.index(
        ".hs-hypothesis-surface.has-expanded-card .hs-card.is-expanded.is-selected .hs-manual-edit-actions"
    )]
    assert "margin: 16px 0 0;" in sticky
    assert "margin-bottom: -" not in sticky


def test_completed_llm_edit_preserves_another_cards_manual_editor_and_save_state():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    revise = js[js.index("async function sendRevise"):
                js.index("// ---- regenerate / manual edit / remove")]
    manual = js[js.index("async function runManualEdit"):
                js.index("async function runRemove")]
    finish = js[js.index("function finishLlmEditRequest"):
                js.index("async function sendRevise")]

    # H1's response owns only H1 state. It must not close or erase a manual
    # draft that was opened on H3 while the LLM request was in flight.
    assert "if (state.manualEditIndex === idx) {" in revise
    scoped_manual_clear = revise.index("if (state.manualEditIndex === idx) {")
    assert scoped_manual_clear < revise.index("state.manualEditIndex = null;", scoped_manual_clear)
    assert scoped_manual_clear < revise.index("state.manualEditDraft = '';", scoped_manual_clear)

    # Manual saves use their own per-card request registry, so an H3 save never
    # replaces H1's revise marker and neither completion can clobber the other.
    assert "state.busyIndex === idx && state.busyKind === 'revise'" in finish
    assert "activeManualEditRequests[idx] = activeRequest;" in manual
    assert "manualEditRequest(idx) !== activeRequest" in manual
    assert "state.busyKind = 'manual-edit';" not in manual
    assert "setBusy(true);" not in manual
    assert "function cancelManualEdit(idx)" in manual


def test_version_navigation_remains_available_during_scoped_agent_response():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")

    navigate = js[js.index("function navigateRegenerationVersion"):js.index("// Per-hypothesis chat threads")]
    controls = js[js.index("function regenerationVersionControls"):js.index("function manualEditBlock")]
    assert "hasPendingThread(idx)" not in navigate
    assert "hasPendingThread(idx)" not in controls
    assert "isRegeneratingHypothesis(idx)" not in navigate
    assert "isRegeneratingHypothesis(idx)" not in controls
    assert "previous.disabled = blocked || view.viewedPosition <= 0" in controls
    assert "next.disabled = blocked || view.viewedPosition >= view.results.length - 1" in controls
    assert "function isViewedThreadPending(index)" in js
    assert "hasPendingThread(idx, currentThreadResultEventId(idx))" in js
    assert "if (isViewedThreadPending(state.focusIndex))" in js
    assert "function hasBlockingCardMutation()" not in js
    assert "isManualEditSaving(idx)" in navigate
    assert "isManualEditSaving(idx)" in controls
    assert "state.events.indexOf(optimisticRequestEvent)" in js


def test_request_provenance_never_reapplies_visible_version_ownership():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    pending = js[js.index("function applyPendingThreadFromStorage"):
                 js.index("async function pollPendingThreadCompletion")]
    restore = js[js.index("async function restoreCurrentCardsFromHistory"):
                 js.index("async function refreshAfterRegenerationCompletion")]
    revise = js[js.index("async function sendRevise"):
                js.index("// ---- regenerate / manual edit / remove")]

    assert "state.focusIndex = pending.index" not in pending
    assert "state.viewedRegenerationPositions[pending.index]" not in pending
    assert "busyResultEventId = pending.resultEventId" in pending
    assert "const preservedViewedResultIds = viewedVersionResultIds();" in restore
    assert "restoreViewedVersionResultIds(preservedViewedResultIds);" in restore
    assert "authoritativeRevisionResponse(data, started, h)" in revise
    assert "authoritativeRevision: authoritativeRevision" in revise
    assert "setViewedVersionResultId(" in restore
    assert "state.focusIndex = authoritativeRevision.index" not in revise


def test_background_regeneration_completion_does_not_rerender_focused_transcript():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    regenerate = js[js.index("async function runRegenerate"):js.index("function cancelRegenerate")]

    assert "if (Number(state.focusIndex) === responseIndex)" in regenerate
    assert "render();" in regenerate
    assert "Repaint only the cards" in regenerate
    assert "renderCards();" in regenerate


def test_hypotheses_use_an_always_visible_constrained_accessible_workspace_divider():
    static = Path(app_module.app.static_folder)
    js = (static / "hypothesis_studio.js").read_text(encoding="utf-8")
    css = (static / "hypothesis_studio.css").read_text(encoding="utf-8")
    html = (Path(app_module.app.root_path) / app_module.app.template_folder / "hypothesis_studio.html").read_text(encoding="utf-8")

    assert 'id="hs-workspace-divider"' in html
    assert 'role="separator"' in html
    assert 'aria-orientation="horizontal"' in html
    assert 'aria-disabled="true" tabindex="-1"' in html
    assert 'aria-disabled="true" tabindex="-1" hidden' not in html
    assert "workspaceDivider: $('hs-workspace-divider')" in js
    assert "const MIN_TRANSCRIPT_PREVIEW_HEIGHT = 88;" in js
    assert "const topRowCards = grid ? Array.from(grid.children).slice(0, columns) : [];" in js
    assert "rememberCollapsedHypothesisHeight();" in js
    assert "Math.max(176, Math.ceil(hypothesisSplit.collapsedHeight || 0))" in js
    assert "visibleOuterHeight(els.chatScope)" in js
    assert "visibleOuterHeight(els.composer)" in js
    assert "fixedChatHeight - MIN_TRANSCRIPT_PREVIEW_HEIGHT" in js
    assert "applyHypothesisPanelHeight(event.clientY - workspaceRect.top" in js
    assert "divider.addEventListener('pointerdown'" in js
    assert "divider.addEventListener('keydown'" in js
    assert "event.key === 'ArrowUp'" in js and "event.key === 'ArrowDown'" in js
    assert "event.key === 'Home'" in js and "event.key === 'End'" in js
    assert "syncHypothesisWorkspaceDivider(true);" in js
    assert "els.workspaceMain.classList.toggle('has-hypothesis-split', hasCards);" in js
    assert "els.workspaceDivider.hidden = !hasCards;" in js
    assert "applyHypothesisPanelHeight(workspaceHeight * ratio, false);" in js
    assert "window.addEventListener('resize', () => {\n            requestAnimationFrame(() => {" in js
    assert js.count("rememberCollapsedHypothesisHeight();") >= 2
    assert "useDefaultExpandedHeight" not in js
    assert "installStaticScrollBlock" not in js
    assert "blockStaticHypothesisSurfaceWheel" not in js
    assert "wireHypothesisWorkspaceDivider();" in js

    assert ".hs-workspace-divider {" in css
    assert "cursor: row-resize" in css
    assert "touch-action: none" in css
    assert ".hs-workspace-main.has-hypothesis-split .hs-cards" in css
    assert "flex: 0 0 var(--hs-hypothesis-panel-height, 40%)" in css
    assert "min-height: var(--hs-hypothesis-panel-min, 176px)" in css
    assert ".hs-workspace-main.is-resizing-hypotheses" in css
    assert ".hs-workspace-main.has-hypothesis-split .hs-hypothesis-surface" in css
    assert "overflow-y: auto" in css
    assert "flex: 1 1 auto" in css


def test_each_round_defaults_to_exact_card_height_then_persists_only_user_split():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    measure = js[js.index("function completeCollapsedHypothesisHeight"):
                 js.index("function hypothesisPanelLimits")]
    layout = js[js.index("function hypothesisPanelContextKey"):
                js.index("function syncHypothesisWorkspaceDivider")]
    generate = js[js.index("async function generate()"):
                  js.index("// ---- focused-hypothesis chat submit")]
    polling = js[js.index("function pollGenerationCompletion"):
                 js.index("// ---- generation (welcome")]

    # The initial pane height is measured from the complete rendered grid, not
    # inferred from a fixed row count or the one-row minimum.
    assert "grid.offsetHeight" in measure
    assert "gridRect.bottom - surfaceRect.top" not in measure
    assert ") + 2;" in measure
    assert "hypothesisSplit.fitAllOnNextLayout = true;" in measure

    # Untouched defaults are round-specific and remeasure exact content rather
    # than inheriting another round's ratio or persisting empty space.
    assert "hypothesisPanelRatioKeyPrefix" in js
    assert "state.viewRoundIndex" in layout
    assert "hypothesisSplit.ratio = readStoredNumber(contextKey, null);" in layout
    assert "if (!Number.isFinite(hypothesisSplit.ratio))" in layout
    assert "applyHypothesisPanelHeight(completeHeight, false);" in layout
    assert "observeAutomaticHypothesisHeight();" in layout

    # Late font/title wrapping keeps the untouched pane fitted. A deliberate
    # pointer or keyboard resize stores only this round's ratio and stops the
    # automatic content observer.
    assert "hypothesisPanelContentObserver = new ResizeObserver" in layout
    assert "hypothesisPanelContentObserver.observe(grid);" in layout
    assert "window.localStorage.setItem(contextKey" in layout
    assert "hypothesisPanelContentObserver.disconnect();" in layout
    assert "applyHypothesisPanelHeight(event.clientY - workspaceRect.top" in js

    # Both uninterrupted and resumed full generation clear that round's prior
    # user split so the newly generated candidate set gets the exact-row default.
    assert "requestGeneratedHypothesisFit();" in generate
    assert "requestGeneratedHypothesisFit();" in polling
    assert js.count("requestGeneratedHypothesisFit();") == 2


def test_full_view_and_manual_edit_share_half_height_expansion_floor():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    card = js[js.index("function renderCard"):
              js.index("function regenerationVersionControls")]
    prime = js[js.index("function primeHypothesisExpansionPanel"):
               js.index("function manualEditRequest")]
    manual = js[js.index("function openManualEdit"):
                js.index("async function runManualEdit")]
    layout = js[js.index("function applyHypothesisPanelLayout"):
                js.index("function syncHypothesisWorkspaceDivider")]

    assert "const EXPANDED_VIEW_DEFAULT_RATIO = 0.5;" in js
    assert "HSL.isOpeningFirstVisibleExpansion(" in prime
    assert "state.regenerationRequests" in prime
    assert "if (!openingFirstFullHypothesis) return;" in prime
    assert "HSL.hypothesisExpansionPanelRatio(" in prime
    assert "EXPANDED_VIEW_DEFAULT_RATIO" in prime
    expand_handler = card[card.index("expandBtn.addEventListener"):
                          card.index("if (expanded) {")]
    assert "primeHypothesisExpansionPanel(idx, nextExpanded);" in expand_handler
    manual_prime = manual.index("primeHypothesisExpansionPanel(idx, true);")
    manual_expand = manual.index("state.expanded = { [idx]: true };")
    assert manual_prime < manual_expand
    assert "idx === 1" not in expand_handler
    assert "firstExpansionAdjusted" not in js

    # The half-height is temporary UI state: it survives rerenders while a card
    # remains expanded, but a user resize supersedes it and collapsing every
    # card returns the untouched round to its exact-row automatic fit.
    assert "workspaceHeight * hypothesisSplit.temporaryExpandedRatio" in layout
    assert "applyHypothesisPanelHeight(" in layout
    assert "temporaryExpandedRatio = null" in card
    assert "visibleExpandedHypotheses()" in card
    assert "if (rememberRatio && limits.workspaceHeight > 0)" in js
    remember = js[js.index("if (rememberRatio && limits.workspaceHeight > 0)"):
                  js.index("function observeAutomaticHypothesisHeight")]
    assert "hypothesisSplit.temporaryExpandedRatio = null;" in remember


def test_hypothesis_workspace_css_uses_grouped_three_column_surface():
    css = (Path(app_module.app.static_folder) / "hypothesis_studio.css").read_text(encoding="utf-8")
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    assert ".hs-hypothesis-surface" in css
    assert "grid-template-columns: repeat(var(--hs-card-columns), minmax(0, 1fr))" in css
    assert "--hs-card-columns: 3" in css
    assert "@container hs-hypotheses (max-width: 1180px)" in css
    assert "@container hs-hypotheses (max-width: 780px)" in css
    assert "syncCardLayout(grid)" in js
    assert "align-items: start" in css
    assert "max-height: min(54vh, 560px)" in css
    assert "grid-template-columns: 1fr;" in css
    assert "padding: 0 0 5px" in css
    assert "gap: 12px" in css
    assert "margin-bottom: 12px" in css
    assert "font-size: clamp(1.2rem, 1.9vw, 1.55rem)" in css
    # Collapsed cards grow to show the full (word-budgeted) title, so they use a
    # min-height rather than a fixed height that would clip a longer valid title.
    assert ".hs-card.is-collapsed { min-height:" in css
    assert ".hs-card.is-collapsed { height:" not in css
    assert ".hs-card.is-expanded { height: auto; }" in css
    assert "card.classList.add('is-collapsed')" in js
    assert "card.classList.add('is-expanded')" in js
    assert ".hs-manual-edit-input" in css
    assert "min-height: 260px" in css
    assert "hs-card-remove" not in css
    assert "hs-card-remove" not in js
    assert "menuItem('Remove'" not in js
    assert ".hs-card-titlebtn:hover .hs-card-title" not in css


def test_expanded_hypothesis_card_controls_stick_to_scroll_viewport():
    css = (Path(app_module.app.static_folder) / "hypothesis_studio.css").read_text(encoding="utf-8")
    selector = ".hs-hypothesis-surface.has-expanded-card .hs-card.is-expanded .hs-card-controls {"
    start = css.index(selector)
    rule = css[start:css.index("}", start)]

    for declaration in (
        "position: sticky",
        "bottom: 0",
        "z-index: 4",
        "border-top: 1px solid var(--v2-border-soft)",
        "background: var(--v2-surface)",
    ):
        assert declaration in rule

    surface_selector = ".hs-hypothesis-surface.has-expanded-card {"
    surface_start = css.index(surface_selector)
    surface_rule = css[surface_start:css.index("}", surface_start)]
    assert "padding-bottom: 0" in surface_rule


def test_legacy_interview_and_chat_import_routes_are_removed():
    routes = {rule.rule for rule in app_module.app.url_map.iter_rules()}
    assert "/api/hypothesis_intent" not in routes
    assert "/api/import_chat_context" not in routes


def test_studio_controller_declares_all_referenced_elements():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    start = js.index("const els = {")
    end = js.index("    };", start)
    els_body = js[start:end]
    import re
    declared = set(re.findall(r"\b([A-Za-z_]\w*):\s*\$\(", els_body))
    referenced = set(re.findall(r"\bels\.([A-Za-z_]\w*)", js))
    assert referenced - declared == set()


def test_execution_directory_without_hypothesis_set_is_not_a_studio_round(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    # round_0 exists (created by the project) but has nothing in it yet.
    response = client.get("/api/hypothesis_history/0")
    assert response.status_code == 404
    assert response.get_json()["success"] is False


def test_durable_history_is_served_as_events(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd = root / "round_0"
    hypothesis_history.append_events(rd, [
        hypothesis_history.user_message_event("Focus axon growth", "intent"),
        hypothesis_history.assistant_message_event("Will do.", "intent", status="ready_to_generate"),
        hypothesis_history.hypothesis_set_generated_event(
            [{"index": 1, "full_text": HYP1, "hypothesis": "mTOR", "title": "Axon growth"}]),
        hypothesis_history.hypothesis_selected_event({"hypothesis": "mTOR", "full_text": HYP1}),
    ])
    data = client.get("/api/hypothesis_history/0").get_json()
    assert data["partial_history"] is False
    kinds = [e["kind"] for e in data["transcript"]]
    assert kinds == ["message", "message", "hypotheses", "selection"]
    # events carry the full durable schema.
    assert [e["type"] for e in data["events"]][-1] == "hypothesis_selected"


def test_durable_full_text_only_hypotheses_are_parsed_for_transcript(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd = root / "round_0"
    hypothesis_history.append_events(rd, [
        {
            "type": hypothesis_history.TYPE_HYPOTHESIS_SET_GENERATED,
            "phase": hypothesis_history.PHASE_HYPOTHESIS_DISCUSSION,
            "hypotheses": [{"index": 1, "full_text": HYP1}],
        },
    ])

    data = client.get("/api/hypothesis_history/0").get_json()
    hyp = data["transcript"][0]["hypotheses"][0]
    assert hyp["title"] == "Axon growth"
    assert hyp["hypothesis"] == "RGC survival depends on mTOR reactivation."
    assert hyp["justification"] == "Prior work links mTOR to regeneration."
    assert hyp["plan"] == "Compare mTOR-high vs low."
    assert "**Justification**" not in hyp["hypothesis"]


def test_backward_compat_round_marked_partial(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd = root / "round_0"
    rd.joinpath("generated_hypotheses.json").write_text(json.dumps(
        {"hypotheses": [HYP1], "hypothesis_state_ids": ["s0"]}))
    rd.joinpath("selected_hypothesis.md").write_text("**Hypothesis**: RGC survival via mTOR.")
    data = client.get("/api/hypothesis_history/0").get_json()
    assert data["partial_history"] is True
    assert data["read_only"] is False
    assert [e["kind"] for e in data["transcript"]] == ["hypotheses", "selection"]
    assert "mTOR" in data["selected_hypothesis"]["hypothesis"]
    assert data["selected_hypothesis"]["title"] == "RGC survival via mTOR."
    assert data["transcript"][-1]["hypothesis"]["title"] == "RGC survival via mTOR."
    assert data["transcript"][-1]["hypothesis"]["index"] == 1


def _seed_selected_round_one_cycle(tmp_path):
    root = _project_round_root(tmp_path)
    source = root / "round_0"
    source.joinpath("round_report.md").write_text("# Round 0 Report", encoding="utf-8")
    source.joinpath("memory_report.md").write_text("# Round 0 Memory", encoding="utf-8")
    source.joinpath("generated_hypotheses.json").write_text(json.dumps({
        "hypotheses": [HYP1, HYP2],
        "hypothesis_state_ids": ["s1", "s2"],
    }), encoding="utf-8")
    source.joinpath("selected_hypothesis.md").write_text(HYP1, encoding="utf-8")
    return root, source


def _write_round_chronology(source, *, selected_at, report_at):
    source.joinpath("hypothesis_studio_history.json").write_text(json.dumps({
        "version": 1,
        "events": [
            {
                "id": "set-event", "sequence": 1,
                "timestamp": selected_at,
                "type": "hypothesis_set_generated",
                "phase": "hypothesis_discussion",
                "hypotheses": [{"index": 1, "state_id": "s1", "full_text": HYP1}],
            },
            {
                "id": "selection-event", "sequence": 2,
                "timestamp": selected_at,
                "type": "hypothesis_selected",
                "phase": "hypothesis_discussion",
                "hypothesis": {"index": 1, "state_id": "s1", "full_text": HYP1},
            },
        ],
    }), encoding="utf-8")
    source.joinpath("round_activity.jsonl").write_text(json.dumps({
        "timestamp": report_at,
        "event_type": "round_report_saved",
    }) + "\n", encoding="utf-8")


def test_completed_round_candidates_keep_historical_selection_out_of_round_one(
    monkeypatch, tmp_path,
):
    client = _project_client(monkeypatch, tmp_path)
    _root, source = _seed_selected_round_one_cycle(tmp_path)
    _write_round_chronology(
        source,
        selected_at="2026-01-15T10:03:00+00:00",
        report_at="2026-01-15T10:08:00+00:00",
    )

    listing = client.get("/api/hypothesis_history").get_json()
    current = listing["rounds"][0]
    assert current["status"] == "exploring"
    assert current["has_selected"] is False
    assert current["has_recorded_selection"] is True
    assert current["selection_scope"] == "historical"
    assert current["ready_to_start"] is False

    detail = client.get("/api/hypothesis_history/0").get_json()
    assert detail["selected_hypothesis"]["full_text"] == HYP1
    assert detail["selection_scope"] == "historical"
    assert detail["transcript"][-1]["applies_to_target_round"] is False


def test_post_completion_selection_is_ready_for_round_one(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    _root, source = _seed_selected_round_one_cycle(tmp_path)
    _write_round_chronology(
        source,
        selected_at="2026-01-15T10:09:00+00:00",
        report_at="2026-01-15T10:08:00+00:00",
    )

    current = client.get("/api/hypothesis_history").get_json()["rounds"][0]
    assert current["status"] == "selected"
    assert current["selection_scope"] == "target_round"
    assert current["ready_to_start"] is True
    assert current["start_blocked_reason"] is None


def test_selected_continuation_with_missing_config_is_actionably_blocked(
    monkeypatch, tmp_path,
):
    client = _project_client(monkeypatch, tmp_path)
    _root, source = _seed_selected_round_one_cycle(tmp_path)
    _write_round_chronology(
        source,
        selected_at="2026-01-15T10:09:00+00:00",
        report_at="2026-01-15T10:08:00+00:00",
    )
    source.joinpath("config.json").unlink()

    current = client.get("/api/hypothesis_history").get_json()["rounds"][0]
    assert current["status"] == "setup_required"
    assert current["has_selected"] is True
    assert current["ready_to_start"] is False
    assert "config.json" in current["start_blocked_reason"]


def _write_project_run_state(tmp_path, *, status, round_number):
    project = app_module._v2_load_projects()[0]
    state_path = Path(project["project_root"]) / "checkpoint" / "run_state.json"
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "project_id": project["id"],
        "round": round_number,
        "status": status,
        "pid": None,
        "updated_at": "2026-08-07T00:00:00+00:00",
    }), encoding="utf-8")
    return project, state_path


def test_selected_started_and_stopped_round_binding_is_permanent(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root, source = _seed_selected_round_one_cycle(tmp_path)

    selected = client.get("/api/hypothesis_history/0").get_json()
    assert selected["status"] == "selected"
    assert selected["read_only"] is False

    target = root / "round_1"
    target.mkdir()
    target.joinpath("config.json").write_text("{}", encoding="utf-8")
    _write_project_run_state(tmp_path, status="running", round_number=1)
    started = client.get("/api/hypothesis_history/0").get_json()
    assert started["status"] == "running"
    assert started["read_only"] is True

    _project, _state_path = _write_project_run_state(
        tmp_path, status="interrupted", round_number=1,
    )
    stopped = client.get("/api/hypothesis_history/0").get_json()
    assert stopped["status"] == "stopped"
    assert stopped["read_only"] is True
    assert target.exists()
    assert source.joinpath("generated_hypotheses.json").exists()
    assert source.joinpath("selected_hypothesis.md").exists()


def test_completed_target_round_remains_historical_and_read_only(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root, _source = _seed_selected_round_one_cycle(tmp_path)
    target = root / "round_1"
    target.mkdir()
    target.joinpath("round_report.md").write_text("# Round 1 Report", encoding="utf-8")
    target.joinpath("memory_report.md").write_text("# Round 1 Memory", encoding="utf-8")
    _project, _state_path = _write_project_run_state(
        tmp_path, status="completed", round_number=1,
    )

    detail = client.get("/api/hypothesis_history/0").get_json()
    assert detail["status"] == "completed"
    assert detail["read_only"] is True

    assert target.exists()


def test_prelaunch_set_allows_manual_llm_regenerate_and_reselect(monkeypatch, tmp_path):
    root = tmp_path / "checkpoints"
    source = root / "round_0"
    source.mkdir(parents=True)
    source.joinpath("round_report.md").write_text("# Round 0", encoding="utf-8")
    source.joinpath("memory_report.md").write_text("# Memory 0", encoding="utf-8")

    gen = FakeGenerator(source)
    _seed_generated(gen)
    client = _wire(monkeypatch, gen)
    _allow_test_selection(monkeypatch)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")
    with client.session_transaction() as session_state:
        session_state["v2_active_project_id"] = "project-1"
        session_state["project_config"] = {"checkpoint_dir": str(source)}

    manual_text = HYP1.replace("mTOR reactivation", "mTOR signaling")
    manual = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1", "manual_hypothesis": manual_text,
    })
    assert manual.status_code == 200 and manual.get_json()["success"] is True

    llm = client.post("/api/edit", json={
        "index": 1,
        "state_id": manual.get_json()["hypothesis"]["state_id"],
        "feedback": "Make this more mechanistic.",
    })
    assert llm.status_code == 200 and llm.get_json()["success"] is True

    regenerated = client.post("/api/regenerate", json={
        "index": 1,
        "state_id": llm.get_json()["hypothesis"]["state_id"],
        "feedback": "Try a distinct mechanism.",
    })
    assert regenerated.status_code == 200 and regenerated.get_json()["success"] is True

    chosen = regenerated.get_json()["hypothesis"]
    selected = client.post("/api/select", json={
        "selection": 1,
        "selection_state_id": chosen["state_id"],
        "final_text": chosen["full_text"],
    })
    assert selected.status_code == 200 and selected.get_json()["success"] is True
    assert source.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == chosen["full_text"]

    (root / "round_1").mkdir()
    locked_mutations = (
        ("/api/generate", {"num_hypotheses": 2}),
        ("/api/edit", {"index": 1, "state_id": chosen["state_id"], "manual_hypothesis": HYP1}),
        ("/api/edit", {"index": 1, "state_id": chosen["state_id"], "feedback": "Revise it."}),
        ("/api/regenerate", {"index": 1, "state_id": chosen["state_id"]}),
        ("/api/regenerate_guidance", {"index": 1, "state_id": chosen["state_id"]}),
        ("/api/remove_hypothesis", {"index": 1, "state_id": chosen["state_id"]}),
        ("/api/select", {"selection": 1, "selection_state_id": chosen["state_id"]}),
    )
    for endpoint, payload in locked_mutations:
        locked = client.post(endpoint, json=payload)
        assert locked.status_code == 409
        assert locked.get_json()["code"] == "hypothesis_round_locked"


def test_selection_without_durable_set_does_not_create_studio_round(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    rd.joinpath("selected_hypothesis.md").write_text(
        "**Hypothesis**: RGC survival via mTOR.", encoding="utf-8"
    )

    listing = client.get("/api/hypothesis_history").get_json()
    assert listing["rounds"] == []
    assert client.get("/api/hypothesis_history/0").status_code == 404


def test_legacy_session_restores_only_the_single_displayed_selected_hypothesis(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    session_dir = rd / "interactive_hyp_gen"
    session_dir.mkdir()
    session_dir.joinpath("session_20260616_141509.json").write_text(json.dumps({
        "generated_hypotheses": [HYP1, HYP2, HYP3],
        "final_selection": {
            "selection_type": "raw",
            "selection_value": None,
            "final_hypothesis": PLAIN_HYP,
        },
    }), encoding="utf-8")
    # Generator restoration wrote empty sessions after some completed legacy
    # runs. They must not hide the earlier non-empty candidate snapshot.
    session_dir.joinpath("session_20260616_142402.json").write_text(json.dumps({
        "generated_hypotheses": [],
        "final_selection": None,
    }), encoding="utf-8")
    rd.joinpath("selected_hypothesis.md").write_text(PLAIN_HYP, encoding="utf-8")
    rd.joinpath("round_report.md").write_text("# Completed exploratory round", encoding="utf-8")

    listing = client.get("/api/hypothesis_history").get_json()
    assert [item["round_index"] for item in listing["rounds"]] == [0]
    assert listing["rounds"][0]["has_generated"] is False
    assert listing["rounds"][0]["has_selected"] is False
    assert listing["rounds"][0]["has_recorded_selection"] is True
    assert listing["rounds"][0]["selection_scope"] == "historical"

    response = client.get("/api/hypothesis_history/0")
    assert response.status_code == 200
    detail = response.get_json()
    assert [item["full_text"] for item in detail["generated_hypotheses"]] == [PLAIN_HYP]
    assert detail["selected_hypothesis"]["full_text"] == PLAIN_HYP
    assert detail["selected_hypothesis"]["index"] == 1
    assert len(detail["transcript"][0]["hypotheses"]) == 1
    assert [item["kind"] for item in detail["transcript"]] == ["hypotheses", "selection"]
    assert detail["partial_history"] is True


def test_legacy_session_selection_is_used_when_markdown_is_missing(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    session_dir = rd / "interactive_hyp_gen"
    session_dir.mkdir()
    session_dir.joinpath("session_20260616_141509.json").write_text(json.dumps({
        "generated_hypotheses": [HYP1, HYP2],
        "final_selection": {
            "selection_type": "raw",
            "selection_value": None,
            "final_hypothesis": HYP2,
        },
    }), encoding="utf-8")

    detail = client.get("/api/hypothesis_history/0").get_json()
    assert detail["selected_hypothesis"]["full_text"] == HYP2
    assert detail["selected_hypothesis"]["index"] == 1
    assert detail["transcript"][-1]["hypothesis"]["index"] == 1
    assert len(detail["generated_hypotheses"]) == 1


def test_durable_selection_without_index_is_not_misattributed_to_first_card(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    first = {"index": 1, "state_id": "a", "hypothesis": "Alpha"}
    second = {"index": 2, "state_id": "b", "hypothesis": "Beta"}
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([first, second]),
        hypothesis_history.hypothesis_removed_event(3, {"index": 3, "hypothesis": "Gamma"}, [first, second]),
        hypothesis_history.hypothesis_selected_event({"hypothesis": "Beta"}),
    ])

    data = client.get("/api/hypothesis_history/0").get_json()

    selection = data["transcript"][-1]
    assert selection["kind"] == "selection"
    assert selection["hypothesis"]["hypothesis"] == "Beta"
    assert selection["hypothesis"].get("index") is None


def test_execution_round_never_borrows_previous_studio_selection(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    # round_1 directs autonomous round 2 via the cross-round convention: the
    # selection lives in round_1, round_2 has no studio cycle of its own.
    (root / "round_1").mkdir(parents=True, exist_ok=True)
    (root / "round_1" / "selected_hypothesis.md").write_text("**Hypothesis**: mTOR directs round 2.")
    (root / "round_2").mkdir(parents=True, exist_ok=True)  # pure autonomous round, no own files

    path = app_module._v2_selected_hypothesis_path_for_round(root / "round_2")
    assert path is None
    assert client.get("/api/hypothesis_history/2").status_code == 404


def test_round_with_own_generated_does_not_borrow_previous_selection(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    (root / "round_1").mkdir(parents=True, exist_ok=True)
    (root / "round_1" / "selected_hypothesis.md").write_text("**Hypothesis**: prior round choice.")
    (root / "round_2").mkdir(parents=True, exist_ok=True)
    # round_2 has its OWN in-progress cycle (generated set) but no selection yet.
    (root / "round_2" / "generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP2]}))
    data = client.get("/api/hypothesis_history/2").get_json()
    assert data["selected_hypothesis"] is None  # not yet selected; no misattribution


def test_current_round_with_cleared_history_does_not_borrow_previous_selection(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    previous = root / "round_0"
    previous.joinpath("selected_hypothesis.md").write_text(HYP1, encoding="utf-8")

    current = root / "round_1"
    current.mkdir(parents=True, exist_ok=True)
    current.joinpath("round_report.md").write_text("# Round 1 complete", encoding="utf-8")
    snapshot = {"index": 1, "state_id": "cleared-1", "full_text": HYP2}
    hypothesis_history.append_events(current, [
        hypothesis_history.hypothesis_set_generated_event([snapshot]),
        hypothesis_history.hypothesis_removed_event(1, snapshot, []),
    ])

    data = client.get("/api/hypothesis_history/1").get_json()

    assert data["is_current"] is True
    assert data["read_only"] is False
    assert data["selected_hypothesis"] is None
    assert data["generated_hypotheses"] == []


def test_historical_get_makes_no_model_call_or_generator_mutation(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    (root / "round_0" / "round_report.md").write_text(
        "# Round 0 complete", encoding="utf-8"
    )
    (root / "round_0" / "generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP1]}))
    # If history reads ever touched a generator/model, these would blow up.
    def _boom(*a, **k):
        raise AssertionError("history read must not build/restore a generator")
    monkeypatch.setattr(app_module, "get_or_restore_generator", _boom)
    resp = client.get("/api/hypothesis_history/0")
    assert resp.status_code == 200 and resp.get_json()["success"] is True



# ============================================================================
# Revision-request provenance (scientist request -> revision, in order)
# ============================================================================

def _guided_client(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    _seed_generated(gen)
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")
    return rd, gen, client


def _seed_current_version_history(rd, gen):
    thread_id = "thr_" + "a" * 32
    gen.hypothesis_thread_ids = [thread_id]
    history = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{
            "index": 1,
            "state_id": gen.hypothesis_state_ids[0],
            "full_text": gen.hypotheses[0],
            "hypothesis_thread_id": thread_id,
        }]),
    ])
    return history["events"][0]["id"]


def _allow_test_selection(monkeypatch):
    monkeypatch.setattr(app_module, "_get_project_round_from_disk", lambda: 0)
    monkeypatch.setattr(app_module, "CLI_DRY_RUN", False)


def _version_history(gen):
    with app_module.app.test_request_context("/"):
        return app_module._studio_regeneration_history_for_slot(gen, 1)


def _seed_slot_regeneration_history(rd, gen, historical_text):
    historical_thread = "thr_" + "a" * 32
    current_thread = "thr_" + "c" * 32
    gen.hypothesis_thread_ids = [current_thread]
    history = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{
            "index": 1,
            "state_id": "historical-state",
            "full_text": historical_text,
            "hypothesis_thread_id": historical_thread,
        }]),
        hypothesis_history.hypothesis_regenerated_event(
            1,
            {
                "index": 1,
                "state_id": "historical-state",
                "full_text": historical_text,
                "hypothesis_thread_id": historical_thread,
            },
            {
                "index": 1,
                "state_id": "sid-1",
                "full_text": gen.hypotheses[0],
                "hypothesis_thread_id": current_thread,
            },
        ),
    ])
    return history["events"][0]["id"], historical_thread, current_thread


def _seed_four_version_history(rd, gen):
    texts = [
        "**Title**: Version one\n**Hypothesis**: Historical mechanism one.",
        "**Title**: Version two\n**Hypothesis**: Historical mechanism two.",
        "**Title**: Version three\n**Hypothesis**: Historical mechanism three.",
        "**Title**: Version four\n**Hypothesis**: Current mechanism four.",
    ]
    threads = ["thr_" + digit * 32 for digit in ("1", "2", "3", "4")]
    states = [f"sid-{number}" for number in range(1, 5)]
    events = [hypothesis_history.hypothesis_set_generated_event([{
        "index": 1,
        "state_id": states[0],
        "full_text": texts[0],
        "hypothesis_thread_id": threads[0],
    }])]
    for position in range(1, 4):
        events.append(hypothesis_history.hypothesis_regenerated_event(
            1,
            {
                "index": 1,
                "state_id": states[position - 1],
                "full_text": texts[position - 1],
                "hypothesis_thread_id": threads[position - 1],
            },
            {
                "index": 1,
                "state_id": states[position],
                "full_text": texts[position],
                "hypothesis_thread_id": threads[position],
            },
        ))
    history = hypothesis_history.append_events(rd, events)
    gen.hypotheses = [texts[-1]]
    gen.hypothesis_state_ids = [states[-1]]
    gen.hypothesis_thread_ids = [threads[-1]]
    return {
        "texts": texts,
        "threads": threads,
        "states": states,
        "result_ids": [
            history["events"][0]["id"],
            history["events"][1]["id"],
            history["events"][2]["id"],
            history["events"][3]["id"],
        ],
    }


def test_chat_resolves_explicit_version_references_from_durable_slot_history(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    historical = (
        "**Title**: Original mechanism\n"
        "**Hypothesis**: Version one exact mechanism.\n"
        "**Justification**: Original evidence."
    )
    _seed_slot_regeneration_history(rd, gen, historical)

    response = client.post("/api/chat", json={
        "message": "Compare version 2 to version 1.",
        "mode": "hypothesis",
        "hypothesis_index": 1,
    })

    assert response.status_code == 200
    context = gen.last_version_reference_context
    assert "Version 2 (current)" in context
    assert "Version 1 (stored exact text)" in context
    assert "Version one exact mechanism" in context
    assert "do not reconstruct or guess" in context


def test_chat_does_not_attach_version_history_without_explicit_version_reference(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    _seed_slot_regeneration_history(rd, gen, "**Hypothesis**: Older exact text.")

    response = client.post("/api/chat", json={
        "message": "Compare the mechanism across round 1 and round 2.",
        "mode": "hypothesis",
        "hypothesis_index": 1,
    })

    assert response.status_code == 200
    assert gen.last_version_reference_context is None


def test_historical_chat_uses_viewed_snapshot_and_thread_without_creating_version(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    seeded = _seed_four_version_history(rd, gen)

    response = client.post("/api/chat", json={
        "message": "Why does this original mechanism make sense?",
        "mode": "hypothesis",
        "hypothesis_index": 1,
        "regeneration_result_id": seeded["result_ids"][0],
    })

    body = response.get_json()
    assert response.status_code == 200 and body["success"] is True
    assert body["source_result_event_id"] == seeded["result_ids"][0]
    assert gen.last_asked_hypothesis_text == seeded["texts"][0]
    assert gen.used_thread_ids[-1] == seeded["threads"][0]
    # Ordinary historical discussion is temporary model scoping, not activation.
    assert gen.hypotheses == [seeded["texts"][3]]
    assert gen.hypothesis_state_ids == [seeded["states"][3]]
    assert gen.hypothesis_thread_ids == [seeded["threads"][3]]
    history = _version_history(gen)
    assert len(history["results"]) == 4
    assert history["active_position"] == 3
    events = hypothesis_history.load_history(rd)["events"]
    assert [event["type"] for event in events[-2:]] == ["user_message", "assistant_message"]
    assert all(event["thread_id"] == seeded["threads"][0] for event in events[-2:])


def test_historical_llm_revision_from_v1_appends_global_v5_and_preserves_v2_through_v4(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    seeded = _seed_four_version_history(rd, gen)
    with client.session_transaction() as session_state:
        session_state["v2_active_project_slug"] = "isolated-project"

    response = client.post("/api/edit", json={
        "index": 1,
        "state_id": seeded["states"][3],
        "feedback": "Make this more mechanistic.",
        "request_id": "client-revision-v5",
        "action": "edit_regeneration_version",
        "regeneration_position": 0,
        "regeneration_result_id": seeded["result_ids"][0],
    })

    body = response.get_json()
    assert response.status_code == 200 and body["success"] is True
    assert body["created_version_number"] == 5
    assert body["authoritative_version_total"] == 5
    assert body["version_created_message"] == "Created Version 5."
    assert body["project_slug"] == "isolated-project"
    assert body["request_id"] == "client-revision-v5"
    assert body["hypothesis_index"] == body["hypothesis"]["index"] == 1
    assert body["source_state_id"] == seeded["states"][3]
    assert body["source_result_event_id"] == seeded["result_ids"][0]
    assert seeded["texts"][0] in gen.edit_calls[-1]["feedback"]
    assert gen.used_thread_ids[-1] == seeded["threads"][0]
    history = _version_history(gen)
    assert len(history["results"]) == 5
    assert history["active_position"] == 4
    assert [result["snapshot"]["full_text"] for result in history["results"][:4]] == seeded["texts"]
    events = hypothesis_history.load_history(rd)["events"]
    assert events[-3]["thread_id"] == seeded["threads"][0]
    assert events[-2]["source_result_event_id"] == seeded["result_ids"][0]
    assert events[-1]["content"] == "Created Version 5."
    assert events[-1]["thread_id"] == seeded["threads"][0]


def test_historical_llm_revision_from_v2_still_appends_global_v5(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    seeded = _seed_four_version_history(rd, gen)

    body = client.post("/api/edit", json={
        "index": 1,
        "state_id": seeded["states"][3],
        "feedback": "Strengthen the causal intervention.",
        "action": "edit_regeneration_version",
        "regeneration_position": 1,
        "regeneration_result_id": seeded["result_ids"][1],
    }).get_json()

    assert body["success"] is True
    assert body["created_version_number"] == 5
    assert body["version_created_message"] == "Created Version 5."
    assert seeded["texts"][1] in gen.edit_calls[-1]["feedback"]
    assert len(_version_history(gen)["results"]) == 5


def test_feedback_free_regeneration_ignores_historical_display_source_and_appends_v5(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    seeded = _seed_four_version_history(rd, gen)

    response = client.post("/api/regenerate_guidance", json={
        "index": 1,
        # Even a stale browser draft cannot replace a populated canonical slot.
        "request_hypothesis": seeded["texts"][0],
    })

    assert response.status_code == 200 and response.get_json()["success"] is True
    assert gen.used_thread_ids[-1] == seeded["threads"][3]
    events = hypothesis_history.load_history(rd)["events"]
    regeneration = events[-1]
    assert regeneration["type"] == "hypothesis_regenerated"
    assert regeneration["before"]["full_text"] == seeded["texts"][3]
    history = _version_history(gen)
    assert len(history["results"]) == 5
    assert [result["snapshot"]["full_text"] for result in history["results"][:4]] == seeded["texts"]


def test_manual_edit_of_v1_updates_that_result_in_place_and_preserves_later_versions(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    seeded = _seed_four_version_history(rd, gen)
    # V1's seeded title is "Version one" (see _seed_four_version_history). The
    # draft below even carries a *different* embedded title, "Version one
    # corrected" -- manual Edit + Save must ignore it and keep "Version one"
    # exactly, never deriving/replacing the title from the edited body.
    manual_body = "**Title**: Version one corrected\n**Hypothesis**: Scientist-corrected original mechanism."
    edited_v1 = "**Title**: Version one\n\n**Hypothesis**: Scientist-corrected original mechanism."

    response = client.post("/api/edit", json={
        "index": 1,
        "state_id": seeded["states"][3],
        "manual_hypothesis": manual_body,
        "action": "edit_regeneration_version",
        "regeneration_position": 0,
        "regeneration_result_id": seeded["result_ids"][0],
    })

    body = response.get_json()
    assert response.status_code == 200 and body["success"] is True
    assert "created_version_number" not in body
    assert body["hypothesis"]["title"] == "Version one"
    history = _version_history(gen)
    assert len(history["results"]) == 4
    assert history["active_position"] == 0
    assert history["results"][0]["snapshot"]["full_text"] == edited_v1
    assert history["results"][0]["snapshot"]["title"] == "Version one"
    assert [result["snapshot"]["full_text"] for result in history["results"][1:]] == seeded["texts"][1:]
    assert hypothesis_history.load_history(rd)["events"][-1]["type"] == "hypothesis_revised"


def test_remove_hypothesis_falls_back_to_index_when_state_id_is_stale(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1, HYP2]
    gen.hypothesis_state_ids = ["new-sid-1", "sid-2"]
    client = _wire(monkeypatch, gen)

    resp = client.post("/api/remove_hypothesis", json={"index": 1, "state_id": "old-sid-1"})
    body = resp.get_json()

    assert resp.status_code == 200
    assert body["success"] is True
    assert body["removed_index"] == 1
    assert len(body["hypotheses"]) == 1
    assert body["hypotheses"][0]["index"] == 1
    assert body["hypotheses"][0]["state_id"] == "sid-2"
    assert gen.hypotheses == [HYP2]


def test_repeated_remove_with_removed_state_id_cannot_delete_shifted_neighbor(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1, HYP2]
    gen.hypothesis_state_ids = ["sid-1", "sid-2"]
    client = _wire(monkeypatch, gen)

    first = client.post("/api/remove_hypothesis", json={"index": 1, "state_id": "sid-1"})
    assert first.get_json()["success"] is True
    repeated = client.post("/api/remove_hypothesis", json={"index": 1, "state_id": "sid-1"})
    payload = repeated.get_json()

    assert payload["success"] is False
    assert payload["removed"] is True
    assert gen.hypotheses == [HYP2]
    assert gen.hypothesis_state_ids == ["sid-2"]


def test_repeated_remove_with_pre_revision_id_preserves_neighbor(monkeypatch, tmp_path):
    gen = FakeGenerator(_round_dir(tmp_path))
    gen.hypotheses = [HYP1, HYP2]
    gen.hypothesis_state_ids = ["sid-1", "sid-2"]
    client = _wire(monkeypatch, gen)
    gen.hypothesis_state_ids[0] = "sid-1-revised"

    payload = {"index": 1, "state_id": "sid-1"}
    assert client.post("/api/remove_hypothesis", json=payload).get_json()["success"]
    repeated = client.post("/api/remove_hypothesis", json=payload).get_json()

    assert repeated["success"] is False
    assert repeated["removed"] is True
    assert gen.hypotheses == [HYP2]
    assert gen.hypothesis_state_ids == ["sid-2"]


def test_manual_edit_cannot_recreate_removed_last_card(monkeypatch, tmp_path):
    gen = FakeGenerator(_round_dir(tmp_path))
    gen.hypotheses = [HYP1, HYP2]
    gen.hypothesis_state_ids = ["sid-1", "sid-2"]
    client = _wire(monkeypatch, gen)
    assert client.post("/api/remove_hypothesis", json={
        "index": 2, "state_id": "sid-2",
    }).get_json()["success"]

    response = client.post("/api/edit", json={
        "index": 2, "state_id": "sid-2", "manual_hypothesis": HYP2,
    }).get_json()

    assert response["success"] is False
    assert gen.hypotheses == [HYP1]
    assert gen.hypothesis_state_ids == ["sid-1"]


def test_manual_edit_resolves_survivor_at_its_new_index(monkeypatch, tmp_path):
    gen = FakeGenerator(_round_dir(tmp_path))
    gen.hypotheses = [HYP1, HYP2, HYP3]
    gen.hypothesis_state_ids = ["sid-1", "sid-2", "sid-3"]
    client = _wire(monkeypatch, gen)
    assert client.post("/api/remove_hypothesis", json={
        "index": 1, "state_id": "sid-1",
    }).get_json()["success"]
    manual = HYP3.replace("Mitochondrial state", "Edited mitochondrial state")

    response = client.post("/api/edit", json={
        "index": 3, "state_id": "sid-3", "manual_hypothesis": manual,
    }).get_json()

    assert response["success"] is True
    assert response["hypothesis"]["index"] == 2
    assert len(gen.hypotheses) == 2
    assert gen.hypotheses[0] == HYP2
    assert gen.parse_hypothesis_sections(gen.hypotheses[1]) == gen.parse_hypothesis_sections(manual)
    assert len(set(gen.hypothesis_state_ids)) == 2


@pytest.mark.parametrize("endpoint", ["/api/edit", "/api/regenerate", "/api/regenerate_guidance"])
def test_agent_result_preserves_manual_edit_after_removal(monkeypatch, tmp_path, endpoint):
    gen = _chat_generator_for_context_test(_round_dir(tmp_path))
    gen.hypotheses = [HYP1, HYP2, HYP3]
    gen.hypothesis_state_ids = ["sid-1", "sid-2", "sid-3"]
    gen.hypothesis_chat_threads = {}
    client = _wire(monkeypatch, gen)
    revised = HYP2.replace("Microglial state", "Revised microglial state")
    manual = HYP3.replace("Mitochondrial state", "Edited mitochondrial state")
    monkeypatch.setattr(app_module, "_persist_generated_hypotheses", lambda _gen, **kwargs: None)

    @contextmanager
    def thread_context(*args, **kwargs):
        yield

    monkeypatch.setattr(app_module, "_hypothesis_thread_ctx", thread_context)

    def model_response(*args, **kwargs):
        assert client.post("/api/remove_hypothesis", json={
            "index": 1, "state_id": "sid-1",
        }).get_json()["success"]
        return revised

    gen.hypothesis_gen_agent._call_api_with_retry = model_response
    original_reconcile = app_module._reconcile_hypothesis_operation_result

    def manual_edit_then_reconcile(generator, operation, result):
        assert client.post("/api/edit", json={
            "index": 2, "state_id": "sid-3", "manual_hypothesis": manual,
        }).get_json()["success"]
        return original_reconcile(generator, operation, result)

    monkeypatch.setattr(app_module, "_reconcile_hypothesis_operation_result", manual_edit_then_reconcile)
    response = client.post(endpoint, json={
        "index": 2, "state_id": "sid-2", "feedback": "Refine the hypothesis",
    }).get_json()

    assert response["success"] is True
    assert gen.hypotheses[0] == revised
    assert gen.parse_hypothesis_sections(gen.hypotheses[1]) == gen.parse_hypothesis_sections(manual)
    assert len(set(gen.hypothesis_state_ids)) == 2


def test_concurrent_remove_cannot_redirect_a_direct_manual_edit(monkeypatch, tmp_path):
    gen = FakeGenerator(_round_dir(tmp_path))
    gen.hypotheses = [HYP1, HYP2, HYP3]
    gen.hypothesis_state_ids = ["sid-1", "sid-2", "sid-3"]
    client = _wire(monkeypatch, gen)
    edited = HYP2.replace("Microglial state", "Edited microglial state")
    removal_done = threading.Event()
    results = {}

    def remove_first_card():
        results["remove"] = client.post(
            "/api/remove_hypothesis", json={"index": 1, "state_id": "sid-1"}
        ).get_json()
        removal_done.set()

    original_capture = app_module._capture_hypothesis_operation_state

    def capture_then_race_remove(*args, **kwargs):
        captured = original_capture(*args, **kwargs)
        # Remove the card in front of the edited one after the edit resolved
        # its position but before it commits the new text.
        worker = threading.Thread(target=remove_first_card)
        worker.start()
        removal_done.wait(0.5)
        results["worker"] = worker
        return captured

    monkeypatch.setattr(
        app_module, "_capture_hypothesis_operation_state", capture_then_race_remove
    )
    response = client.post("/api/edit", json={
        "index": 2, "state_id": "sid-2", "manual_hypothesis": edited,
    }).get_json()
    results["worker"].join(timeout=2)

    assert response["success"] is True
    assert results["remove"]["success"] is True
    assert len(gen.hypotheses) == 2
    assert "Edited microglial state" in gen.hypotheses[0]
    assert gen.hypotheses[1] == HYP3


def test_remove_final_hypothesis_returns_an_empty_active_set(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1]
    gen.hypothesis_state_ids = ["sid-1"]
    gen.hypothesis_thread_ids = ["thr_" + "a" * 32]
    client = _wire(monkeypatch, gen)

    resp = client.post("/api/remove_hypothesis", json={"index": 1})
    body = resp.get_json()

    assert resp.status_code == 200
    assert body["success"] is True
    assert body["hypotheses"] == []
    assert body["count"] == 0
    assert gen.hypotheses == []
    events = hypothesis_history.load_history(rd)["events"]
    assert events[-1]["type"] == hypothesis_history.TYPE_HYPOTHESIS_REMOVED
    assert events[-1]["hypotheses"] == []


def test_guided_edit_persists_exact_request_before_new_version(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    req = "Make it specific to RGC subtypes and drop the mTOR framing."
    resp = client.post("/api/edit", json={"index": 1, "state_id": "sid-1", "feedback": req})
    assert resp.status_code == 200 and resp.get_json()["success"] is True
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == [
        "user_message", "hypothesis_regenerated", "assistant_message",
    ]
    assert events[0]["content"] == req  # exact, not paraphrased
    assert events[0]["phase"] == "hypothesis_discussion"
    assert events[0]["hypothesis_index"] == 1
    assert events[0]["sequence"] < events[1]["sequence"]
    assert events[2]["content"] == "Created Version 2."


def test_llm_edit_acknowledgement_stays_on_source_version_thread(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    source_result_id = _seed_current_version_history(rd, gen)

    response = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1", "feedback": "Make it more specific.",
    })

    body = response.get_json()
    assert body["created_version_number"] == 2
    assert body["version_created_message"] == "Created Version 2."
    events = hypothesis_history.load_history(rd)["events"]
    assert [event["type"] for event in events] == [
        "hypothesis_set_generated", "user_message",
        "hypothesis_regenerated", "assistant_message",
    ]
    transcript = app_module._v2_events_to_transcript(events)
    messages = [item for item in transcript if item["kind"] == "message"]
    assert [(item["role"], item["content"], item["result_event_id"]) for item in messages] == [
        ("user", "Make it more specific.", source_result_id),
        ("assistant", "Created Version 2.", source_result_id),
    ]
    assert transcript[2]["result_event_id"] == body["result_event_id"]


def test_guided_regenerate_persists_exact_request(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    req = "Try a metabolic mechanism instead."
    client.post("/api/regenerate", json={"index": 1, "state_id": "sid-1", "feedback": req})
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == ["user_message", "hypothesis_regenerated"]
    assert events[0]["content"] == req


def test_regenerate_guidance_persists_guidance_text(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    guidance = "For the next run, weight single-cell evidence more heavily."
    client.post("/api/regenerate_guidance",
                json={"index": 1, "state_id": "sid-1", "guidance": guidance})
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == ["user_message", "hypothesis_regenerated"]
    assert events[0]["content"] == guidance
    assert events[0]["sequence"] < events[1]["sequence"]


def test_regenerate_guidance_preserves_both_feedback_and_guidance(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    client.post("/api/regenerate_guidance", json={
        "index": 1, "state_id": "sid-1",
        "feedback": "Focus on inflammation.", "guidance": "Prefer testable in zebrafish.",
    })
    events = hypothesis_history.load_history(rd)["events"]
    assert events[0]["type"] == "user_message"
    # Both scientist-authored fields preserved verbatim, neither dropped.
    assert "Focus on inflammation." in events[0]["content"]
    assert "Prefer testable in zebrafish." in events[0]["content"]


def test_plain_regenerate_creates_no_fake_user_message(monkeypatch, tmp_path):
    """A button-only regenerate (no feedback, no guidance, no manual draft) uses
    only the system default and must NOT fabricate a scientist message."""
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    resp = client.post("/api/regenerate_guidance", json={"index": 1, "state_id": "sid-1"})
    assert resp.status_code == 200 and resp.get_json()["success"] is True
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == ["hypothesis_regenerated"]  # no user_message


def test_regenerate_guidance_uses_request_hypothesis_to_recover_missing_slot(monkeypatch, tmp_path):
    """The V2 client can still display cards when a restored generator has lost
    the active hypothesis list. A seed-only request_hypothesis should recover the
    slot without treating the draft as a manual replacement."""
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    gen.hypotheses = []
    gen.hypothesis_state_ids = []

    resp = client.post("/api/regenerate_guidance", json={
        "index": 1,
        "request_hypothesis": HYP1,
    })

    body = resp.get_json()
    assert resp.status_code == 200 and body["success"] is True
    assert gen.regen_calls and gen.regen_calls[0]["idx"] == 1
    assert "Use the user-provided draft" not in gen.regen_calls[0]["feedback"]
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == ["hypothesis_regenerated"]


def test_manual_direct_edit_creates_no_user_message(monkeypatch, tmp_path):
    """Literal manual text editing is not an agent request: only the revision
    event is recorded, with no fabricated scientist message."""
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    new_text = "**Title**: Manual\n**Hypothesis**: Hand-written by the scientist."
    resp = client.post("/api/edit", json={"index": 1, "state_id": "sid-1", "manual_hypothesis": new_text})
    body = resp.get_json()
    assert resp.status_code == 200 and body["success"] is True
    assert "result_event_id" not in body
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == ["hypothesis_revised"]
    assert "Hand-written" in events[0]["after"]["full_text"]


def test_llm_manual_and_regenerate_share_one_card_version_history(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    version_one_id = _seed_current_version_history(rd, gen)

    llm_one = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1", "feedback": "Make this more mechanistic.",
    }).get_json()
    assert llm_one["success"] is True
    version_two_id = llm_one["result_event_id"]
    history = _version_history(gen)
    assert [item["result_event_id"] for item in history["results"]] == [
        version_one_id, version_two_id,
    ]
    assert history["active_position"] == 1
    assert [item["snapshot"]["index"] for item in history["results"]] == [1, 1]

    # No embedded title in the draft (matches the real client); the version's
    # existing title ("Axon growth", inherited from V1 since the fake LLM edit
    # above doesn't change it) must survive into the saved full_text.
    manual = client.post("/api/edit", json={
        "index": 1,
        "state_id": llm_one["hypothesis"]["state_id"],
        "manual_hypothesis": "**Hypothesis**: Exact manually polished Version 2.",
    }).get_json()
    assert manual["success"] is True
    assert manual["hypothesis"]["title"] == "Axon growth"
    manual_text = manual["hypothesis"]["full_text"]
    history = _version_history(gen)
    assert len(history["results"]) == 2
    assert history["active_position"] == 1
    assert history["results"][1]["result_event_id"] == version_two_id
    assert history["results"][1]["snapshot"]["full_text"] == manual_text

    llm_two = client.post("/api/edit", json={
        "index": 1,
        "state_id": manual["hypothesis"]["state_id"],
        "feedback": "Make the causal test more specific.",
    }).get_json()
    assert llm_two["success"] is True
    history = _version_history(gen)
    assert len(history["results"]) == 3
    assert history["active_position"] == 2
    assert history["results"][0]["snapshot"]["full_text"] == HYP1
    assert history["results"][1]["snapshot"]["full_text"] == manual_text
    assert [item["snapshot"]["index"] for item in history["results"]] == [1, 1, 1]

    regenerated = client.post("/api/regenerate_guidance", json={
        "index": 1,
        "state_id": llm_two["hypothesis"]["state_id"],
        "request_hypothesis": llm_two["hypothesis"]["full_text"],
    }).get_json()
    assert regenerated["success"] is True
    history = _version_history(gen)
    assert len(history["results"]) == 4
    assert history["active_position"] == 3
    assert len({item["result_event_id"] for item in history["results"]}) == 4
    assert all(item["snapshot"]["index"] == 1 for item in history["results"])


def test_select_after_llm_edit_uses_exact_displayed_version(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    _seed_current_version_history(rd, gen)
    _allow_test_selection(monkeypatch)

    llm = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1", "feedback": "Make this more mechanistic.",
    }).get_json()
    displayed = llm["hypothesis"]["full_text"]
    response = client.post("/api/select", json={
        "selection": 1,
        "selection_state_id": llm["hypothesis"]["state_id"],
        "final_text": displayed,
        "regeneration_result_id": llm["result_event_id"],
    })

    assert response.get_json()["success"] is True
    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == displayed
    selected = hypothesis_history.load_history(rd)["events"][-1]["hypothesis"]
    assert selected["full_text"] == displayed
    assert selected["source_result_event_id"] == llm["result_event_id"]


def test_select_after_manual_edit_uses_exact_in_place_version(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    _seed_current_version_history(rd, gen)
    _allow_test_selection(monkeypatch)

    llm = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1", "feedback": "Make this more mechanistic.",
    }).get_json()
    # Real clients never send a title in the manual draft; the version's title
    # (inherited from V1's "Axon growth" here, since the fake LLM edit above
    # doesn't change it) is preserved and folded back into the saved full_text.
    manual = client.post("/api/edit", json={
        "index": 1,
        "state_id": llm["hypothesis"]["state_id"],
        "manual_hypothesis": "**Hypothesis**: Scientist-corrected Version 2.",
    }).get_json()
    assert manual["hypothesis"]["title"] == "Axon growth"
    manual_text = manual["hypothesis"]["full_text"]
    history = _version_history(gen)
    assert len(history["results"]) == 2

    response = client.post("/api/select", json={
        "selection": 1,
        "selection_state_id": manual["hypothesis"]["state_id"],
        "final_text": manual_text,
        "regeneration_result_id": llm["result_event_id"],
    })

    assert response.get_json()["success"] is True
    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == manual_text
    selected = hypothesis_history.load_history(rd)["events"][-1]["hypothesis"]
    assert selected["full_text"] == manual_text
    assert selected["source_result_event_id"] == llm["result_event_id"]


def test_edit_regeneration_version_persists_exact_edit_with_fresh_state(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    # Double-newline section spacing: manual-edit save reconstructs sections
    # (to fold the preserved title back in) using this canonical join, so an
    # identity re-save round-trips byte-exact only when the fixture already
    # matches that canonical spacing.
    historical = "**Title**: Earlier\n\n**Hypothesis**: Use the earlier exact result.\n\n**Justification**: Exact rationale."
    result_id, historical_thread, current_thread = _seed_slot_regeneration_history(
        rd, gen, historical
    )

    resp = client.post("/api/edit", json={
        "index": 1,
        "state_id": "sid-1",
        "manual_hypothesis": historical,
        "action": "edit_regeneration_version",
        "regeneration_position": 99,  # ignored in favor of the durable result id
        "regeneration_result_id": result_id,
    })

    body = resp.get_json()
    assert resp.status_code == 200 and body["success"] is True
    assert gen.hypotheses[0] == historical
    assert body["hypothesis"]["full_text"] == historical
    assert body["hypothesis"]["state_id"] != "sid-1"
    events = hypothesis_history.load_history(rd)["events"]
    revision = events[-1]
    assert revision["type"] == "hypothesis_revised"
    assert revision["action"] == "edit_regeneration_version"
    assert revision["regeneration_position"] == 0
    assert revision["source_result_event_id"] == result_id
    assert revision["after"]["full_text"] == historical
    assert revision["after"]["state_id"] == body["hypothesis"]["state_id"]
    assert revision["after"]["hypothesis_thread_id"] == historical_thread
    assert gen.hypothesis_thread_ids == [historical_thread]
    assert gen.hypothesis_thread_ids != [current_thread]


def test_agent_edit_of_regeneration_version_uses_historical_text_as_draft(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    historical = "**Title**: Earlier\n**Hypothesis**: Historical mechanism.\n**Justification**: Earlier rationale."
    result_id, historical_thread, current_thread = _seed_slot_regeneration_history(
        rd, gen, historical
    )

    resp = client.post("/api/edit", json={
        "index": 1,
        "state_id": "sid-1",
        "manual_hypothesis": historical,
        "feedback": "Make this more testable.",
        "action": "edit_regeneration_version",
        "regeneration_position": 0,
        "regeneration_result_id": result_id,
    })

    assert resp.get_json()["success"] is True
    assert len(gen.edit_calls) == 1
    prompt = gen.edit_calls[0]["feedback"]
    assert historical in prompt
    assert "Make this more testable." in prompt
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events][-3:] == [
        "user_message", "hypothesis_regenerated", "assistant_message",
    ]
    assert events[-2]["source_result_event_id"] == result_id
    assert events[-3]["thread_id"] == historical_thread
    assert events[-1]["thread_id"] == historical_thread
    assert events[-2]["before"]["hypothesis_thread_id"] == historical_thread
    assert events[-2]["after"]["hypothesis_thread_id"] != historical_thread
    assert gen.hypothesis_thread_ids == [events[-2]["after"]["hypothesis_thread_id"]]
    assert gen.hypothesis_thread_ids != [current_thread]
    assert gen.used_thread_ids[-1] == historical_thread


def test_legacy_threadless_result_edit_starts_fresh_isolated_thread(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    current_thread = "thr_" + "c" * 32
    gen.hypothesis_thread_ids = [current_thread]
    history = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{
            "index": 1, "state_id": "old", "full_text": HYP2,
        }]),
        hypothesis_history.hypothesis_regenerated_event(
            1, {"index": 1, "state_id": "old", "full_text": HYP2},
            {
                "index": 1, "state_id": "sid-1", "full_text": HYP1,
                "hypothesis_thread_id": current_thread,
            },
        ),
    ])
    result_id = history["events"][0]["id"]

    body = client.post("/api/edit", json={
        "index": 1,
        "state_id": "sid-1",
        "feedback": "Make it testable.",
        "action": "edit_regeneration_version",
        "regeneration_position": 0,
        "regeneration_result_id": result_id,
    }).get_json()

    assert body["success"] is True
    assert body["historical_thread_recovered"] is False
    assert gen.hypothesis_thread_ids[0] != current_thread
    assert gen.hypothesis_thread_ids[0].startswith("thr_")
    # The LLM call uses a fresh isolated source thread for the legacy result;
    # the resulting new version then receives its own thread identity.
    assert gen.used_thread_ids[-1] != gen.hypothesis_thread_ids[0]
    assert hypothesis_history.load_history(rd)["events"][-2]["after"]["hypothesis_thread_id"] == gen.hypothesis_thread_ids[0]


def test_historical_activation_rejects_wrong_slot_and_prior_set_boundary(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP1, HYP2]
    gen.hypothesis_state_ids = ["sid-1", "sid-2"]
    gen.hypothesis_thread_ids = ["thr_" + "1" * 32, "thr_" + "2" * 32]
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda g=None: "Objective")
    first = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": "thr_" + "a" * 32},
            {"index": 2, "full_text": HYP2, "hypothesis_thread_id": "thr_" + "b" * 32},
        ])
    ])["events"][0]["id"]
    latest_history = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": gen.hypothesis_thread_ids[0]},
            {"index": 2, "full_text": HYP2, "hypothesis_thread_id": gen.hypothesis_thread_ids[1]},
        ]),
        hypothesis_history.hypothesis_regenerated_event(
            1,
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": gen.hypothesis_thread_ids[0]},
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": "thr_" + "d" * 32},
        ),
    ])
    slot_one_result = latest_history["events"][-1]["id"]

    prior = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1", "manual_hypothesis": HYP1,
        "action": "edit_regeneration_version", "regeneration_position": 0,
        "regeneration_result_id": first,
    }).get_json()
    wrong_slot = client.post("/api/edit", json={
        "index": 2, "state_id": "sid-2", "manual_hypothesis": HYP2,
        "action": "edit_regeneration_version", "regeneration_position": 0,
        "regeneration_result_id": slot_one_result,
    }).get_json()

    assert prior["success"] is False
    assert wrong_slot["success"] is False
    assert gen.hypotheses == [HYP1, HYP2]


def test_candidate_removal_preserves_surviving_result_thread_history(tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP2]
    gen.hypothesis_state_ids = ["survivor-state"]
    gen.hypothesis_thread_ids = ["thr_" + "b" * 32]
    original = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": "thr_" + "a" * 32},
            {"index": 2, "full_text": HYP2, "hypothesis_thread_id": "thr_" + "b" * 32},
        ])
    ])["events"][0]["id"]
    regenerated = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_regenerated_event(
            2,
            {"index": 2, "full_text": HYP2, "hypothesis_thread_id": "thr_" + "b" * 32},
            {"index": 2, "full_text": HYP2 + " regenerated",
             "hypothesis_thread_id": "thr_" + "c" * 32},
        )
    ])["events"][-1]["id"]
    gen.hypothesis_thread_ids = ["thr_" + "c" * 32]
    removal = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_removed_event(
            1,
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": "thr_" + "a" * 32},
            [{"index": 1, "state_id": "survivor-state", "full_text": HYP2,
              "hypothesis_thread_id": gen.hypothesis_thread_ids[0]}],
        )
    ])["events"][-1]["id"]

    with app_module.app.test_request_context("/"):
        first_version = app_module._studio_resolve_regeneration_result(gen, 1, original)
        current_version = app_module._studio_resolve_regeneration_result(gen, 1, regenerated)
        assert app_module._studio_resolve_regeneration_result(gen, 1, removal) is None
    assert first_version["hypothesis_thread_id"] == "thr_" + "b" * 32
    assert current_version["hypothesis_thread_id"] == gen.hypothesis_thread_ids[0]


def test_historical_activation_history_failure_rolls_back_content_state_and_thread(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    result_id, _, current_thread = _seed_slot_regeneration_history(rd, gen, HYP2)
    before_events = hypothesis_history.load_history(rd)["events"]

    def fail_append(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(hypothesis_history, "append_events", fail_append)
    body = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1", "manual_hypothesis": HYP2,
        "action": "edit_regeneration_version", "regeneration_position": 0,
        "regeneration_result_id": result_id,
    }).get_json()

    assert body["success"] is False
    assert gen.hypotheses == [HYP1]
    assert gen.hypothesis_state_ids == ["sid-1"]
    assert gen.hypothesis_thread_ids == [current_thread]
    # The failed strict append created no activation success event.
    assert hypothesis_history.load_history(rd)["events"] == before_events


def test_selecting_historical_regeneration_finalizes_exact_text(monkeypatch, tmp_path):
    rd = _round_dir(tmp_path)
    gen = FakeGenerator(rd)
    gen.hypotheses = [HYP2]
    gen.hypothesis_state_ids = ["current-state"]
    historical_thread = "thr_" + "a" * 32
    current_thread = "thr_" + "c" * 32
    gen.hypothesis_thread_ids = [current_thread]
    history = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{
            "index": 1, "state_id": "old-state", "full_text": HYP1,
            "hypothesis_thread_id": historical_thread,
        }]),
        hypothesis_history.hypothesis_regenerated_event(
            1,
            {"index": 1, "state_id": "old-state", "full_text": HYP1,
             "hypothesis_thread_id": historical_thread},
            {"index": 1, "state_id": "current-state", "full_text": HYP2,
             "hypothesis_thread_id": current_thread},
        ),
    ])
    historical_result_id = history["events"][0]["id"]
    client = _wire(monkeypatch, gen)
    monkeypatch.setattr(app_module, "_get_project_round_from_disk", lambda: 0)
    monkeypatch.setattr(app_module, "CLI_DRY_RUN", False)

    resp = client.post("/api/select", json={
        "selection": 1,
        "selection_state_id": "old-state",
        "final_text": "**Hypothesis**: Browser-forged text must be ignored.",
        "regeneration_result_id": historical_result_id,
    })

    assert resp.get_json()["success"] is True
    assert rd.joinpath("selected_hypothesis.md").read_text(encoding="utf-8") == HYP1
    selected = hypothesis_history.load_history(rd)["events"][-1]
    assert selected["type"] == "hypothesis_selected"
    assert selected["hypothesis"]["full_text"] == HYP1
    assert selected["hypothesis"]["source_result_event_id"] == historical_result_id


def test_edit_regeneration_version_rejects_stale_current_state_without_mutation(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    before = gen.hypotheses[0]
    result_id, _, current_thread = _seed_slot_regeneration_history(rd, gen, HYP2)

    resp = client.post("/api/edit", json={
        "index": 1,
        "state_id": "historical-stale-state-id",
        "manual_hypothesis": "**Hypothesis**: Earlier result.",
        "action": "edit_regeneration_version",
        "regeneration_position": 0,
        "regeneration_result_id": result_id,
    })

    body = resp.get_json()
    assert body["success"] is False and body.get("stale_selection") is True
    assert gen.hypotheses[0] == before
    assert gen.hypothesis_state_ids == ["sid-1"]
    assert gen.hypothesis_thread_ids == [current_thread]
    assert len(hypothesis_history.load_history(rd)["events"]) == 2


def test_invalid_edit_regeneration_version_creates_no_event(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    before = gen.hypotheses[0]

    resp = client.post("/api/edit", json={
        "index": 1,
        "state_id": "sid-1",
        "manual_hypothesis": "**Hypothesis**: Earlier result.",
        "action": "edit_regeneration_version",
        "regeneration_position": -1,
    })

    assert resp.get_json()["success"] is False
    assert gen.hypotheses[0] == before
    assert hypothesis_history.load_history(rd)["events"] == []


def test_failed_mutation_records_neither_request_nor_revision(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    gen.edit_returns_none = True  # scientific op yields no hypothesis
    resp = client.post("/api/edit", json={"index": 1, "state_id": "sid-1", "feedback": "please revise"})
    assert resp.get_json()["success"] is False
    assert hypothesis_history.load_history(rd)["events"] == []


def test_stale_mutation_records_neither_request_nor_revision(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    resp = client.post("/api/edit", json={"index": 1, "state_id": "WRONG", "feedback": "please revise"})
    assert resp.get_json().get("stale_selection") is True
    assert hypothesis_history.load_history(rd)["events"] == []


def test_repeated_llm_edits_preserve_chronology_as_versions(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    # First guided revision.
    r1 = client.post("/api/edit", json={"index": 1, "state_id": "sid-1", "feedback": "First change"})
    sid2 = r1.get_json()["hypothesis"]["state_id"]
    # Second guided revision on the now-current version.
    client.post("/api/edit", json={"index": 1, "state_id": sid2, "feedback": "Second change"})
    events = hypothesis_history.load_history(rd)["events"]
    assert [e["type"] for e in events] == [
        "user_message", "hypothesis_regenerated", "assistant_message",
        "user_message", "hypothesis_regenerated", "assistant_message",
    ]
    assert events[0]["content"] == "First change"
    assert events[2]["content"] == "Created Version 2."
    assert events[3]["content"] == "Second change"
    assert events[5]["content"] == "Created Version 3."
    assert [e["sequence"] for e in events] == [1, 2, 3, 4, 5, 6]


def test_selection_follows_newest_revision(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    monkeypatch.setattr(app_module, "_get_project_round_from_disk", lambda: 0)
    monkeypatch.setattr(app_module, "CLI_DRY_RUN", False)

    client.post("/api/edit", json={"index": 1, "state_id": "sid-1", "feedback": "Refine it"})
    client.post("/api/select", json={"selection": "custom", "custom_source": "direct_custom",
                                     "final_text": "**Hypothesis**: Final.\n\n**Justification**: J"})
    types = [e["type"] for e in hypothesis_history.load_history(rd)["events"]]
    assert types == [
        "user_message", "hypothesis_regenerated", "assistant_message",
        "hypothesis_selected",
    ]


def test_history_api_preserves_request_then_revision_order(monkeypatch, tmp_path):
    # Drive a guided edit against a real project so the read endpoint can see it.
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd = root / "round_0"
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{"index": 1, "full_text": HYP1}]),
        {**hypothesis_history.user_message_event("Tighten it", "hypothesis_discussion"), "hypothesis_index": 1},
        hypothesis_history.hypothesis_revised_event(1, {"full_text": HYP1}, {"full_text": HYP1 + " (revised)"}),
    ])
    data = client.get("/api/hypothesis_history/0").get_json()
    kinds = [e["kind"] for e in data["transcript"]]
    assert kinds == ["hypotheses", "message", "hypothesis_revised"]
    msg = data["transcript"][1]
    assert msg["role"] == "user" and msg["content"] == "Tighten it"
    # events array preserves the same order with full schema.
    assert [e["type"] for e in data["events"]] == [
        "hypothesis_set_generated", "user_message", "hypothesis_revised"]


def test_history_api_preserves_edit_regeneration_version_metadata(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    use_event = hypothesis_history.hypothesis_revised_event(
        1,
        {"index": 1, "state_id": "current", "full_text": HYP2},
        {"index": 1, "state_id": "fresh", "full_text": HYP1},
    )
    use_event["action"] = "edit_regeneration_version"
    use_event["regeneration_position"] = 0
    hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{"index": 1, "full_text": HYP1}]),
        hypothesis_history.hypothesis_regenerated_event(
            1, {"index": 1, "full_text": HYP1}, {"index": 1, "full_text": HYP2}),
        use_event,
    ])

    data = client.get("/api/hypothesis_history/0").get_json()
    projected = data["transcript"][-1]
    assert projected["kind"] == "hypothesis_revised"
    assert projected["action"] == "edit_regeneration_version"
    assert projected["regeneration_position"] == 0
    assert projected["after"]["full_text"] == HYP1
    assert [e["type"] for e in data["events"]] == [
        "hypothesis_set_generated", "hypothesis_regenerated", "hypothesis_revised"]


def test_history_api_exposes_result_association_without_internal_thread_ids(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    thread_a = "thr_" + "a" * 32
    thread_b = "thr_" + "b" * 32
    history = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([{
            "index": 1, "full_text": HYP1, "hypothesis_thread_id": thread_a,
        }]),
        {**hypothesis_history.user_message_event("Question A", "hypothesis_discussion"),
         "hypothesis_index": 1, "thread_id": thread_a},
        hypothesis_history.hypothesis_regenerated_event(
            1,
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": thread_a},
            {"index": 1, "full_text": HYP2, "hypothesis_thread_id": thread_b},
        ),
    ])

    data = client.get("/api/hypothesis_history/0").get_json()
    serialized = json.dumps(data)
    assert thread_a not in serialized and thread_b not in serialized
    assert "hypothesis_thread_id" not in serialized and '"thread_id"' not in serialized
    set_id = history["events"][0]["id"]
    regen_id = history["events"][2]["id"]
    assert data["transcript"][0]["result_event_id"] == set_id
    assert data["transcript"][0]["hypotheses"][0]["thread_recoverable"] is True
    assert data["transcript"][1]["result_event_id"] == set_id
    assert data["transcript"][2]["result_event_id"] == regen_id
    assert data["transcript"][2]["after"]["thread_recoverable"] is True


def test_history_api_recovers_legacy_version_thread_from_regeneration_before(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    thread_a = "thr_" + "a" * 32
    thread_b = "thr_" + "b" * 32
    history = hypothesis_history.append_events(rd, [
        # Legacy generated-set snapshots did not retain a thread id.
        hypothesis_history.hypothesis_set_generated_event([{
            "index": 1, "full_text": HYP1,
        }]),
        {**hypothesis_history.user_message_event("Question A", "hypothesis_discussion"),
         "hypothesis_index": 1, "thread_id": thread_a},
        hypothesis_history.hypothesis_revised_event(
            1,
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": thread_a},
            {"index": 1, "full_text": HYP1 + " edited", "hypothesis_thread_id": thread_a},
        ),
        hypothesis_history.hypothesis_regenerated_event(
            1,
            {"index": 1, "full_text": HYP1 + " edited", "hypothesis_thread_id": thread_a},
            {"index": 1, "full_text": HYP2, "hypothesis_thread_id": thread_b},
        ),
    ])

    data = client.get("/api/hypothesis_history/0").get_json()
    set_id = history["events"][0]["id"]
    assert data["transcript"][0]["hypotheses"][0]["thread_recoverable"] is False
    assert data["transcript"][1]["result_event_id"] == set_id
    assert data["transcript"][2]["result_event_id"] == set_id
    assert data["transcript"][3]["before"]["thread_recoverable"] is True

    generator = FakeGenerator(rd)
    monkeypatch.setattr(app_module, "_studio_history_round_dir", lambda _generator: rd)
    resolved = app_module._studio_resolve_regeneration_result(generator, 1, set_id)
    assert resolved["hypothesis_thread_id"] == thread_a
    assert resolved["thread_recoverable"] is True


def test_history_api_keeps_every_slot_version_transcript_independent(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    rd = _project_round_root(tmp_path) / "round_0"
    threads = {
        key: "thr_" + key * 32
        for key in ("a", "b", "c", "d", "e")
    }

    def message(text, index, thread_key):
        return {
            **hypothesis_history.user_message_event(text, "hypothesis_discussion"),
            "hypothesis_index": index,
            "thread_id": threads[thread_key],
        }

    history = hypothesis_history.append_events(rd, [
        hypothesis_history.hypothesis_set_generated_event([
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": threads["a"]},
            {"index": 2, "full_text": HYP2, "hypothesis_thread_id": threads["b"]},
        ]),
        message("A version 1", 1, "a"),
        message("B version 1", 2, "b"),
        hypothesis_history.hypothesis_regenerated_event(
            1,
            {"index": 1, "full_text": HYP1, "hypothesis_thread_id": threads["a"]},
            {"index": 1, "full_text": HYP1 + " A2", "hypothesis_thread_id": threads["c"]},
        ),
        message("A version 2", 1, "c"),
        hypothesis_history.hypothesis_regenerated_event(
            2,
            {"index": 2, "full_text": HYP2, "hypothesis_thread_id": threads["b"]},
            {"index": 2, "full_text": HYP2 + " B2", "hypothesis_thread_id": threads["d"]},
        ),
        message("B version 2", 2, "d"),
        hypothesis_history.hypothesis_regenerated_event(
            1,
            {"index": 1, "full_text": HYP1 + " A2", "hypothesis_thread_id": threads["c"]},
            {"index": 1, "full_text": HYP1 + " A3", "hypothesis_thread_id": threads["e"]},
        ),
        message("A version 3", 1, "e"),
    ])

    data = client.get("/api/hypothesis_history/0").get_json()
    transcript_messages = [item for item in data["transcript"] if item["kind"] == "message"]
    ids = [event["id"] for event in history["events"]]
    assert [(item["hypothesis_index"], item["content"], item["result_event_id"])
            for item in transcript_messages] == [
        (1, "A version 1", ids[0]),
        (2, "B version 1", ids[0]),
        (1, "A version 2", ids[3]),
        (2, "B version 2", ids[5]),
        (1, "A version 3", ids[7]),
    ]


def test_history_persistence_makes_no_model_call_and_op_unchanged(monkeypatch, tmp_path):
    """Recording provenance must not introduce an LLM call, and the scientific
    edit must behave exactly as before (single edit_hypothesis call, revised
    hypothesis returned)."""
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    resp = client.post("/api/edit", json={"index": 1, "state_id": "sid-1", "feedback": "Tighten it"})
    body = resp.get_json()
    assert body["success"] is True
    assert "(revised)" in body["hypothesis"]["full_text"]  # scientific result unchanged
    assert len(gen.edit_calls) == 1 and gen.edit_calls[0]["idx"] == 1  # exactly one op call
    # History was still written.
    assert [e["type"] for e in hypothesis_history.load_history(rd)["events"]] == [
        "user_message", "hypothesis_regenerated", "assistant_message"]


# ============================================================================
# Product-facing round labeling (storage index -> target autonomous round)
# ============================================================================

def test_target_round_number_is_storage_plus_one():
    # Proven +1: a studio cycle in round_K directs autonomous round K+1.
    assert app_module._v2_target_round_number(0) == 1
    assert app_module._v2_target_round_number(1) == 2
    assert app_module._v2_target_round_number(5) == 6


def test_first_cycle_in_round0_is_labelled_round_1(monkeypatch, tmp_path):
    """Brand-new project: the first studio cycle is stored in round_0 but is,
    in product terms, the conversation preparing Round 1."""
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    (root / "round_0" / "round_report.md").write_text(
        "# Round 0 complete", encoding="utf-8"
    )
    (root / "round_0" / "generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP1]}))

    listing = client.get("/api/hypothesis_history").get_json()
    entry = next(r for r in listing["rounds"] if r["storage_round_index"] == 0)
    assert entry["target_round_number"] == 1
    assert entry["label"] == "Round 1"
    assert entry["round_index"] == 0  # storage addressing key preserved
    assert listing["current_target_round_number"] == 1

    detail = client.get("/api/hypothesis_history/0").get_json()
    assert detail["storage_round_index"] == 0
    assert detail["target_round_number"] == 1
    assert detail["label"] == "Round 1"
    # Detail is still addressed by the storage index (backward compatible).
    assert detail["round_index"] == 0


def test_round1_set_is_the_only_entry_after_exploration_and_generation(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd0 = root / "round_0"
    rd0.joinpath("round_report.md").write_text("# Round 0\n\nExploratory findings.")
    rd0.joinpath("generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP1]}))

    listing = client.get("/api/hypothesis_history").get_json()

    assert [(entry["label"], entry["is_current"]) for entry in listing["rounds"]] == [
        ("Round 1", True),
    ]
    assert listing["current_target_round_number"] == 1


def test_running_round1_remains_current_locked_and_never_creates_round2(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd0 = root / "round_0"
    rd0.joinpath("round_report.md").write_text("# Round 0\n\nExploratory findings.")
    rd0.joinpath("generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP1]}))
    rd0.joinpath("selected_hypothesis.md").write_text(HYP1)
    (root / "round_1" / "query_1").mkdir(parents=True)

    listing = client.get("/api/hypothesis_history").get_json()
    assert [entry["label"] for entry in listing["rounds"]] == ["Round 1"]
    current = listing["rounds"][0]
    assert current["is_current"] is True
    assert current["status"] == "running"
    assert current["has_selected"] is True

    round1 = client.get("/api/hypothesis_history/0").get_json()
    assert round1["label"] == "Round 1"
    assert round1["is_current"] is True
    assert round1["read_only"] is True
    assert round1["status"] == "running"
    assert round1["selected_hypothesis"]["full_text"] == HYP1

    # round_1 is the execution directory for Round 1, not a Studio set for
    # Round 2. In particular, Round 1's selection is never borrowed into it.
    assert client.get("/api/hypothesis_history/1").status_code == 404


def test_round2_appears_only_after_its_set_is_generated(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd0 = root / "round_0"
    rd0.joinpath("round_report.md").write_text("# Round 0\n")
    rd0.joinpath("generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP1]}))
    rd0.joinpath("selected_hypothesis.md").write_text(HYP1)

    rd1 = root / "round_1"
    rd1.mkdir(parents=True)
    rd1.joinpath("round_report.md").write_text("# Round 1\n\nInvestigation complete.")

    before_generation = client.get("/api/hypothesis_history").get_json()
    assert [entry["label"] for entry in before_generation["rounds"]] == ["Round 1"]
    assert client.get("/api/hypothesis_history/1").status_code == 404

    rd1.joinpath("generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP2]}))

    after_generation = client.get("/api/hypothesis_history").get_json()
    assert [entry["label"] for entry in after_generation["rounds"]] == ["Round 1", "Round 2"]
    assert after_generation["current_target_round_number"] == 2
    assert [entry["label"] for entry in after_generation["rounds"] if entry["is_current"]] == ["Round 2"]

    historical_round1 = client.get("/api/hypothesis_history/0").get_json()
    assert historical_round1["read_only"] is True
    assert historical_round1["status"] == "completed"

    round2 = client.get("/api/hypothesis_history/1").get_json()
    assert round2["label"] == "Round 2"
    assert round2["read_only"] is False
    assert round2["status"] == "exploring"
    assert round2["generated_hypotheses"][0]["full_text"] == HYP2
    assert round2["selected_hypothesis"] is None


def test_current_sidebar_uses_only_current_status_copy():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    css = (Path(app_module.app.static_folder) / "hypothesis_studio.css").read_text(encoding="utf-8")
    assert "Current · Being investigated" not in js
    assert "hs-round-item-note', 'Current'" in js
    assert "hs-round-unread-dot" not in js
    assert ".hs-round-unread-dot" not in css


def test_expanded_selected_card_sticky_controls_keep_selected_background():
    css = (Path(app_module.app.static_folder) / "hypothesis_studio.css").read_text(encoding="utf-8")
    selector = ".hs-hypothesis-surface.has-expanded-card .hs-card.is-expanded.is-selected .hs-card-controls {"
    rule = css[css.index(selector):css.index("}", css.index(selector))]
    assert "background: var(--v2-accent-soft);" in rule


def test_labels_never_expose_checkpoint_dir_numbers(monkeypatch, tmp_path):
    """No sidebar label is 'Round 0' — checkpoint dir indices are never product
    labels; the first cycle is Round 1 and later cycles increment from there."""
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    (root / "round_0" / "round_report.md").write_text(
        "# Round 0 complete", encoding="utf-8"
    )
    for i in (0, 1, 2):
        (root / f"round_{i}").mkdir(parents=True, exist_ok=True)
        (root / f"round_{i}" / "generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP1]}))
        (root / f"round_{i}" / "selected_hypothesis.md").write_text("**Hypothesis**: choice.")

    listing = client.get("/api/hypothesis_history").get_json()
    labels = {r["storage_round_index"]: r["label"] for r in listing["rounds"]}
    assert labels[0] == "Round 1" and labels[1] == "Round 2" and labels[2] == "Round 3"
    assert "Round 0" not in labels.values()
    # Ordered by target autonomous round number (ascending).
    targets = [r["target_round_number"] for r in listing["rounds"]]
    assert targets == sorted(targets)


def test_brand_new_project_has_no_studio_cycles(monkeypatch, tmp_path):
    """Before any autonomous round has started there are NO Studio cycles yet
    (Round 0 is autonomous, not a conversation): the sidebar is empty and the
    list endpoint signals initial_exploration so the Studio shows the callout."""
    client = _project_client(monkeypatch, tmp_path)
    listing = client.get("/api/hypothesis_history").get_json()
    assert listing["rounds"] == []
    assert listing["initial_exploration"] is True
    assert listing["current_round_index"] is None
    assert listing["current_target_round_number"] is None


def test_hypothesis_history_exposes_activity_signature_for_unseen_markers(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd0 = root / "round_0"
    rd0.joinpath("round_report.md").write_text("# Round Report\n\nExploratory findings.", encoding="utf-8")
    rd0.joinpath("memory_report.md").write_text("# Memory\n\nAccumulated understanding.", encoding="utf-8")
    hypothesis_history.append_events(rd0, [
        hypothesis_history.hypothesis_set_generated_event([{"index": 1, "full_text": HYP1, "hypothesis": "mTOR"}]),
    ])

    listing = client.get("/api/hypothesis_history").get_json()
    current = next(r for r in listing["rounds"] if r["storage_round_index"] == 0)
    assert current["activity_signature"].startswith("history:1:")

    detail = client.get("/api/hypothesis_history/0").get_json()
    assert detail["activity_signature"] == current["activity_signature"]


def test_user_only_history_does_not_create_activity_signature(tmp_path):
    rd = _round_dir(tmp_path)
    hypothesis_history.append_events(rd, [hypothesis_history.user_message_event("I am typing", hypothesis_history.PHASE_HYPOTHESIS_DISCUSSION)])
    assert app_module._v2_round_activity_signature(rd) is None


def test_studio_unread_markers_ignore_history_rollbacks_and_rerender_after_seen():
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")

    assert "function historyActivityMarker(signature)" in js
    assert "if (current.sequence !== seen.sequence) return current.sequence > seen.sequence" in js
    assert "return current.timestamp > seen.timestamp" in js
    assert "isNewerActivitySignature(signature, seenActivitySignature(idx))" in js
    assert "if (changed && els.roundList && state.rounds.length) renderSidebar();" in js


def test_round0_completion_alone_does_not_create_round1_studio_entry(monkeypatch, tmp_path):
    """A completed execution is not itself a durable candidate set."""
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    rd0 = root / "round_0"
    # Round 0's autonomous outputs -> past the initial-exploration gate.
    rd0.joinpath("round_report.md").write_text("# Round Report\n\nExploratory findings.")
    rd0.joinpath("memory_report.md").write_text("# Memory\n\nAccumulated understanding.")

    listing = client.get("/api/hypothesis_history").get_json()
    assert listing["initial_exploration"] is False
    assert listing["rounds"] == []
    assert listing["current_round_index"] is None
    assert listing["current_target_round_number"] is None


def test_latest_durable_set_remains_current_after_its_round_completes(monkeypatch, tmp_path):
    """Completion does not fabricate the next Studio round before generation."""
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    for i in range(2):
        rd = root / f"round_{i}"
        rd.mkdir(parents=True, exist_ok=True)
        rd.joinpath("round_report.md").write_text(f"# Round {i} Report\n\nFindings.")
        rd.joinpath("memory_report.md").write_text(f"# Round {i} Memory\n\nUnderstanding.")
    # round_0 owns the durable set for product Round 1. round_1 has completed,
    # but it does not yet own a generated set for product Round 2.
    (root / "round_0" / "generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP1]}))
    (root / "round_0" / "selected_hypothesis.md").write_text("**Hypothesis**: Round 1 direction.")

    listing = client.get("/api/hypothesis_history").get_json()
    assert listing["initial_exploration"] is False
    assert listing["current_round_index"] == 0
    assert listing["current_target_round_number"] == 1
    current = [r for r in listing["rounds"] if r["is_current"]]
    assert len(current) == 1
    assert current[0]["storage_round_index"] == 0
    assert current[0]["label"] == "Round 1"
    assert current[0]["status"] == "completed"
    assert [r["label"] for r in listing["rounds"]] == ["Round 1"]

    detail = client.get("/api/hypothesis_history/0").get_json()
    assert detail["is_current"] is True
    assert detail["read_only"] is True
    assert "Round 1 direction" in detail["selected_hypothesis"]["hypothesis"]


def test_normal_studio_visit_does_not_restore_a_saved_historical_round():
    """Only an explicit #round-N URL may reopen history after a run completes."""
    js = (Path(app_module.app.static_folder) / "hypothesis_studio.js").read_text(encoding="utf-8")
    function_start = js.index("async function restoreRequestedRoundFromLocation()")
    function_end = js.index("// ---- generation resume", function_start)
    implementation = js[function_start:function_end]

    assert "const requested = requestedRoundFromLocation();" in implementation
    assert "const saved = readStudioView();" not in implementation
    assert "saved.viewRoundIndex" not in implementation


def test_completed_round_does_not_open_empty_future_studio_cycle(monkeypatch, tmp_path):
    client = _project_client(monkeypatch, tmp_path)
    root = _project_round_root(tmp_path)
    for index in range(5):
        round_dir = root / f"round_{index}"
        round_dir.mkdir(parents=True, exist_ok=True)
        round_dir.joinpath("round_report.md").write_text(f"# Round {index} Report\n")
    # Round 4 had a durable set in round_3 and has now completed in round_4.
    root.joinpath("round_3", "generated_hypotheses.json").write_text(json.dumps({"hypotheses": [HYP1]}))
    root.joinpath("round_3", "selected_hypothesis.md").write_text("**Hypothesis**: Round 4 direction.")

    listing = client.get("/api/hypothesis_history").get_json()
    assert listing["current_round_index"] == 3
    assert listing["current_target_round_number"] == 4
    current = next(entry for entry in listing["rounds"] if entry["is_current"])
    assert current["label"] == "Round 4"
    assert current["status"] == "completed"
    assert all(entry["label"] != "Round 5" for entry in listing["rounds"])

    assert client.get("/api/hypothesis_history/4").status_code == 404


def test_current_studio_checkpoint_resolver_ignores_stale_project_round0(tmp_path):
    """A V2 project record may keep checkpoint_dir at round_0 forever; Studio
    must still store the active Round 4 cycle in round_3 after autonomous Round
    3 completes."""
    root = tmp_path / "checkpoint" / "checkpoints"
    for i in range(4):
        rd = root / f"round_{i}"
        rd.mkdir(parents=True, exist_ok=True)
        rd.joinpath("config.json").write_text("{}", encoding="utf-8")
        rd.joinpath("round_report.md").write_text(f"# Round {i} Report", encoding="utf-8")
        rd.joinpath("memory_report.md").write_text(f"# Round {i} Memory", encoding="utf-8")

    resolved = app_module._v2_resolve_current_studio_checkpoint_dir(root / "round_0")
    assert resolved == root / "round_3"


def test_restore_recreates_stale_cached_generator_on_current_studio_round(monkeypatch, tmp_path):
    root = tmp_path / "checkpoint" / "checkpoints"
    for i in range(4):
        rd = root / f"round_{i}"
        rd.mkdir(parents=True, exist_ok=True)
        rd.joinpath("config.json").write_text("{}", encoding="utf-8")
        rd.joinpath("round_report.md").write_text(f"# Round {i} Report", encoding="utf-8")
        rd.joinpath("memory_report.md").write_text(f"# Round {i} Memory", encoding="utf-8")

    created = []

    def fake_create_generator(checkpoint_dir, *args, **kwargs):
        created.append(Path(checkpoint_dir))
        return FakeGenerator(checkpoint_dir)

    monkeypatch.setattr(app_module, "create_generator", fake_create_generator)
    monkeypatch.setattr(app_module, "_restore_generated_hypotheses_if_available", lambda gen: [])
    monkeypatch.setattr(app_module, "_resolve_canonical_task_description", lambda gen: "Objective")

    with app_module.app.test_request_context("/"):
        app_module.session["authenticated"] = True
        app_module.session["session_id"] = "stale-studio-session"
        app_module.session["project_config"] = {
            "checkpoint_dir": str(root / "round_0"),
            "code_dir": str(tmp_path / "code"),
            "readme_path": str(tmp_path / "code" / "README.md"),
            "task_description": "Objective",
        }
        app_module.GENERATORS["stale-studio-session"] = FakeGenerator(root / "round_0")

        restored = app_module.get_or_restore_generator()

    app_module.GENERATORS.pop("stale-studio-session", None)
    assert restored is not None
    assert created == [root / "round_3"]
    assert Path(restored.checkpoint_dir) == root / "round_3"


# ============================================================================
# V1 invariant: manual Edit + Save preserves a version's existing title
# exactly -- it never derives or replaces the title from the edited body.
#
# Regression coverage for the reported bug: manually editing a hypothesis
# whose card title was a full generated sentence (e.g. "ATF3 Acts as a
# Locus-Specific Transcriptional Repressor at Regeneration Genes in Mouse
# RGCs") collapsed the title down to a bare fragment ("ATF3") because the
# manual-only /api/edit branch stored the edited body verbatim -- dropping
# the Title line the real client never sends -- and every downstream reader
# (the API response, the durable history snapshot, and the frontend's
# cardTitle() fallback) re-derived a title from the now title-less content.
#
# Only manual Edit + Save is affected: regeneration and LLM-driven revision
# both still legitimately create a new version and may receive a freshly
# derived/generated title for that new version (see the last two tests).
# ============================================================================

ATF3_REPORTED_TITLE = (
    "ATF3 Acts as a Locus-Specific Transcriptional Repressor at "
    "Regeneration Genes in Mouse RGCs"
)


def test_manual_edit_of_current_v1_preserves_title_exactly(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    gen.hypotheses = [
        f"**Title**: {ATF3_REPORTED_TITLE}\n\n"
        "**Hypothesis**: ATF3 represses regeneration genes at poised enhancers."
    ]
    gen.hypothesis_state_ids = ["sid-1"]
    _seed_current_version_history(rd, gen)

    # The real client's manual-edit textarea never carries a Title line at
    # all (hypothesisBodyMarkdown() strips it client-side); this is the
    # literal shape of what /api/edit receives for a manual save.
    resp = client.post("/api/edit", json={
        "index": 1,
        "state_id": "sid-1",
        "manual_hypothesis": "**Hypothesis**: ATF3 directly represses Vangl2 and Rgs3 in resting RGCs.",
    })
    body = resp.get_json()

    assert resp.status_code == 200 and body["success"] is True
    assert body["hypothesis"]["title"] == ATF3_REPORTED_TITLE
    # Manual edit + save never bumps the version.
    assert "created_version_number" not in body
    assert "result_event_id" not in body

    events = hypothesis_history.load_history(rd)["events"]
    assert events[-1]["type"] == "hypothesis_revised"
    assert events[-1]["after"]["title"] == ATF3_REPORTED_TITLE
    assert events[-1]["after"]["full_text"].startswith(f"**Title**: {ATF3_REPORTED_TITLE}")
    assert gen.hypotheses[0].startswith(f"**Title**: {ATF3_REPORTED_TITLE}")


def test_repeated_manual_edits_preserve_v1_title(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    gen.hypotheses = [
        f"**Title**: {ATF3_REPORTED_TITLE}\n\n**Hypothesis**: Original ATF3 claim."
    ]
    gen.hypothesis_state_ids = ["sid-1"]
    _seed_current_version_history(rd, gen)

    state_id = "sid-1"
    revisions = (
        "**Hypothesis**: ATF3 represses Vangl2 in resting RGCs.",
        "**Hypothesis**: ATF3 represses Vangl2 and Rgs3 in resting RGCs.",
        "**Hypothesis**: ATF3 co-occupies Vangl2 and Rgs3 with repressive marks in resting RGCs.",
    )
    for manual_body in revisions:
        resp = client.post("/api/edit", json={
            "index": 1, "state_id": state_id, "manual_hypothesis": manual_body,
        })
        body = resp.get_json()
        assert resp.status_code == 200 and body["success"] is True
        assert body["hypothesis"]["title"] == ATF3_REPORTED_TITLE
        assert "created_version_number" not in body
        state_id = body["hypothesis"]["state_id"]

    saved_revisions = [
        e for e in hypothesis_history.load_history(rd)["events"]
        if e["type"] == "hypothesis_revised"
    ]
    assert len(saved_revisions) == len(revisions)
    assert all(e["after"]["title"] == ATF3_REPORTED_TITLE for e in saved_revisions)
    assert "co-occupies Vangl2 and Rgs3" in gen.hypotheses[0]
    assert gen.hypotheses[0].startswith(f"**Title**: {ATF3_REPORTED_TITLE}")


def test_manual_edit_without_label_colons_stays_visible_and_preserves_history(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    gen.hypotheses = [
        f"**Title**: {ATF3_REPORTED_TITLE}\n\n"
        "**Hypothesis**: Original ATF3 claim.\n\n"
        "**Justification**: Original evidence."
    ]
    gen.hypothesis_state_ids = ["sid-1"]
    original_result_id = _seed_current_version_history(rd, gen)

    resp = client.post("/api/edit", json={
        "index": 1,
        "state_id": "sid-1",
        "manual_hypothesis": (
            "**Hypothesis** ATF3 directly represses Vangl2 and Rgs3.\n\n"
            "**Justification** The edited evidence remains visible."
        ),
    })
    body = resp.get_json()

    assert resp.status_code == 200 and body["success"] is True
    assert body["hypothesis"]["title"] == ATF3_REPORTED_TITLE
    assert body["hypothesis"]["hypothesis"] == "ATF3 directly represses Vangl2 and Rgs3."
    assert body["hypothesis"]["justification"] == "The edited evidence remains visible."
    assert "**Hypothesis** ATF3 directly" in body["hypothesis"]["full_text"]
    assert "**Hypothesis**:" not in body["hypothesis"]["full_text"]
    assert "**Justification** The edited" in body["hypothesis"]["full_text"]
    assert "created_version_number" not in body
    assert "result_event_id" not in body

    history = _version_history(gen)
    assert history["active_position"] == 0
    assert len(history["results"]) == 1
    assert history["results"][0]["result_event_id"] == original_result_id
    assert history["results"][0]["snapshot"]["hypothesis"] == body["hypothesis"]["hypothesis"]
    assert history["results"][0]["snapshot"]["justification"] == body["hypothesis"]["justification"]


def test_manual_edit_of_historical_v1_preserves_v1_title_and_leaves_later_versions_unchanged(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    v1_text = (
        f"**Title**: {ATF3_REPORTED_TITLE}\n\n"
        "**Hypothesis**: ATF3 represses regeneration genes at poised enhancers."
    )
    # _seed_slot_regeneration_history seeds V1 = v1_text (historical) and
    # V2 = gen.hypotheses[0] (current, HYP1/"Axon growth" from _guided_client).
    result_id, _historical_thread, _current_thread = _seed_slot_regeneration_history(rd, gen, v1_text)
    v2_text_before = gen.hypotheses[0]

    resp = client.post("/api/edit", json={
        "index": 1,
        "state_id": "sid-1",  # the slot's current (V2) state -- required to pass staleness checks
        "manual_hypothesis": "**Hypothesis**: ATF3 directly represses Vangl2 and Rgs3 in resting RGCs.",
        "action": "edit_regeneration_version",
        "regeneration_position": 0,
        "regeneration_result_id": result_id,
    })
    body = resp.get_json()

    assert resp.status_code == 200 and body["success"] is True
    assert body["hypothesis"]["title"] == ATF3_REPORTED_TITLE
    assert body["hypothesis"]["title"] != "ATF3"

    history = _version_history(gen)
    assert history["results"][0]["snapshot"]["title"] == ATF3_REPORTED_TITLE
    assert "Vangl2 and Rgs3" in history["results"][0]["snapshot"]["full_text"]
    # Later version (V2, the slot's current content at seed time) is untouched.
    assert history["results"][1]["snapshot"]["full_text"] == v2_text_before


def test_llm_revision_still_creates_new_version_with_its_own_title(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    gen.hypotheses = [
        f"**Title**: {ATF3_REPORTED_TITLE}\n\n**Hypothesis**: ATF3 represses regeneration genes."
    ]
    gen.hypothesis_state_ids = ["sid-1"]
    _seed_current_version_history(rd, gen)

    new_title = "ATF3-Vangl2/Rgs3 Co-Occupancy Defines a Repressive Chromatin Module"
    gen.edit_hypothesis = lambda idx, feedback=None: (
        f"**Title**: {new_title}\n\n"
        "**Hypothesis**: ATF3 co-occupies Vangl2 and Rgs3 with repressive marks."
    )

    resp = client.post("/api/edit", json={
        "index": 1, "state_id": "sid-1", "feedback": "Make this more mechanistic.",
    })
    body = resp.get_json()

    assert resp.status_code == 200 and body["success"] is True
    assert body["created_version_number"] == 2  # LLM revision -> next global version
    assert body["hypothesis"]["title"] == new_title
    assert body["hypothesis"]["title"] != ATF3_REPORTED_TITLE

    history = _version_history(gen)
    assert len(history["results"]) == 2
    assert history["results"][0]["snapshot"]["title"] == ATF3_REPORTED_TITLE  # V1 untouched
    assert history["results"][1]["snapshot"]["title"] == new_title


def test_regeneration_still_creates_new_version_with_its_own_title(monkeypatch, tmp_path):
    rd, gen, client = _guided_client(monkeypatch, tmp_path)
    gen.hypotheses = [
        f"**Title**: {ATF3_REPORTED_TITLE}\n\n**Hypothesis**: ATF3 represses regeneration genes."
    ]
    gen.hypothesis_state_ids = ["sid-1"]
    _seed_current_version_history(rd, gen)

    new_title = "PTEN Deletion Restores Regenerative Competence in Injured RGCs"
    gen.regenerate_hypothesis = lambda idx, feedback=None: (
        f"**Title**: {new_title}\n\n"
        "**Hypothesis**: PTEN loss reactivates mTOR-dependent axon growth."
    )

    resp = client.post("/api/regenerate", json={
        "index": 1, "state_id": "sid-1", "feedback": "Try a different mechanism.",
    })
    body = resp.get_json()

    assert resp.status_code == 200 and body["success"] is True
    assert body["hypothesis"]["title"] == new_title
    assert body["hypothesis"]["title"] != ATF3_REPORTED_TITLE

    history = _version_history(gen)
    assert len(history["results"]) == 2
    assert history["results"][0]["snapshot"]["title"] == ATF3_REPORTED_TITLE
    assert history["results"][1]["snapshot"]["title"] == new_title
