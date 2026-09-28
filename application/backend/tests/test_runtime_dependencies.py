"""Offline default-runtime smoke checks; no provider request leaves the process."""
import json
from pathlib import Path
import tomllib

import httpx


def test_default_scientific_pdf_notebook_and_sdk_imports():
    import numpy
    import pydantic_core
    import claude_agent_sdk
    import pymupdf
    import pypdf
    import nbformat
    import nbclient
    import jupyter_client
    assert numpy.arange(3).sum() == 3
    assert pymupdf.open().page_count == 0
    assert nbformat.v4.new_notebook().nbformat == 4


def test_lightgbm_is_explicitly_optional():
    config = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())['project']
    assert not any(item.lower().startswith('lightgbm') for item in config['dependencies'])
    assert config['optional-dependencies']['lightgbm']


def test_together_chat_api_remains_compatible_with_patched_pillow():
    from together import Together
    def respond(request):
        body = json.loads(request.content)
        assert body['messages'][1]['content'] == 'offline fixture'
        assert body['frequency_penalty'] == 0
        return httpx.Response(200, json={
            'id': 'offline', 'object': 'chat.completion', 'created': 1, 'model': 'test',
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'fixture answer'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = Together(api_key='offline-fixture', http_client=http)
        result = client.chat.completions.create(model='test',
            messages=[{'role': 'system', 'content': 'fixture'}, {'role': 'user', 'content': 'offline fixture'}],
            temperature=0, max_tokens=10, frequency_penalty=0)
        assert result.choices[0].message.content == 'fixture answer'
