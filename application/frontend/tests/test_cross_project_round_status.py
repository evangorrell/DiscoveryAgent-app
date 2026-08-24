import json
from pathlib import Path

import pytest

from da_selection_ui import app as app_module
from da_selection_ui import local_round_runner as runner


class NoopThread:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass


class LiveProcess:
    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None


def make_project(tmp_path, name):
    root = tmp_path / name
    code = root / "code"
    round_dir = root / "checkpoint" / "checkpoints" / "round_0"
    (code / "data").mkdir(parents=True)
    round_dir.mkdir(parents=True)
    readme = code / "README.md"
    readme.write_text(f"Context for {name}.", encoding="utf-8")
    (round_dir / "config.json").write_text(
        json.dumps({"config": {"num_queries": 5}}), encoding="utf-8",
    )
    return {
        "id": f"id-{name}",
        "url_slug": name,
        "display_name": name.title(),
        "task_description": f"Study {name}.",
        "project_root": str(root),
        "code_dir": str(code),
        "checkpoint_dir": str(round_dir),
        "readme_path": str(readme),
    }


def backend_environment(tmp_path):
    root = tmp_path / "backend"
    python = root / ".venv" / "bin" / "python"
    launcher = root / "scripts" / "run_hb_sherlock.py"
    python.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o755)
    launcher.write_text("", encoding="utf-8")
    return {
        "ANTHROPIC_API_KEY": "test-only",
        "DISCOVERYAGENT_BACKEND_ROOT": str(root),
        "DISCOVERYAGENT_BACKEND_PYTHON": str(python),
        "PATH": "",
    }


@pytest.fixture(autouse=True)
def clean_process_registries():
    runner._PROCESSES.clear()
    runner._PROCESS_RUN_IDS.clear()
    yield
    runner._PROCESSES.clear()
    runner._PROCESS_RUN_IDS.clear()


def test_two_projects_run_concurrently_and_stopping_b_cannot_contaminate_a(
    monkeypatch, tmp_path,
):
    project_a = make_project(tmp_path, "alpha")
    project_b = make_project(tmp_path, "beta")
    env = backend_environment(tmp_path)
    processes = iter((LiveProcess(31001), LiveProcess(31002)))
    live_pids = {31001, 31002}
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: next(processes))
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(
        runner, "_pid_matches_state",
        lambda state: state.get("pid") in live_pids and state.get("status") in {"starting", "running"},
    )

    run_a = runner.start_round(project_a, env=env)
    run_b = runner.start_round(project_b, env=env)

    assert run_a["run_id"] != run_b["run_id"]
    assert runner.round_status(project_a)["status"] == "running"
    assert runner.round_status(project_b)["status"] == "running"
    assert len(runner._PROCESSES) == 2

    # Mismatched stop identities are rejected before process termination.
    with pytest.raises(runner.RoundLaunchError) as wrong_round:
        runner.stop_round(
            project_b, expected_round=1, expected_run_id=run_b["run_id"],
        )
    assert wrong_round.value.code == "round_identity_mismatch"
    with pytest.raises(runner.RoundLaunchError) as wrong_run:
        runner.stop_round(
            project_b, expected_round=0, expected_run_id=run_a["run_id"],
        )
    assert wrong_run.value.code == "run_identity_mismatch"

    def terminate(pid, _process):
        assert pid == run_b["pid"]
        live_pids.remove(pid)
        return -15

    monkeypatch.setattr(runner, "_terminate_process_group", terminate)
    stopped_b = runner.stop_round(
        project_b, expected_round=run_b["round"], expected_run_id=run_b["run_id"],
    )
    assert stopped_b["project_id"] == project_b["id"]
    assert stopped_b["run_id"] == run_b["run_id"]
    assert stopped_b["status"] == "interrupted"

    # A remains the same live run in the shared runner, Project Home adapter,
    # and exact Round Workspace status interpretation.
    status_a = runner.round_status(project_a)
    assert status_a["status"] == "running"
    assert status_a["run_id"] == run_a["run_id"]
    assert runner._PROCESSES[str(Path(project_a["project_root"]).resolve())].poll() is None
    with app_module.app.test_request_context("/"):
        home_a = app_module._v2_project_state_for(
            project_a["checkpoint_dir"], project_a["display_name"],
            project_a["task_description"], project_a["url_slug"], project_a["id"],
        )
        home_b = app_module._v2_project_state_for(
            project_b["checkpoint_dir"], project_b["display_name"],
            project_b["task_description"], project_b["url_slug"], project_b["id"],
        )
        card_a = app_module._v2_project_summary(
            project_a["id"], project_a["display_name"], "",
            project_a["checkpoint_dir"], project_a["task_description"],
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
            url_slug=project_a["url_slug"],
        )
        card_b = app_module._v2_project_summary(
            project_b["id"], project_b["display_name"], "",
            project_b["checkpoint_dir"], project_b["task_description"],
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
            url_slug=project_b["url_slug"],
        )
    assert home_a["research_status"]["state"] == "research_in_progress"
    assert home_a["research_status"]["run_id"] == run_a["run_id"]
    assert home_b["research_status"]["state"] == "research_stopped"
    assert card_a["status_label"] == "In Progress"
    assert card_b["status_label"] == "Stopped"
    round_a = Path(project_a["checkpoint_dir"])
    assert app_module._round_workspace_round_status(
        0, round_a, project_id=project_a["id"],
    ) == "running"

    log_a = round_a / "live_round.log"
    log_a.write_text("before stop B\n", encoding="utf-8")
    with log_a.open("a", encoding="utf-8") as handle:
        handle.write("after stop B\n")
    assert log_a.read_text(encoding="utf-8").endswith("after stop B\n")


