"""Exact-round compatibility routing regressions for V2 Round Workspace."""
from da_selection_ui import app as app_module


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_get_disk_path", lambda: tmp_path)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s["authenticated"] = True
    return client


def _create(client, name="Optic Nerve Regeneration"):
    resp = client.post(
        "/api/projects",
        data={
            "project_name": name,
            "task_description": "Identify testable regeneration hypotheses.",
            "dataset_context": "ctx",
        },
        content_type="multipart/form-data",
    )
    return resp.get_json()["project_id"]


def _rounds_root(tmp_path, pid):
    return tmp_path / pid / "checkpoint" / "checkpoints"


def _write_completed_round(tmp_path, pid, n, finding):
    rd = _rounds_root(tmp_path, pid) / f"round_{n}"
    rd.mkdir(parents=True, exist_ok=True)
    rd.joinpath("config.json").write_text('{"config": {"task_description": "x"}}', encoding="utf-8")
    rd.joinpath("round_report.md").write_text(
        f"# Round {n} Report\n\n## Round Report\n\n{finding}\n", encoding="utf-8"
    )
    return rd


def _write_running_round(tmp_path, pid, n):
    rd = _rounds_root(tmp_path, pid) / f"round_{n}"
    rd.mkdir(parents=True, exist_ok=True)
    rd.joinpath("config.json").write_text('{"config": {"task_description": "x"}}', encoding="utf-8")
    (rd / "query_000").mkdir()
    return rd


# --- (8) three distinct rounds, unmistakably different content --------------

def test_three_distinct_completed_rounds_redirect_to_their_exact_v2_state(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)
    _write_completed_round(tmp_path, pid, 1, "ROUND ONE UNIQUE FINDING")
    _write_completed_round(tmp_path, pid, 2, "ROUND TWO UNIQUE FINDING")
    _write_completed_round(tmp_path, pid, 3, "ROUND THREE UNIQUE FINDING")

    for round_number in (1, 2, 3):
        response = client.get(
            f"/legacy/projects/optic-nerve-regeneration/rounds/{round_number}"
        )
        assert response.status_code == 302
        assert response.headers["Location"] == (
            "/rounds/?project=optic-nerve-regeneration"
            f"&round={round_number}&review=1"
        )


# --- (9) session-state independence ------------------------------------------

def test_requested_round_wins_regardless_of_session_active_round(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)
    _write_completed_round(tmp_path, pid, 1, "ROUND ONE UNIQUE FINDING")
    _write_completed_round(tmp_path, pid, 3, "ROUND THREE UNIQUE FINDING")

    # Session "active" checkpoint points at round 3.
    with client.session_transaction() as s:
        s["project_config"]["checkpoint_dir"] = str(_rounds_root(tmp_path, pid) / "round_3")
    response = client.get("/legacy/projects/optic-nerve-regeneration/rounds/1")
    assert "round=1" in response.headers["Location"]
    assert "round=3" not in response.headers["Location"]

    # Flip: session "active" checkpoint now points at round 1.
    with client.session_transaction() as s:
        s["project_config"]["checkpoint_dir"] = str(_rounds_root(tmp_path, pid) / "round_1")
    response = client.get("/legacy/projects/optic-nerve-regeneration/rounds/3")
    assert "round=3" in response.headers["Location"]
    assert "round=1" not in response.headers["Location"]


def test_report_view_never_touches_the_session_generator(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)
    _write_completed_round(tmp_path, pid, 1, "ROUND ONE UNIQUE FINDING")

    calls = []
    monkeypatch.setattr(app_module, "get_or_restore_generator", lambda *a, **k: calls.append(1) or None)
    resp = client.get("/legacy/projects/optic-nerve-regeneration/rounds/1")
    assert resp.status_code == 302
    assert calls == []  # the round-scoped report path never asks for a generator


# --- (10) stale running-looking directory alongside a genuinely active one --

def test_stale_running_round_does_not_leak_active_round_content(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)
    _write_running_round(tmp_path, pid, 2)  # looks running, but is stale/abandoned
    _write_running_round(tmp_path, pid, 4)  # the genuinely active round

    resp = client.get("/legacy/projects/optic-nerve-regeneration/rounds/2")
    assert resp.status_code == 302
    assert resp.headers["Location"] == (
        "/rounds/?project=optic-nerve-regeneration&round=2"
    )


def test_stale_round_polling_endpoint_stays_pinned(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)
    _write_running_round(tmp_path, pid, 2)
    _write_running_round(tmp_path, pid, 4)

    client.get("/projects/optic-nerve-regeneration")
    payload = client.get("/api/round_status?round=2").get_json()
    assert payload["success"] is True
    assert payload["active_round_path"].endswith("round_2")

    payload4 = client.get("/api/round_status?round=4").get_json()
    assert payload4["active_round_path"].endswith("round_4")


def test_unpinned_round_status_keeps_legacy_sibling_scan_behavior(monkeypatch, tmp_path):
    """Without ?round=N, /api/round_status is unchanged: it still scans for
    whichever round is genuinely active (round 4 here), proving the pin is
    additive and does not alter the retained status API behavior."""
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)
    _write_running_round(tmp_path, pid, 2)
    _write_running_round(tmp_path, pid, 4)

    with client.session_transaction() as s:
        s["project_config"]["checkpoint_dir"] = str(_rounds_root(tmp_path, pid) / "round_2")
    payload = client.get("/api/round_status").get_json()
    assert payload["active_round_path"].endswith("round_4")


# --- historical (non-latest) completed round uses the SAME rendering path ---

def test_historical_completed_round_redirects_to_exact_v2_review(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    pid = _create(client)
    _write_completed_round(tmp_path, pid, 1, "ROUND ONE UNIQUE FINDING")
    _write_completed_round(tmp_path, pid, 2, "ROUND TWO UNIQUE FINDING")  # latest completed

    response = client.get("/legacy/projects/optic-nerve-regeneration/rounds/1")
    assert response.status_code == 302
    assert response.headers["Location"] == (
        "/rounds/?project=optic-nerve-regeneration&round=1&review=1"
    )
