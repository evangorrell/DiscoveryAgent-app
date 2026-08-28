"""Release-facing contracts for the frontend-owned AIR runtime subset."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAPERCLIP_WHEEL = (
    REPOSITORY_ROOT / "vendor" / "gxl_paperclip-0.7.24-py3-none-any.whl"
)
PAPERCLIP_SHA256 = "036362c1408eb872183f7614d1784e1d5c2a770456878d2c6e02808cad593db8"


def _project_configuration() -> dict:
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_frontend_wheel_explicitly_owns_its_runtime_air_subset():
    configuration = _project_configuration()

    assert configuration["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "da_selection_ui",
        "AIR",
    ]


def test_frontend_air_runtime_dependencies_are_release_reproducible():
    configuration = _project_configuration()
    dependencies = set(configuration["project"]["dependencies"])
    sources = configuration["tool"]["uv"]["sources"]

    assert "claude-agent-sdk==0.2.131" in dependencies
    assert "gxl-paperclip==0.7.24" in dependencies
    assert sources["gxl-paperclip"] == {
        "path": "vendor/gxl_paperclip-0.7.24-py3-none-any.whl",
    }
    assert PAPERCLIP_WHEEL.is_file()
    assert hashlib.sha256(PAPERCLIP_WHEEL.read_bytes()).hexdigest() == PAPERCLIP_SHA256


def test_legacy_requirements_mirror_runtime_dependency_pins():
    requirements = {
        line.strip()
        for line in (REPOSITORY_ROOT / "da_selection_ui" / "requirements.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "claude-agent-sdk==0.2.131" in requirements
    assert "gxl-paperclip==0.7.24" in requirements
