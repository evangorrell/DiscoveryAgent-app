"""Curated Anthropic models supported by DiscoveryAgent V2 projects.

The picker is deliberately installation-owned rather than populated from
Anthropic at page-load time. Projects store distinct reasoning and execution
choices; the local runner expands those roles across the concrete backend
configuration fields when it snapshots a fresh round.
"""

from __future__ import annotations

from typing import Any


DEFAULT_REASONING_MODEL = "claude-opus-4-6"
DEFAULT_EXECUTION_MODEL = "claude-opus-4-6"
# Compatibility name for older callers and persisted unified-model projects.
DEFAULT_PROJECT_MODEL = DEFAULT_REASONING_MODEL

# This is the complete, curated set the V2 product is prepared to run.  Do not
# accept arbitrary provider/model strings merely because an upstream account
# happens to list them.
SUPPORTED_PROJECT_MODELS: tuple[dict[str, str], ...] = (
    {"id": "claude-opus-5", "display_name": "Claude Opus 5"},
    {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5"},
    {"id": "claude-opus-4-8", "display_name": "Claude Opus 4.8"},
    {"id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6"},
    {"id": "claude-opus-4-6", "display_name": "Claude Opus 4.6"},
    {"id": "claude-haiku-4-5-20251001", "display_name": "Claude Haiku 4.5"},
    {"id": "claude-sonnet-4-5-20250929", "display_name": "Claude Sonnet 4.5"},
)

# argparse destinations read by scripts/run_hb_sherlock.py and its workflow /
# tool-agent config builders. Deep-research's provider-specific retrieval model
# is intentionally not here: it is not an Anthropic DiscoveryAgent model.
REASONING_MODEL_CONFIG_FIELDS: tuple[str, ...] = (
    "hypothesis_model",
    "memory_llm_model",
    "review_optimization_model",
    "review_designer_model",
    "director_model",
    "report_summarization_model",
    "single_search_model",
    "opentargets_model",
)
EXECUTION_MODEL_CONFIG_FIELDS: tuple[str, ...] = ("execution_model",)
PROJECT_MODEL_CONFIG_FIELDS = EXECUTION_MODEL_CONFIG_FIELDS + REASONING_MODEL_CONFIG_FIELDS


class UnsupportedProjectModel(ValueError):
    """A saved or submitted project model is outside the curated catalog."""

    def __init__(self, model_id: str):
        self.model_id = model_id
        super().__init__(
            f"{model_id} is no longer a supported DiscoveryAgent Anthropic model. "
            "Choose a supported replacement in Edit Project before starting another round."
        )


def available_models(**_ignored: Any) -> dict[str, Any]:
    """Return the deterministic project picker catalog without a network call."""
    return {
        "models": project_model_choices(),
        "source": "curated",
        "error": None,
    }


def project_model_choices(*, default_model: str = DEFAULT_REASONING_MODEL) -> list[dict[str, Any]]:
    """Presentation-ready choices with exactly one explicit default."""
    return [
        {
            **entry,
            "default": entry["id"] == default_model,
            "label": (
                f'{entry["display_name"]} (Default)'
                if entry["id"] == default_model
                else entry["display_name"]
            ),
        }
        for entry in SUPPORTED_PROJECT_MODELS
    ]


def known_model_ids() -> set[str]:
    """Stable identifiers accepted by project create/edit and round launch."""
    return {entry["id"] for entry in SUPPORTED_PROJECT_MODELS}


def validate_project_model(value: Any) -> str:
    """Return a normalized curated Anthropic id or raise a recoverable error."""
    model_id = str(value or "").strip()
    if not model_id or model_id not in known_model_ids() or not model_id.startswith("claude-"):
        raise UnsupportedProjectModel(model_id or "The selected model")
    return model_id


def resolve_project_model(project: dict[str, Any] | None) -> str:
    """Resolve a project model; missing legacy metadata uses the current default.

    An explicit but retired/invalid value is never silently replaced.  That
    distinction keeps old projects readable while requiring an intentional
    Edit Project save before new execution can start.
    """
    raw = project.get("project_model") if isinstance(project, dict) else None
    if raw is None or not str(raw).strip():
        return DEFAULT_PROJECT_MODEL
    return validate_project_model(raw)


def resolve_project_models(project: dict[str, Any] | None) -> dict[str, str]:
    """Resolve both project roles without silently replacing explicit values.

    Read-time migration accepts the former unified ``project_model`` contract
    and the PI-era ``hypothesis_model``/``execution_model`` split. Nothing is
    written until the scientist explicitly saves Create/Edit Project.
    """
    source = project if isinstance(project, dict) else {}
    unified = source.get("project_model")
    reasoning = source.get("reasoning_model")
    if reasoning is None or not str(reasoning).strip():
        reasoning = source.get("hypothesis_model")
    if reasoning is None or not str(reasoning).strip():
        reasoning = unified
    execution = source.get("execution_model")
    if execution is None or not str(execution).strip():
        execution = unified
    if reasoning is None or not str(reasoning).strip():
        reasoning = DEFAULT_REASONING_MODEL
    if execution is None or not str(execution).strip():
        execution = DEFAULT_EXECUTION_MODEL
    return {
        "reasoning_model": validate_project_model(reasoning),
        "execution_model": validate_project_model(execution),
    }


def project_model_role_config(reasoning_model: Any, execution_model: Any) -> dict[str, str]:
    """Map the two project roles to every concrete backend model field."""
    reasoning = validate_project_model(reasoning_model)
    execution = validate_project_model(execution_model)
    return {
        **{field: reasoning for field in REASONING_MODEL_CONFIG_FIELDS},
        **{field: execution for field in EXECUTION_MODEL_CONFIG_FIELDS},
    }


def project_model_config(model_id: Any) -> dict[str, str]:
    """Compatibility expansion for a legacy unified-model caller."""
    validated = validate_project_model(model_id)
    return {
        "project_model": validated,
        **project_model_role_config(validated, validated),
    }


def reset_cache() -> None:
    """Backward-compatible no-op; the curated catalog has no runtime cache."""
