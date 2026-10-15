import sys
from html.parser import HTMLParser
from da_selection_ui import app as app_module


def test_server_markdown_never_inserts_active_html():
    html = app_module.render_markdown_to_html('<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[bad](javascript:alert(1))')
    assert '<script' not in html
    assert ' onerror=' not in html or '&lt;img' in html
    assert 'href="javascript:' not in html


def test_server_markdown_missing_sanitizer_is_escaped(monkeypatch):
    monkeypatch.setitem(sys.modules, 'bleach', None)
    assert '&lt;img' in app_module.render_markdown_to_html('<img src=x onerror=alert(1)>')


class _ImageTags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _rewrite_readme_images(html, code_dir):
    return app_module.rewrite_image_sources(
        html, checkpoint_prefix=None, code_prefix='/readme_assets/',
        checkpoint_dir=None, code_dir=code_dir,
    )


def test_rewritten_image_symlink_target_cannot_escape_src_attribute(tmp_path):
    target = tmp_path / 'figure" onerror="alert(1).png'
    target.write_bytes(b'image')
    (tmp_path / 'figure.png').symlink_to(target.name)
    html = _rewrite_readme_images(
        app_module.render_markdown_to_html('![plot](figure.png)'), tmp_path.resolve(),
    )
    images = [attrs for tag, attrs in _ImageTags(html).tags if tag == 'img']
    assert images == [{'alt': 'plot', 'src': '/readme_assets/' + target.name}]


def test_rewritten_image_keeps_entity_encoded_filenames_intact(tmp_path):
    image = tmp_path / 'a&b "quoted".png'
    image.write_bytes(b'image')
    html = _rewrite_readme_images(
        app_module.render_markdown_to_html('![plot](<a&b "quoted".png>)'), tmp_path.resolve(),
    )
    images = [attrs for tag, attrs in _ImageTags(html).tags if tag == 'img']
    assert images == [{'alt': 'plot', 'src': '/readme_assets/' + image.name}]
