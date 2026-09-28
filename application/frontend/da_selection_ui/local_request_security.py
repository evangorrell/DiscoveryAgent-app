"""Browser boundary for the unauthenticated, loopback-only application.

Checks also cover GETs: legacy reads can reconcile or activate projects.
Headerless local CLI clients remain supported. Proxy headers are not trusted.
"""

from urllib.parse import urlsplit

from flask import jsonify, request


def _origin(value: str):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or parsed.username is not None
                or parsed.password is not None or parsed.hostname is None):
            return None
        return (parsed.scheme, parsed.hostname.lower(), parsed.port or
                (443 if parsed.scheme == "https" else 80))
    except ValueError:
        return None


def enforce_local_request_boundary():
    local = _origin(request.host_url)
    if local is None or local[1] not in {"localhost", "127.0.0.1", "::1"}:
        return jsonify(error="DiscoveryAgent accepts loopback hosts only."), 403
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return jsonify(error="Cross-site requests are not allowed."), 403
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    # Another local application's port is not a trusted origin either.
    if ((origin is not None and _origin(origin) != local)
            or (origin is None and referer and _origin(referer) != local)):
        return jsonify(error="Cross-origin requests are not allowed."), 403
    return None
