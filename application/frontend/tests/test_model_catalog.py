"""Project-level curated Anthropic model contract."""

import pytest

from da_selection_ui import model_catalog


def test_catalog_is_curated_deterministic_and_has_exactly_one_default(monkeypatch):
    import anthropic

    def network_must_not_open(*_args, **_kwargs):
        raise AssertionError("opening Create/Edit must not query Anthropic")

    monkeypatch.setattr(anthropic, "Anthropic", network_must_not_open)
    first = model_catalog.available_models()
    second = model_catalog.available_models(refresh=True)

    assert first == second
    assert first["source"] == "curated"
    assert first["error"] is None
    assert sum(bool(item["default"]) for item in first["models"]) == 1
    default = next(item for item in first["models"] if item["default"])
    assert default["id"] == model_catalog.DEFAULT_PROJECT_MODEL
    assert default["label"].endswith(" (Default)")
    assert all(item["id"].startswith("claude-") for item in first["models"])


def test_only_curated_anthropic_ids_validate():
    assert model_catalog.validate_project_model(" claude-opus-4-6 ") == "claude-opus-4-6"

    for rejected in ("", "gpt-4o", "claude-unlisted-future-model"):
        with pytest.raises(model_catalog.UnsupportedProjectModel):
            model_catalog.validate_project_model(rejected)


def test_missing_legacy_metadata_uses_default_but_invalid_saved_value_does_not():
    assert model_catalog.resolve_project_model({}) == model_catalog.DEFAULT_PROJECT_MODEL
    assert model_catalog.resolve_project_model({"project_model": ""}) == model_catalog.DEFAULT_PROJECT_MODEL

    with pytest.raises(model_catalog.UnsupportedProjectModel) as retired:
        model_catalog.resolve_project_model({"project_model": "claude-retired-1"})
    assert "Choose a supported replacement" in str(retired.value)


def test_one_model_expands_to_every_backend_model_field():
    chosen = "claude-sonnet-4-6"
    config = model_catalog.project_model_config(chosen)

    assert config["project_model"] == chosen
    assert set(config) == {"project_model", *model_catalog.PROJECT_MODEL_CONFIG_FIELDS}
    assert {config[field] for field in model_catalog.PROJECT_MODEL_CONFIG_FIELDS} == {chosen}


def test_separate_roles_expand_to_the_complete_backend_model_contract():
    reasoning = "claude-sonnet-4-6"
    execution = "claude-haiku-4-5-20251001"
    assert model_catalog.REASONING_MODEL_CONFIG_FIELDS == (
        "hypothesis_model",
        "memory_llm_model",
        "review_optimization_model",
        "review_designer_model",
        "director_model",
        "report_summarization_model",
        "single_search_model",
        "opentargets_model",
    )
    assert model_catalog.EXECUTION_MODEL_CONFIG_FIELDS == ("execution_model",)

    config = model_catalog.project_model_role_config(reasoning, execution)
    assert set(config) == set(model_catalog.PROJECT_MODEL_CONFIG_FIELDS)
    assert {config[field] for field in model_catalog.REASONING_MODEL_CONFIG_FIELDS} == {reasoning}
    assert config["execution_model"] == execution
    assert config["report_summarization_model"] == reasoning


def test_unified_and_pi_era_metadata_are_read_without_eager_rewrite():
    unified = {"project_model": "claude-sonnet-4-6"}
    assert model_catalog.resolve_project_models(unified) == {
        "reasoning_model": "claude-sonnet-4-6",
        "execution_model": "claude-sonnet-4-6",
    }
    assert unified == {"project_model": "claude-sonnet-4-6"}

    separate = {
        "hypothesis_model": "claude-opus-4-6",
        "execution_model": "claude-haiku-4-5-20251001",
    }
    assert model_catalog.resolve_project_models(separate) == {
        "reasoning_model": "claude-opus-4-6",
        "execution_model": "claude-haiku-4-5-20251001",
    }
    assert "reasoning_model" not in separate
