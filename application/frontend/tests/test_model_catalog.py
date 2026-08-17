"""The model picker rolls forward with Anthropic instead of going stale.

The point of asking the Models API is that a model released after this code
shipped is offered without an edit here. The tests below pin that, plus the
floor that keeps the app usable when the API cannot be reached.
"""

import pytest

from da_selection_ui import model_catalog


@pytest.fixture(autouse=True)
def clear_cache():
    model_catalog.reset_cache()
    yield
    model_catalog.reset_cache()


class FakeModel:
    def __init__(self, model_id, display_name):
        self.id = model_id
        self.display_name = display_name


def fake_listing(monkeypatch, models, calls=None):
    def fetch():
        if calls is not None:
            calls.append(1)
        return [{"id": m.id, "display_name": m.display_name} for m in models]

    monkeypatch.setattr(model_catalog, "_fetch_from_anthropic", fetch)


def test_a_model_released_after_this_code_is_offered_without_an_edit(monkeypatch):
    fake_listing(monkeypatch, [
        FakeModel("claude-opus-9", "Claude Opus 9"),
        FakeModel("claude-opus-5", "Claude Opus 5"),
    ])

    catalog = model_catalog.available_models()

    assert catalog["source"] == "anthropic"
    assert [entry["id"] for entry in catalog["models"]] == ["claude-opus-9", "claude-opus-5"]
    assert "claude-opus-9" in model_catalog.known_model_ids()
    # Nothing in this module names that model.
    assert "claude-opus-9" not in {entry["id"] for entry in model_catalog.FALLBACK_MODELS}


def test_the_live_order_is_preserved_so_the_newest_model_is_first(monkeypatch):
    fake_listing(monkeypatch, [
        FakeModel("newest", "Newest"),
        FakeModel("older", "Older"),
        FakeModel("oldest", "Oldest"),
    ])

    listed = model_catalog.available_models()["models"]

    assert [entry["id"] for entry in listed] == ["newest", "older", "oldest"]


def test_an_unreachable_api_falls_back_instead_of_failing(monkeypatch):
    def explode():
        raise RuntimeError("no API key configured")

    monkeypatch.setattr(model_catalog, "_fetch_from_anthropic", explode)

    catalog = model_catalog.available_models()

    assert catalog["source"] == "builtin"
    assert catalog["models"] == [dict(entry) for entry in model_catalog.FALLBACK_MODELS]
    assert "no API key configured" in catalog["error"]


def test_a_failed_call_is_not_cached_as_the_answer(monkeypatch):
    state = {"fail": True}

    def flaky():
        if state["fail"]:
            raise RuntimeError("offline")
        return [{"id": "claude-opus-5", "display_name": "Claude Opus 5"}]

    monkeypatch.setattr(model_catalog, "_fetch_from_anthropic", flaky)
    assert model_catalog.available_models()["source"] == "builtin"

    state["fail"] = False
    assert model_catalog.available_models()["source"] == "anthropic"


def test_the_live_list_is_cached_rather_than_refetched_per_request(monkeypatch):
    calls = []
    fake_listing(monkeypatch, [FakeModel("claude-opus-5", "Claude Opus 5")], calls)

    model_catalog.available_models(now=100.0)
    model_catalog.available_models(now=100.0 + model_catalog.CACHE_TTL_SECONDS - 1)
    assert len(calls) == 1

    model_catalog.available_models(now=100.0 + model_catalog.CACHE_TTL_SECONDS + 1)
    assert len(calls) == 2


def test_a_pinned_model_stays_valid_even_when_the_live_list_drops_it(monkeypatch):
    fake_listing(monkeypatch, [FakeModel("claude-opus-9", "Claude Opus 9")])

    known = model_catalog.known_model_ids()

    # The live list offers only the newest model, but a project pinned to a
    # built-in one must not be invalidated by that.
    assert "claude-opus-9" in known
    assert "claude-sonnet-4-6" in known


def test_an_empty_live_list_is_treated_as_a_failure(monkeypatch):
    import anthropic

    class EmptyModels:
        def list(self, **_kwargs):
            return []

    class FakeClient:
        def __init__(self, **_kwargs):
            self.models = EmptyModels()

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)

    assert model_catalog.available_models()["source"] == "builtin"
