import sys
from da_selection_ui import app as app_module


def test_server_markdown_never_inserts_active_html():
    html = app_module.render_markdown_to_html('<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[bad](javascript:alert(1))')
    assert '<script' not in html
    assert ' onerror=' not in html or '&lt;img' in html
    assert 'href="javascript:' not in html


def test_server_markdown_missing_sanitizer_is_escaped(monkeypatch):
    monkeypatch.setitem(sys.modules, 'bleach', None)
    assert '&lt;img' in app_module.render_markdown_to_html('<img src=x onerror=alert(1)>')
