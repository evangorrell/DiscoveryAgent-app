"""Installation-safe access to packaged frontend guide content."""

from importlib import resources


def read_audit_guide_markdown() -> str | None:
    """Return the packaged Audit Agent guide, or ``None`` if unavailable."""
    try:
        return resources.files("da_selection_ui.guides").joinpath("AUDIT_UI.md").read_text(
            encoding="utf-8"
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None
