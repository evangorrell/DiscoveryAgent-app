import importlib.util
from pathlib import Path
from unittest.mock import Mock
import pytest


@pytest.mark.parametrize('area', ['frontend', 'backend'])
def test_content_logging_is_explicit_opt_in(monkeypatch, area):
    path = Path(__file__).resolve().parents[2] / area / 'AIR/utils/logger.py'
    spec = importlib.util.spec_from_file_location('privacy_logger', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    logger = object.__new__(module.Logger)
    logger.logger = Mock()
    logger.analysis_name = 'test'
    monkeypatch.delenv('DISCOVERYAGENT_DEBUG_PROMPTS', raising=False)
    logger.log_prompt('user', 'private research')
    logger.log_response('private research')
    logger.log_code('private research')
    logger.log_action('system_prompt', 'private research')
    logger.log_action('Created initial notebook', 'private research')
    logger.log_action('selected_hypothesis_full', 'private research')
    logger.log_error('Operation failed', code='private research')
    logger.log_action('round_started', 'round=1')
    assert 'private research' not in str(logger.logger.mock_calls)
    assert 'round=1' in str(logger.logger.mock_calls)
    monkeypatch.setenv('DISCOVERYAGENT_DEBUG_PROMPTS', '1')
    logger.log_prompt('user', 'private research')
    assert 'private research' in str(logger.logger.mock_calls)
