"""Exact helper-query persistence for Round Activity Input details."""

import json

from AIR.utils import progress_recorder


def test_query_started_persists_exact_input_without_changing_bounded_objective(tmp_path):
    exact = "Compare the following cohorts:\n\n- treatment\n- control\n\n" + ("x" * 1400)

    progress_recorder.write_query_started(tmp_path, 7, objective=exact)

    status_path = tmp_path / "query_07" / "query_status.json"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["query_input"] == exact.strip()
    assert payload["objective"].endswith("... [truncated]")
    assert len(payload["objective"]) < len(payload["query_input"])


def test_later_status_updates_preserve_exact_query_input(tmp_path):
    exact = "Measure effect sizes.\nPreserve this second line exactly."
    progress_recorder.write_query_started(tmp_path, 3, objective=exact)

    progress_recorder.write_query_step(tmp_path, 3, "analyzing_results")
    progress_recorder.write_query_completed(tmp_path, 3, summary="Completed analysis.")

    payload = json.loads(
        (tmp_path / "query_03" / "query_status.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 2
    assert payload["query_input"] == exact
    assert payload["status"] == "completed"


def test_missing_input_is_represented_as_null_without_persisting_other_context(tmp_path):
    progress_recorder.write_query_started(tmp_path, 1)

    payload = json.loads(
        (tmp_path / "query_01" / "query_status.json").read_text(encoding="utf-8")
    )
    assert payload["query_input"] is None
    assert payload["objective"] == progress_recorder.DEFAULT_OBJECTIVE
