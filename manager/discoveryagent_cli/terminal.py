"""Conservative clickable URLs: plain text unless a known TTY supports OSC 8."""
from typing import Mapping, TextIO
from urllib.parse import urlsplit


def local_url_link(url: str, stream: TextIO, environ: Mapping[str, str]) -> str:
    visible = ''.join(c for c in url if ord(c) >= 32 and ord(c) != 127)
    # Do not emit controls into redirected logs, Apple Terminal/unknown terminals,
    # or multiplexers whose passthrough capabilities have not been negotiated.
    if (visible != url or not getattr(stream, 'isatty', lambda: False)()
            or environ.get('TERM') == 'dumb' or environ.get('TMUX') or environ.get('STY')
            or environ.get('TERM_PROGRAM') not in {'iTerm.app', 'WezTerm'}):
        return visible
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
                 and parsed.port is not None and 0 < parsed.port < 65536
                 and parsed.path == '/' and not parsed.query and not parsed.fragment
                 and not parsed.username and not parsed.password)
    except ValueError:
        valid = False
    if not valid:
        return visible
    return f'\033]8;;{url}\033\\{url}\033]8;;\033\\'
