from AIR.utils import code_chat


class FakeOptions:
    def __init__(
        self,
        *,
        resume=None,
        continue_conversation=False,
        system_prompt=None,
        cwd=None,
        allowed_tools=None,
        permission_mode=None,
        model=None,
        max_turns=None,
        max_buffer_size=None,
        agents=None,
        **kwargs,
    ):
        self.resume = resume
        self.continue_conversation = continue_conversation
        self.system_prompt = system_prompt
        self.cwd = cwd
        self.allowed_tools = allowed_tools or []
        self.permission_mode = permission_mode
        self.model = model
        self.max_turns = max_turns
        self.max_buffer_size = max_buffer_size
        self.agents = agents
        self.extra = kwargs


class FakeResultMessage:
    def __init__(self, session_id, result, subtype="success"):
        self.session_id = session_id
        self.result = result
        self.subtype = subtype


def test_run_code_chat_serializes_conversation_even_with_sdk_session(monkeypatch, tmp_path):
    calls = []

    async def fake_query(*, prompt, options):
        call_index = len(calls) + 1
        calls.append({"prompt": prompt, "options": options})
        yield FakeResultMessage(session_id=f"sdk-session-{call_index}", result=f"answer-{call_index}")

    monkeypatch.setattr(code_chat, "query", fake_query)
    monkeypatch.setattr(code_chat, "ClaudeAgentOptions", FakeOptions)
    monkeypatch.setattr(code_chat, "ResultMessage", FakeResultMessage)
    monkeypatch.setattr(code_chat, "AssistantMessage", None)
    monkeypatch.setattr(code_chat, "SystemMessage", None)
    monkeypatch.setattr(code_chat, "StreamEvent", None)

    conversation = code_chat.CodeChatConversation()

    first = code_chat.run_code_chat(
        question="What happened in round 1?",
        working_dir=tmp_path,
        model="test-model",
        report_prompt="REPORT SEED",
        conversation=conversation,
    )

    assert first == "answer-1"
    assert conversation.sdk_session_id == "sdk-session-1"
    assert calls[0]["prompt"].startswith("Conversation so far:\nUser: What happened in round 1?")
    assert "REPORT SEED" in calls[0]["options"].system_prompt
    assert calls[0]["options"].resume is None
    assert calls[0]["options"].continue_conversation is False

    second = code_chat.run_code_chat(
        question="What changed afterward?",
        working_dir=tmp_path,
        model="test-model",
        report_prompt="REPORT SEED",
        conversation=conversation,
    )

    assert second == "answer-2"
    assert conversation.sdk_session_id == "sdk-session-2"
    assert calls[1]["prompt"].startswith(
        "Conversation so far:\n"
        "User: What happened in round 1?\n\n"
        "Assistant: answer-1\n\n"
        "User: What changed afterward?"
    )
    assert calls[1]["options"].resume is None
    assert calls[1]["options"].continue_conversation is False
    assert "REPORT SEED" in calls[1]["options"].system_prompt
