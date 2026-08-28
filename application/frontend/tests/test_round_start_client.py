from pathlib import Path

from da_selection_ui import app as app_module


def test_shared_round_start_asset_precedes_each_surface_client():
    templates = Path(app_module.__file__).resolve().parent / "templates"
    surfaces = {
        "project_home.html": "project_home.js",
        "hypothesis_studio.html": "hypothesis_studio.js",
        "round_workspace.html": "round_workspace.js",
    }

    for template_name, surface_asset in surfaces.items():
        source = (templates / template_name).read_text(encoding="utf-8")
        shared = "filename='round_start.js'"
        surface = f"filename='{surface_asset}'"
        assert source.index(shared) < source.index(surface)

    overlay = (templates / "_start_round_overlay.html").read_text(encoding="utf-8")
    assert "data-start-round" in overlay
