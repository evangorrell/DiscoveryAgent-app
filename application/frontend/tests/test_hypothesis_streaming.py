"""Anthropic hypothesis calls consume SSE streams and preserve final Messages."""

from types import SimpleNamespace
from unittest.mock import Mock

import anthropic

from AIR.hyp_agents.hypothesis_gen_agent import HypothesisGenAgent


class _Stream:
    def __init__(self, response):
        self.response = response
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def get_final_message(self):
        assert self.entered
        return self.response

    def __exit__(self, *_args):
        self.exited = True


class _Messages:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.last_stream = None

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        self.last_stream = _Stream(self.response)
        return self.last_stream

    def create(self, **_kwargs):
        raise AssertionError("Hypothesis calls must not use the silent non-streaming API")


def _agent(response):
    agent = HypothesisGenAgent.__new__(HypothesisGenAgent)
    messages = _Messages(response)
    agent.client = SimpleNamespace(messages=messages)
    agent.llm_config = SimpleNamespace(api_provider="anthropic", model_name="claude-opus-4-6")
    agent.messages = [{"role": "user", "content": "Generate hypotheses"}]
    agent.last_response_citations = []
    agent.last_response_documents = []
    return agent, messages


def test_anthropic_generation_consumes_stream_and_returns_final_text():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="Candidate set")])
    agent, messages = _agent(response)

    result = agent._call_api_with_retry(
        {"model": "claude-opus-4-6", "max_tokens": 8192, "messages": agent.messages},
        max_retries=0,
    )

    assert result == "Candidate set"
    assert messages.calls == [{
        "model": "claude-opus-4-6", "max_tokens": 8192, "messages": agent.messages,
    }]
    assert messages.last_stream.entered
    assert messages.last_stream.exited


def test_streamed_final_message_preserves_citations():
    citation = SimpleNamespace(to_dict=lambda: {"type": "char_location", "start_char_index": 2})
    response = SimpleNamespace(content=[
        SimpleNamespace(type="text", text="Grounded candidate", citations=[citation]),
    ])
    agent, _messages = _agent(response)
    documents = [{"title": "Round report"}]

    result = agent._call_api_with_retry(
        {"model": "claude-opus-4-6", "messages": agent.messages},
        max_retries=0,
        track_citations=True,
        documents=documents,
    )

    assert result == "Grounded candidate"
    assert agent.last_response_citations == [{
        "text": "Grounded candidate",
        "citations": [{"type": "char_location", "start_char_index": 2}],
    }]
    assert agent.last_response_documents == documents


def test_one_shot_call_streams_without_changing_conversation():
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="Revision")])
    agent, messages = _agent(response)
    conversation = list(agent.messages)

    assert agent.call_llm_once("System", "Revise", max_tokens=2000, temperature=0.5) == "Revision"
    assert messages.calls[0]["messages"] == [{"role": "user", "content": "Revise"}]
    assert agent.messages == conversation
    assert messages.last_stream.exited


def test_judge_streams_and_selects_returned_candidate():
    response = SimpleNamespace(content=[
        SimpleNamespace(type="text", text="**Selected Hypothesis**: 2"),
    ])
    agent, messages = _agent(response)
    agent.judge_client = agent.client
    agent.judge_llm_config = agent.llm_config
    agent.logger = Mock()

    assert agent._judge_hypotheses(["First", "Second"]) == "Second"
    assert messages.last_stream.exited


def test_transient_stream_failure_closes_and_retries(monkeypatch):
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="Recovered")])
    agent, messages = _agent(response)
    agent.logger = Mock()
    failed_stream = _Stream(response)
    failed_stream.get_final_message = Mock(side_effect=anthropic.APIError(
        "Overloaded", request=Mock(), body=None,
    ))
    recovered_stream = _Stream(response)
    monkeypatch.setattr(messages, "stream", Mock(side_effect=[failed_stream, recovered_stream]))

    assert agent._call_api_with_retry({}, max_retries=1, wait_time=0) == "Recovered"
    assert failed_stream.exited
    assert recovered_stream.exited
