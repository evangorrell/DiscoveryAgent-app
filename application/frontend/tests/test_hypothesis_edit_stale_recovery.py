from da_selection_ui import app as app_module


INITIAL_HYPOTHESIS = (
    "**Title**: Initial\n\n"
    "**Hypothesis**: Original claim.\n\n"
    "**Justification**: Original justification.\n\n"
    "**Interpretation**: Original interpretation."
)

EDITED_HYPOTHESIS = (
    "**Title**: Edited\n\n"
    "**Hypothesis**: Revised claim.\n\n"
    "**Justification**: Revised justification.\n\n"
    "**Interpretation**: Revised interpretation."
)


class DummyHypothesisAgent:
    last_response_citations = []


class DummyGenerator:
    def __init__(self):
        self.hypotheses = [INITIAL_HYPOTHESIS]
        self.hypothesis_state_ids = ["fresh-state-id"]
        self.hypothesis_gen_agent = DummyHypothesisAgent()
        self.citation_documents = []
        self.edit_calls = []
        self.regenerate_calls = []
        self.final_selection = None

    def _sanitize_hypothesis_text(self, text, title_override=None):
        text = (text or "").strip()
        if title_override is None:
            return text
        sections = self.parse_hypothesis_sections(text)
        sections["title"] = title_override
        parts = []
        if sections.get("title"):
            parts.append(f"**Title**: {sections['title']}")
        if sections.get("hypothesis"):
            parts.append(f"**Hypothesis**: {sections['hypothesis']}")
        if sections.get("justification"):
            parts.append(f"**Justification**: {sections['justification']}")
        if sections.get("interpretation"):
            parts.append(f"**Interpretation**: {sections['interpretation']}")
        if sections.get("plan"):
            parts.append(f"**Plan**: {sections['plan']}")
        sanitized = "\n\n".join(parts).strip()
        return sanitized if sanitized else text

    def parse_hypothesis_sections(self, text):
        sections = {
            "title": None,
            "hypothesis": "",
            "justification": "",
            "interpretation": "",
            "plan": None,
        }
        for block in (text or "").split("\n\n"):
            if ":" not in block:
                continue
            key, value = block.split(":", 1)
            normalized = key.replace("*", "").strip().lower()
            value = value.strip()
            if normalized == "title":
                sections["title"] = value
            elif normalized == "hypothesis":
                sections["hypothesis"] = value
            elif normalized == "justification":
                sections["justification"] = value
            elif normalized == "interpretation":
                sections["interpretation"] = value
            elif normalized == "plan":
                sections["plan"] = value
        return sections

    def edit_hypothesis(self, idx, feedback=None):
        self.edit_calls.append({"idx": idx, "feedback": feedback})
        self.hypotheses[idx - 1] = EDITED_HYPOTHESIS
        return EDITED_HYPOTHESIS

    def regenerate_hypothesis(self, idx, feedback=None):
        self.regenerate_calls.append({"idx": idx, "feedback": feedback})
        self.hypotheses[idx - 1] = EDITED_HYPOTHESIS
        return EDITED_HYPOTHESIS

    def save_selection_raw(self, output_content, agent_guidance=None, username=None):
        self.final_selection = {
            "selection_type": "raw",
            "final_hypothesis": output_content,
            "output_path": "[test]",
            "timestamped_output_path": "[test]",
        }
        return "[test]"

    def save_session_log(self):
        return "[test-log]"

    def persist_generated_hypotheses(self):
        return None


def _build_client(monkeypatch, generator):
    monkeypatch.setattr(app_module, "get_generator", lambda: generator)
    monkeypatch.setattr(app_module, "get_or_restore_generator", lambda: generator)
    monkeypatch.setattr(app_module, "_is_audit_only_mode", lambda: False)
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["session_id"] = "test-session"
    return client


