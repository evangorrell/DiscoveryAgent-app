"""Non-circular identity of the program files carried by an install archive."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def package_source_manifest(root: Path) -> dict:
    result = {}
    for component in ('application', 'manager', 'tools'):
        entries = []
        for path in sorted((root / component).rglob('*')):
            relative = path.relative_to(root / component).as_posix()
            if component == 'manager' and (relative == 'discoveryagent_cli/testing.py'
                    or '__pycache__' in path.parts or path.suffix == '.pyc'):
                continue
            if path.is_symlink():
                entries.append([relative, 'symlink', 0o777, os.readlink(path)])
            elif path.is_file():
                mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                entries.append([relative, 'file', mode, hashlib.sha256(path.read_bytes()).hexdigest()])
        result[component] = entries
    return result