def test_late_monitor_cannot_overwrite_or_untrack_a_newer_same_project_run(
    monkeypatch, tmp_path,
):
    project = make_project(tmp_path, "alpha")
    paths = runner._project_paths(project)
    paths.update({
        "round": 0,
        "target_dir": Path(project["checkpoint_dir"]),
        "lock_file": Path(project["project_root"]) / "checkpoint" / ".round_0.lock",
    })
    paths["lock_file"].touch()
    old_process = LiveProcess(32001)
    new_process = LiveProcess(32002)
    old_run = "a" * 32
    new_run = "b" * 32
    state = {
        "schema_version": 2, "project_id": project["id"], "round": 0,
        "run_id": new_run, "status": "running", "pid": new_process.pid,
        "started_at": "2026-01-01T00:00:00+00:00",
        "config_file": str(Path(project["checkpoint_dir"]) / "config.json"),
    }
    paths["state_file"].write_text(json.dumps(state), encoding="utf-8")
    key = str(Path(project["project_root"]).resolve())
    runner._PROCESSES[key] = new_process
    runner._PROCESS_RUN_IDS[key] = new_run

    class Exited:
        def wait(self):
            return 1

    runner._monitor_process(project, paths, Exited(), old_run)

    assert json.loads(paths["state_file"].read_text(encoding="utf-8")) == state
    assert runner._PROCESSES[key] is new_process
    assert runner._PROCESS_RUN_IDS[key] == new_run
    assert paths["lock_file"].exists()


def test_completion_or_failure_in_one_project_does_not_change_another(
    monkeypatch, tmp_path,
):
    project_a = make_project(tmp_path, "alpha")
    project_b = make_project(tmp_path, "beta")
    for project, run_id, pid in ((project_a, "a" * 32, 33001), (project_b, "b" * 32, 33002)):
        root = Path(project["project_root"])
        state = {
            "schema_version": 2, "project_id": project["id"], "round": 0,
            "run_id": run_id, "status": "running", "pid": pid,
            "started_at": "2026-01-01T00:00:00+00:00",
            "config_file": str(Path(project["checkpoint_dir"]) / "config.json"),
        }
        (root / "checkpoint" / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(runner, "_pid_matches_state", lambda state: state.get("pid") == 33002)
    paths_a = runner._project_paths(project_a)
    paths_a.update({
        "round": 0, "target_dir": Path(project_a["checkpoint_dir"]),
        "lock_file": Path(project_a["project_root"]) / "checkpoint" / ".round_0.lock",
    })

    class Failed:
        def wait(self):
            return 7

    runner._monitor_process(project_a, paths_a, Failed(), "a" * 32)

    assert runner.round_status(project_a)["status"] == "failed"
    assert runner.round_status(project_b)["status"] == "running"