def test_api_edit_restores_generator_and_seeds_missing_hypothesis_from_request(monkeypatch):
    generator = DummyGenerator()
    generator.hypotheses = []
    generator.hypothesis_state_ids = []
    monkeypatch.setattr(app_module, "get_or_restore_generator", lambda: generator)
    monkeypatch.setattr(app_module, "_is_audit_only_mode", lambda: False)
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["authenticated"] = True
        flask_session["session_id"] = "test-session"

    response = client.post(
        "/api/edit",
        json={
            "index": 1,
            "state_id": "client-state-id",
            "feedback": "Make it more specific",
            "manual_hypothesis": INITIAL_HYPOTHESIS,
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert generator.edit_calls[0]["idx"] == 1
    assert "Make it more specific" in generator.edit_calls[0]["feedback"]


def test_api_edit_recovers_from_stale_state_when_manual_hypothesis_is_present(monkeypatch):
    generator = DummyGenerator()
    client = _build_client(monkeypatch, generator)

    response = client.post(
        "/api/edit",
        json={
            "index": 1,
            "state_id": "stale-state-id",
            "feedback": "Combine this with hypothesis 1",
            "manual_hypothesis": INITIAL_HYPOTHESIS,
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["hypothesis"]["index"] == 1
    assert payload["hypothesis"]["state_id"] != "fresh-state-id"
    assert generator.edit_calls == [
        {
            "idx": 1,
            "feedback": (
                "Use the user-edited draft below as the starting point. "
                "Respect the Title/Hypothesis/Justification structure. "
                "Apply the requested changes that follow.\n\n"
                f"User-edited draft:\n{INITIAL_HYPOTHESIS}\n\n"
                "Requested changes:\nCombine this with hypothesis 1"
            ),
        }
    ]


def test_api_edit_rejects_stale_state_without_manual_hypothesis(monkeypatch):
    generator = DummyGenerator()
    client = _build_client(monkeypatch, generator)

    response = client.post(
        "/api/edit",
        json={
            "index": 1,
            "state_id": "stale-state-id",
            "feedback": "Combine this with hypothesis 1",
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is False
    assert payload["stale_selection"] is True
    assert "out of date" in payload["error"]
    assert generator.edit_calls == []


def test_api_regenerate_restores_generator_and_seeds_missing_hypothesis_from_request(monkeypatch):
    generator = DummyGenerator()
    generator.hypotheses = []
    generator.hypothesis_state_ids = []
    client = _build_client(monkeypatch, generator)

    response = client.post(
        "/api/regenerate",
        json={
            "index": 1,
            "state_id": "client-state-id",
            "feedback": "Make it cleaner",
            "manual_hypothesis": INITIAL_HYPOTHESIS,
            "generation_id": 0,
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert generator.regenerate_calls[0]["idx"] == 1
    assert "Make it cleaner" in generator.regenerate_calls[0]["feedback"]


def test_api_regenerate_guidance_uses_request_draft_after_restore(monkeypatch):
    generator = DummyGenerator()
    generator.hypotheses = []
    generator.hypothesis_state_ids = []
    client = _build_client(monkeypatch, generator)

    response = client.post(
        "/api/regenerate_guidance",
        json={
            "index": 1,
            "state_id": "client-state-id",
            "guidance": "Prioritize measurable outcomes",
            "manual_hypothesis": INITIAL_HYPOTHESIS,
            "generation_id": 0,
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert generator.regenerate_calls[0]["idx"] == 1
    assert "Prioritize measurable outcomes" in generator.regenerate_calls[0]["feedback"]


def test_regenerate_guidance_accepts_stale_list_token_when_target_card_is_still_current(monkeypatch):
    generator = DummyGenerator()
    generator.hypotheses = [INITIAL_HYPOTHESIS, INITIAL_HYPOTHESIS]
    # Hypothesis 1 changed while this request waited; Hypothesis 2 did not.
    generator.hypothesis_state_ids = ["h1-new-state", "h2-stable-state"]
    client = _build_client(monkeypatch, generator)
    monkeypatch.setattr(app_module, "_get_generation_id", lambda: 8)

    response = client.post(
        "/api/regenerate_guidance",
        json={
            "index": 2,
            "state_id": "h2-stable-state",
            "request_hypothesis": INITIAL_HYPOTHESIS,
            "generation_id": 7,
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["hypothesis"]["index"] == 2
    assert payload["generation_id"] == 8
    assert generator.regenerate_calls[0]["idx"] == 2


def test_regenerate_guidance_rejects_stale_list_token_when_target_card_was_replaced(monkeypatch):
    generator = DummyGenerator()
    generator.hypotheses = [INITIAL_HYPOTHESIS, INITIAL_HYPOTHESIS]
    generator.hypothesis_state_ids = ["h1-new-state", "h2-new-state"]
    client = _build_client(monkeypatch, generator)
    monkeypatch.setattr(app_module, "_get_generation_id", lambda: 8)

    response = client.post(
        "/api/regenerate_guidance",
        json={
            "index": 2,
            "state_id": "h2-old-state",
            "request_hypothesis": INITIAL_HYPOTHESIS,
            "generation_id": 7,
        },
    )

    payload = response.get_json()
    assert payload["success"] is False
    assert payload["stale_selection"] is True
    assert generator.regenerate_calls == []


def test_api_select_accepts_final_text_after_generator_restore(monkeypatch):
    generator = DummyGenerator()
    generator.hypotheses = []
    generator.hypothesis_state_ids = []
    client = _build_client(monkeypatch, generator)
    monkeypatch.setattr(app_module, "CLI_DRY_RUN", True)
    monkeypatch.setattr(app_module, "_get_project_round_from_disk", lambda: 1)

    response = client.post(
        "/api/select",
        json={
            "selection": 1,
            "selection_state_id": "client-state-id",
            "final_text": INITIAL_HYPOTHESIS,
        },
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["full_text"] == INITIAL_HYPOTHESIS
    assert generator.hypotheses[0] == INITIAL_HYPOTHESIS
