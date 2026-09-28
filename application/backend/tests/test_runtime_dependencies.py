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


def test_lock_retains_reviewed_runtime_dependency_floors():
    """A plain re-lock must not silently lose transitive hardening updates."""
    from packaging.version import Version
    lock = tomllib.loads((Path(__file__).resolve().parents[1] / 'uv.lock').read_text())
    floors = {
        'aiohttp': '3.14.3', 'anyio': '4.14.2', 'authlib': '1.8.0',
        'bleach': '6.4.0', 'cryptography': '50.0.1', 'filelock': '3.32.7',
        'fonttools': '4.66.0', 'idna': '3.20', 'jupyter-server': '2.21.1',
        'jupyterlab': '4.6.4', 'lxml': '6.1.3', 'mistune': '3.3.4',
        'nbconvert': '7.17.1', 'pillow': '12.3.0', 'pygments': '2.21.0',
        'pyjwt': '2.15.0', 'pypdf': '6.19.0', 'python-multipart': '0.0.32',
        'requests': '2.34.2', 'setuptools': '84.0.0', 'starlette': '1.7.0',
        'together': '2.37.0', 'tornado': '6.5.10', 'urllib3': '2.8.0',
    }
    for name, floor in floors.items():
        versions = [p['version'] for p in lock['package'] if p['name'] == name]
        assert versions, name
        assert all(Version(v) >= Version(floor) for v in versions), (name, versions)


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
