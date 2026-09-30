"""Human release version, independent of source identity and update ordering."""
import re

_SEMVER = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def validate_release_version(value: object) -> str:
    match = _SEMVER.fullmatch(value) if isinstance(value, str) else None
    if not match or any(part.isdigit() and len(part) > 1 and part[0] == '0'
                        for part in (match.group(4) or '').split('.')):
        raise ValueError('release_version must be a semantic version such as 1.0.0 or 1.0.0-rc.1')
    return value
