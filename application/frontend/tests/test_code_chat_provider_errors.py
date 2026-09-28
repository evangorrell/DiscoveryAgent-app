import pytest
from AIR.utils import code_chat
from test_code_chat_resume import FakeOptions, FakeResultMessage


class AssistantFailure:
    error = 'provider_error'
    content = []


@pytest.mark.parametrize('failure', ['authentication', 'workspace', 'insufficient credits', 'model unavailable', 'provider rejected', 'error_max_turns', 'assistant'])
def test_terminal_errors_are_safe_retryable_and_rollback_history(monkeypatch, tmp_path, failure):
    async def query(**kwargs):
        yield (AssistantFailure() if failure == 'assistant' else FakeResultMessage(
            'failed-session', failure + ' sensitive-scientific-context sk-ant-never-echo',
            subtype='error_max_turns' if failure == 'error_max_turns' else 'error_during_execution', is_error=True))
    for name, value in [('query', query), ('ClaudeAgentOptions', FakeOptions), ('ResultMessage', FakeResultMessage),
                        ('AssistantMessage', AssistantFailure), ('SystemMessage', None), ('StreamEvent', None)]:
        monkeypatch.setattr(code_chat, name, value)
    history = code_chat.CodeChatConversation()
    history.add_user_message('Previous question')
    history.add_assistant_message('Previous answer')
    history.sdk_session_id = 'previous-session'
    before = list(history.messages)
    for _ in range(2):
        result = code_chat.run_code_chat('Retry me', tmp_path, include_trace=True, conversation=history)
        assert result['error_type'] == 'provider_error'
        assert result['answer'] == ''
        assert result['error']
        assert 'sensitive-scientific-context' not in str(result)
        assert 'sk-ant' not in str(result)
        assert history.messages == before
        assert history.sdk_session_id == 'previous-session'
