from __future__ import annotations

import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

from AIR.tool_agents.deep_research import DeepResearchConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _project_dependencies() -> list[str]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["dependencies"]


def _project_configuration() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_default_deep_research_provider_has_required_sdk_dependency() -> None:
    assert DeepResearchConfig().deep_research_provider == "paperclip"

    paperclip_dependencies = [
        dependency
        for dependency in _project_dependencies()
        if dependency.lower().startswith("gxl-paperclip")
    ]

    assert paperclip_dependencies == ["gxl-paperclip==0.7.24"]

    source = _project_configuration()["tool"]["uv"]["sources"]["gxl-paperclip"]
    wheel = PROJECT_ROOT / source["path"]
    assert wheel.name == "gxl_paperclip-0.7.24-py3-none-any.whl"
    assert wheel.is_file()

    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    assert metadata["Name"] == "gxl-paperclip"
    assert metadata["Version"] == "0.7.24"


def test_unrelated_pypi_paperclip_package_is_not_declared() -> None:
    names = {
        dependency.partition(" ")[0].split("[")[0].split("=")[0].lower()
        for dependency in _project_dependencies()
    }

    assert "paperclip" not in names
