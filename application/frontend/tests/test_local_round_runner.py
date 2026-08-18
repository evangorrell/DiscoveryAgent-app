import json
import os
import re
import signal
import subprocess
import shutil
import venv
from pathlib import Path

import pytest

from da_selection_ui import local_round_runner as runner
from da_selection_ui import app as app_module


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    code = root / "code"
    (code / "data").mkdir(parents=True)
    readme = code / "README.md"
    readme.write_text("Local scientific context", encoding="utf-8")
    round_dir = root / "checkpoint" / "checkpoints" / "round_0"
    round_dir.mkdir(parents=True)
    (round_dir / "config.json").write_text('{"config": {"num_queries": 2}}', encoding="utf-8")
    return {
        "id": "project-1",
        "url_slug": "local-project",
        "task_description": "Find a reproducible relationship.",
        "project_root": str(root),
        "code_dir": str(code),
        "checkpoint_dir": str(round_dir),
        "readme_path": str(readme),
    }


@pytest.fixture
def backend(tmp_path):
    root = tmp_path / "backend"
    python = root / ".venv" / "bin" / "python"
    launcher = root / "scripts" / "run_hb_sherlock.py"
    python.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    launcher.write_text("", encoding="utf-8")
    return root, python, launcher


@pytest.fixture(autouse=True)
def clear_process_registry():
    runner._PROCESSES.clear()
    yield
    runner._PROCESSES.clear()


def launch_env(backend):
    root, python, _launcher = backend
    return {
        "ANTHROPIC_API_KEY": "test-secret-never-persist",
        "DISCOVERYAGENT_BACKEND_ROOT": str(root),
        "DISCOVERYAGENT_BACKEND_PYTHON": str(python),
        "PATH": "",
    }


class NoopThread:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        pass


class FakeProcess:
    pid = 43210

    def poll(self):
        return None


@pytest.mark.parametrize("launch", [runner.start_round, runner.resume_round])
@pytest.mark.parametrize("missing_value", [None, "", "   "])
def test_missing_environment_key_is_actionable_and_never_reaches_launcher(
    monkeypatch, project, backend, launch, missing_value,
):
    env = launch_env(backend)
    if missing_value is None:
        env.pop("ANTHROPIC_API_KEY")
    else:
        env["ANTHROPIC_API_KEY"] = missing_value
    monkeypatch.setattr(
        runner,
        "resolve_backend_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("credential preflight must run before launcher resolution")
        ),
    )
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("credential preflight must prevent process spawn")
        ),
    )
    with pytest.raises(runner.RoundLaunchError) as error:
        launch(project, env=env)
    assert error.value.code == "anthropic_api_key_missing"
    assert error.value.message.startswith("Anthropic API key not detected")
    assert "restart DiscoveryAgent" in error.value.message
    assert not (Path(project["project_root"]) / "checkpoint" / "run_state.json").exists()


def test_start_uses_backend_venv_and_secret_free_files(monkeypatch, project, backend):
    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return FakeProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    result = runner.start_round_zero(project, env=launch_env(backend))

    root, python, launcher = backend
    round_dir = Path(project["checkpoint_dir"])
    checkpoint_root = Path(project["project_root"]) / "checkpoint"
    assert result == {"project_id": "project-1", "round": 0, "status": "starting", "pid": 43210}
    assert captured["command"] == [str(python), str(launcher), "--config-file", str(round_dir / "config.json"), "--num_rounds", "1"]
    assert captured["kwargs"]["cwd"] == str(root)
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["env"]["ANTHROPIC_API_KEY"] == "test-secret-never-persist"

    config = json.loads((round_dir / "config.json").read_text())["config"]
    assert config["log_dir"] == str(checkpoint_root)
    assert config["checkpoint_base_dir"] == str(checkpoint_root / "checkpoints")
    assert config["existing_codebase"] == project["code_dir"]
    assert config["num_rounds"] == 1
    persisted = (round_dir / "config.json").read_text() + (checkpoint_root / "run_state.json").read_text()
    assert "test-secret-never-persist" not in persisted


def test_configured_backend_python_preserves_venv_symlink(monkeypatch, tmp_path, project):
    home = tmp_path / "home"
    release = home / "install" / "releases" / "release-1"
    backend_root = release / "application" / "backend"
    launcher = backend_root / "scripts" / "run_hb_sherlock.py"
    backend_venv = release / "envs" / "backend"
    backend_bin = backend_venv / "bin"
    backend_python = backend_bin / "python"
    base_bin = home / "install" / "runtime" / "python" / "bin"
    base_python = base_bin / "python3.11"

    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    backend_bin.mkdir(parents=True)
    (backend_venv / "pyvenv.cfg").write_text("home = runtime-python\n", encoding="utf-8")
    base_bin.mkdir(parents=True)
    base_python.write_text("#!/bin/sh\n", encoding="utf-8")
    base_python.chmod(0o755)
    backend_python.symlink_to(base_python)
    assert backend_python.is_symlink()
    assert backend_python.resolve() == base_python

    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return FakeProcess()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    environment = {
        "ANTHROPIC_API_KEY": "test-secret-never-persist",
        "DISCOVERYAGENT_BACKEND_ROOT": str(backend_root),
        "DISCOVERYAGENT_BACKEND_PYTHON": str(backend_python).replace(str(home), "~", 1),
        "PATH": "/usr/local/bin:/usr/bin",
    }

    resolved_root, resolved_python, resolved_launcher = runner.resolve_backend_runtime(environment)
    runner.start_round_zero(project, env=environment)

    assert (resolved_root, resolved_python, resolved_launcher) == (
        backend_root,
        backend_python,
        launcher,
    )
    assert captured["command"][0] == str(backend_python)
    assert str(base_python) not in captured["command"]
    child_environment = captured["kwargs"]["env"]
    assert child_environment["VIRTUAL_ENV"] == str(backend_venv)
    assert child_environment["PATH"].split(os.pathsep)[0] == str(backend_bin)
    assert child_environment["PATH"].split(os.pathsep)[0] != str(base_bin)


def test_child_environment_localizes_runtime_without_mutating_parent(monkeypatch, project, backend):
    root, _python, _launcher = backend
    project_root = Path(project["project_root"])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-secret")
    monkeypatch.setenv("AIR_DATA_DIR", "/lustre/old/data")
    monkeypatch.setenv("DATA_PATH", "/lustre/old/root")
    monkeypatch.setenv("CLAUDE_CODE_SKILLS_PATH", "/lustre/old/skills")
    monkeypatch.setenv("DISCOVERYAGENT_BACKEND_ROOT", "/lustre/old/backend")
    monkeypatch.setenv("CLAUDE_CODE_CLI_PATH", "/active/bin/claude")
    monkeypatch.setenv("NODE_PATH", "/active/lib/node_modules")
    monkeypatch.setenv("WRITING_AGENT_CLI_PATH", "/active/bin/writing-claude")
    monkeypatch.setenv("WRITING_AGENT_USE_USER_CLI", "1")
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.setenv("HOME", "/Users/scientist")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    parent_before = dict(os.environ)
    child = runner._child_environment(root, backend[1], project_root)

    assert child["ANTHROPIC_API_KEY"] == "parent-secret"
    assert child["PATH"].split(os.pathsep) == [str(backend[1].parent), "/usr/local/bin", "/usr/bin"]
    assert child["HOME"] == "/Users/scientist"
    assert child["LANG"] == "en_US.UTF-8"
    assert child["DATA_PATH"] == str(project_root)
    assert child["DISCOVERYAGENT_FRONTEND_LOCAL_ROUND"] == "1"
    assert child["PYTHONPATH"].split(os.pathsep)[0] == str(root)
    assert "AIR_DATA_DIR" not in child
    assert "CLAUDE_CODE_SKILLS_PATH" not in child
    assert "DISCOVERYAGENT_BACKEND_ROOT" not in child
    assert "CLAUDE_CODE_CLI_PATH" not in child
    assert "NODE_PATH" not in child
    assert "WRITING_AGENT_CLI_PATH" not in child
    assert "WRITING_AGENT_USE_USER_CLI" not in child
    assert dict(os.environ) == parent_before


@pytest.mark.parametrize("active_root", [
    "/current/local/project",
    "/scratch/current-user/discoveryagent_runs/current-project",
])
def test_child_environment_accepts_arbitrary_active_roots(active_root, tmp_path):
    source = {
        "ANTHROPIC_API_KEY": "secret",
        "AIR_DATA_DIR": "/lustre/old/data",
        "DATA_PATH": "/lustre/old/root",
        "PATH": "/usr/bin",
        "HOME": "/home/scientist",
        "LC_ALL": "C.UTF-8",
    }
    source_before = dict(source)
    child = runner._child_environment(
        tmp_path / "backend", tmp_path / "backend" / ".venv" / "bin" / "python",
        Path(active_root), source,
    )

    assert child["DATA_PATH"] == active_root
    assert "AIR_DATA_DIR" not in child
    assert child["ANTHROPIC_API_KEY"] == "secret"
    assert child["PATH"].split(os.pathsep) == [
        str(tmp_path / "backend" / ".venv" / "bin"), "/usr/bin",
    ]
    assert child["HOME"] == "/home/scientist"
    assert child["LC_ALL"] == "C.UTF-8"
    assert source == source_before


def test_child_environment_drops_all_external_claude_runtime_overrides(tmp_path):
    source = {
        "ANTHROPIC_API_KEY": "secret",
        "PATH": "",
        "CLAUDE_CODE_CLI_PATH": "/valid/or/stale/bin/claude",
        "NODE_PATH": "/valid/or/stale/node_modules",
        "WRITING_AGENT_CLI_PATH": "/valid/or/stale/bin/writing-claude",
        "WRITING_AGENT_USE_USER_CLI": "true",
    }

    child = runner._child_environment(
        tmp_path / "backend", tmp_path / "backend" / ".venv" / "bin" / "python",
        tmp_path / "project", source,
    )

    assert "CLAUDE_CODE_CLI_PATH" not in child
    assert "NODE_PATH" not in child
    assert "WRITING_AGENT_CLI_PATH" not in child
    assert "WRITING_AGENT_USE_USER_CLI" not in child


def test_child_environment_ignores_global_claude_without_executable_discovery(monkeypatch, tmp_path):
    global_bin = tmp_path / "global-bin"
    global_bin.mkdir()
    fake_claude = global_bin / "claude"
    fake_claude.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_claude.chmod(0o755)
    source = {
        "ANTHROPIC_API_KEY": "secret",
        "PATH": os.pathsep.join([str(global_bin), os.defpath]),
    }
    monkeypatch.setattr(
        runner.shutil,
        "which",
        lambda *_args, **_kwargs: pytest.fail("local-round environment must not discover claude"),
    )

    child = runner._child_environment(
        tmp_path / "backend", tmp_path / "backend-env" / "bin" / "python",
        tmp_path / "project", source,
    )

    assert str(global_bin) in child["PATH"].split(os.pathsep)
    assert "CLAUDE_CODE_CLI_PATH" not in child


def test_child_environment_makes_backend_venv_authoritative_for_tools(tmp_path):
    backend_root = tmp_path / "arbitrary-backend"
    backend_venv = backend_root / "runtime-env"
    venv.EnvBuilder(with_pip=True).create(backend_venv)
    backend_bin = backend_venv / "bin"
    backend_python = backend_bin / "python"

    frontend_bin = tmp_path / "frontend-env" / "bin"
    tool_bin = tmp_path / "ordinary-tools"
    frontend_bin.mkdir(parents=True)
    tool_bin.mkdir()
    for name in ("claude", "node", "git"):
        executable = tool_bin / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)

    source = {
        "ANTHROPIC_API_KEY": "inherited-secret",
        "PATH": os.pathsep.join([str(frontend_bin), str(tool_bin), os.defpath]),
        "VIRTUAL_ENV": str(frontend_bin.parent),
        "VIRTUAL_ENV_PROMPT": "(frontend-env) ",
        "PYTHONHOME": str(frontend_bin.parent),
        "PYTHONPATH": "/existing/pythonpath",
        "HOME": "/home/scientist",
    }
    source_before = dict(source)

    child = runner._child_environment(backend_root, backend_python, tmp_path / "project", source)

    assert child["PATH"].split(os.pathsep)[0] == str(backend_bin)
    assert str(frontend_bin) not in child["PATH"].split(os.pathsep)
    assert child["VIRTUAL_ENV"] == str(backend_venv)
    assert "VIRTUAL_ENV_PROMPT" not in child
    assert "PYTHONHOME" not in child
    assert child["ANTHROPIC_API_KEY"] == "inherited-secret"
    assert child["PYTHONPATH"].split(os.pathsep) == [str(backend_root), "/existing/pythonpath"]
    assert source == source_before

    for command in ("python", "python3"):
        result = subprocess.run(
            [command, "-c", "import sys; print(sys.executable)"],
            env=child, text=True, capture_output=True, check=True,
        )
        assert Path(result.stdout.strip()).resolve().is_relative_to(backend_venv.resolve())
    pip_result = subprocess.run(
        ["pip", "--version"], env=child, text=True, capture_output=True, check=True,
    )
    assert str(backend_venv.resolve()) in pip_result.stdout
    for executable in ("claude", "node", "git"):
        assert shutil.which(executable, path=child["PATH"]) == str(tool_bin / executable)


def test_duplicate_start_is_rejected(monkeypatch, project, backend):
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    runner.start_round_zero(project, env=launch_env(backend))
    monkeypatch.setattr(runner, "_pid_matches_state", lambda _state: True)
    with pytest.raises(runner.RoundLaunchError) as error:
        runner.start_round_zero(project, env=launch_env(backend))
    assert error.value.code == "round_already_running"


def test_stale_lock_is_reclaimed(monkeypatch, project, backend):
    lock = Path(project["project_root"]) / "checkpoint" / ".round_0.lock"
    lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    assert runner.start_round_zero(project, env=launch_env(backend))["status"] == "starting"


def test_spawn_failure_is_secret_free_and_unlocks(monkeypatch, project, backend):
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(runner.RoundLaunchError) as error:
        runner.start_round_zero(project, env=launch_env(backend))
    assert error.value.code == "subprocess_creation_failed"
    checkpoint_root = Path(project["project_root"]) / "checkpoint"
    assert not (checkpoint_root / ".round_0.lock").exists()
    assert "test-secret-never-persist" not in (checkpoint_root / "run_state.json").read_text()


def test_status_reconciles_native_completion(project):
    round_dir = Path(project["checkpoint_dir"])
    (round_dir / "memory_report.md").write_text("complete", encoding="utf-8")
    assert runner.round_zero_status(project)["status"] == "completed"


def test_frontend_reconstruction_keeps_live_backend_without_resume_event(monkeypatch, project):
    write_run_state(project, status="running", round_number=0, pid=43210)
    monkeypatch.setattr(runner, "_pid_matches_state", lambda _state: True)
    round_dir = Path(project["checkpoint_dir"])
    activity = round_dir / "round_activity.jsonl"
    activity.write_text(
        json.dumps({"timestamp": "2026-01-01T09:00:00Z", "event_type": "round_started"}),
        encoding="utf-8",
    )
    original = activity.read_text(encoding="utf-8")

    reconstructed_status = runner.round_status(project)
    events = app_module._build_round_activity_jsonl_events(round_dir)

    assert reconstructed_status["status"] == "running"
    assert reconstructed_status["pid"] == 43210
    assert [event["message"] for event in events] == ["Round started."]
    assert activity.read_text(encoding="utf-8") == original


def make_continuation(
    project, *, target_incomplete=False, source_round=3, query_budget=12,
):
    checkpoints = Path(project["project_root"]) / "checkpoint" / "checkpoints"
    source = checkpoints / f"round_{source_round}"
    source.mkdir(parents=True, exist_ok=True)
    source_config = {
        "config": {
            "analysis_name": "historical-analysis",
            "working_directory": "/lustre/old/code",
            "existing_codebase": "/lustre/old/code",
            "log_dir": "/lustre/old/logs",
            "checkpoint_base_dir": "/lustre/old/logs/checkpoints",
            "config_file": "/lustre/old/round_2/config.json",
            "_resume_memory_report_path": "/lustre/old/round_2/memory_report.md",
            "sherlock_data_dir": "/lustre/old",
            "task_description_file": "/lustre/old/task.txt",
            "num_queries": query_budget,
            "human_selection": True,
        }
    }
    (source / "config.json").write_text(json.dumps(source_config), encoding="utf-8")
    report = source / "round_report.md"
    memory = source / "memory_report.md"
    selection = source / "selected_hypothesis.md"
    report.write_text("complete", encoding="utf-8")
    memory.write_text("memory", encoding="utf-8")
    selection.write_text("**Hypothesis**: Continue locally.", encoding="utf-8")
    os.utime(report, (100, 100))
    os.utime(memory, (101, 101))
    os.utime(selection, (102, 102))
    if target_incomplete:
        target = checkpoints / f"round_{source_round + 1}"
        query = target / "query_000"
        query.mkdir(parents=True)
        (query / "checkpoint.json").write_text("{}", encoding="utf-8")
        (target / "config.json").write_text(json.dumps(source_config), encoding="utf-8")
    return source, selection


def test_fresh_round_zero_uses_exploratory_query_budget(monkeypatch, project, backend):
    config_file = Path(project["checkpoint_dir"]) / "config.json"
    config_file.unlink()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)

    result = runner.start_round(project, env=launch_env(backend))

    config = json.loads(config_file.read_text(encoding="utf-8"))["config"]
    assert result["round"] == 0
    assert runner.EXPLORATORY_ROUND_QUERY_BUDGET == 5
    assert config["num_queries"] == runner.EXPLORATORY_ROUND_QUERY_BUDGET


def _install_test_skill(project):
    skill = Path(project["code_dir"]) / ".claude" / "skills" / "test-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test instructions.\n---\n",
        encoding="utf-8",
    )
    return skill.parent


def test_round_zero_config_uses_current_project_skill_root(monkeypatch, project, backend):
    expected = _install_test_skill(project)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)

    runner.start_round(project, env=launch_env(backend))

    config = json.loads(Path(project["checkpoint_dir"]).joinpath("config.json").read_text())["config"]
    assert config["execution_skills_path"] == str(expected)


def test_round_zero_without_skills_removes_stale_skill_path(monkeypatch, project, backend):
    config_file = Path(project["checkpoint_dir"]) / "config.json"
    config_file.write_text(
        json.dumps({"config": {"execution_skills_path": "/stale/project/skills"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)

    runner.start_round(project, env=launch_env(backend))

    config = json.loads(config_file.read_text())["config"]
    assert "execution_skills_path" not in config


@pytest.mark.parametrize(
    ("source_round", "source_budget"),
    [(0, 5), (1, 12)],
)
def test_new_hypothesis_round_uses_product_query_budget(
    monkeypatch, project, backend, source_round, source_budget,
):
    source, _selection = make_continuation(
        project, source_round=source_round, query_budget=source_budget,
    )
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)

    result = runner.start_round(project, env=launch_env(backend))

    target = source.parent / f"round_{source_round + 1}" / "config.json"
    config = json.loads(target.read_text(encoding="utf-8"))["config"]
    assert result["round"] == source_round + 1
    assert runner.HYPOTHESIS_ROUND_QUERY_BUDGET == 12
    assert config["num_queries"] == runner.HYPOTHESIS_ROUND_QUERY_BUDGET


def test_continuation_uses_native_round_and_local_target_config(monkeypatch, project, backend):
    source, selection = make_continuation(project)
    captured = {}

    def fake_popen(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        return FakeProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    result = runner.start_round(project, env=launch_env(backend))
    target_config = source.parent / "round_4" / "config.json"
    assert result["round"] == 4
    assert captured["command"] == [
        str(backend[1]), str(backend[2]),
        "--config-file", str(target_config),
        "--num_rounds", "1",
        "--selected-hypothesis-file", str(selection),
        "--generate_plan",
    ]
    config = json.loads(target_config.read_text())["config"]
    assert config["working_directory"] == project["code_dir"]
    assert config["log_dir"] == str(Path(project["project_root"]) / "checkpoint")
    assert config["checkpoint_base_dir"] == str(source.parent)
    assert config["readme_path"] == project["readme_path"]
    assert config["task_description_file"] is None
    assert config["selected_hypothesis_file"] == str(selection)
    assert "/lustre/" not in target_config.read_text()


def test_continuation_recomputes_current_project_skill_root(monkeypatch, project, backend):
    expected = _install_test_skill(project)
    source, _selection = make_continuation(project)
    payload = json.loads((source / "config.json").read_text())
    payload["config"]["execution_skills_path"] = "/stale/project/skills"
    (source / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)

    runner.start_round(project, env=launch_env(backend))

    target = source.parent / "round_4" / "config.json"
    config = json.loads(target.read_text())["config"]
    assert config["execution_skills_path"] == str(expected)


def test_continuation_without_skills_drops_stale_inherited_path(monkeypatch, project, backend):
    source, _selection = make_continuation(project)
    payload = json.loads((source / "config.json").read_text())
    payload["config"]["execution_skills_path"] = "/stale/project/skills"
    (source / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)

    runner.start_round(project, env=launch_env(backend))

    target = source.parent / "round_4" / "config.json"
    assert "execution_skills_path" not in json.loads(target.read_text())["config"]


def test_reselection_is_the_exact_hypothesis_read_at_launch(monkeypatch, project, backend):
    source, selection = make_continuation(project)
    hypothesis_a = "**Hypothesis**: Candidate A."
    hypothesis_b = "**Hypothesis**: Candidate B, selected last."
    selection.write_text(hypothesis_a, encoding="utf-8")
    selection.write_text(hypothesis_b, encoding="utf-8")
    os.utime(selection, (103, 103))
    captured = {}

    def fake_popen(command, **_kwargs):
        selected_flag = command.index("--selected-hypothesis-file")
        selected_path = Path(command[selected_flag + 1])
        captured["path"] = selected_path
        captured["text_at_spawn"] = selected_path.read_text(encoding="utf-8")
        return FakeProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)

    result = runner.start_round(project, env=launch_env(backend))

    assert result["round"] == 4
    assert captured["path"] == source / "selected_hypothesis.md"
    assert captured["text_at_spawn"] == hypothesis_b
    target_config = json.loads(
        source.parent.joinpath("round_4", "config.json").read_text(encoding="utf-8")
    )["config"]
    assert target_config["selected_hypothesis_file"] == str(selection)


def test_continuation_requires_fresh_selection(project, backend):
    source, selection = make_continuation(project)
    os.utime(selection, (99, 99))
    with pytest.raises(runner.RoundLaunchError) as error:
        runner.start_round(project, env=launch_env(backend))
    assert error.value.code == "selected_hypothesis_missing"


def test_incomplete_native_next_round_uses_backend_resume(monkeypatch, project, backend):
    _source, _selection = make_continuation(project, target_incomplete=True)
    captured = {}
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    class Capture:
        pid = 43210
    monkeypatch.setattr(runner.subprocess, "Popen", lambda command, **_kwargs: (captured.update(command=command) or Capture()))
    assert runner.start_round(project, env=launch_env(backend))["round"] == 4
    assert "round_4/config.json" in captured["command"][3]


def test_duplicate_continuation_is_rejected(monkeypatch, project, backend):
    make_continuation(project)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    runner.start_round(project, env=launch_env(backend))
    monkeypatch.setattr(runner, "_pid_matches_state", lambda _state: True)
    with pytest.raises(runner.RoundLaunchError) as error:
        runner.start_round(project, env=launch_env(backend))
    assert error.value.code == "round_already_running"


def test_completed_continuation_is_not_relaunched(project, backend):
    source, _selection = make_continuation(project)
    target = source.parent / "round_4"
    target.mkdir()
    (target / "config.json").write_text("{}", encoding="utf-8")
    (target / "round_report.md").write_text("complete", encoding="utf-8")
    (target / "memory_report.md").write_text("memory", encoding="utf-8")
    with pytest.raises(runner.RoundLaunchError) as error:
        runner.start_round(project, env=launch_env(backend))
    assert error.value.code == "round_already_completed"


def write_run_state(project, *, status="running", round_number=0, pid=43210):
    state_file = Path(project["project_root"]) / "checkpoint" / "run_state.json"
    state_file.write_text(json.dumps({
        "schema_version": 1,
        "project_id": project["id"],
        "round": round_number,
        "status": status,
        "pid": pid,
        "started_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "exit_code": None,
        "config_file": str(Path(project["project_root"]) / "checkpoint" / "checkpoints" / f"round_{round_number}" / "config.json"),
    }), encoding="utf-8")
    return state_file


def test_stop_marks_interrupted_and_preserves_checkpoints(monkeypatch, project):
    round_dir = Path(project["checkpoint_dir"])
    query = round_dir / "query_001"
    query.mkdir()
    checkpoint = query / "checkpoint.json"
    checkpoint.write_text('{"native": true}', encoding="utf-8")
    state_file = write_run_state(project)
    runner._PROCESSES[str(Path(project["project_root"]).resolve())] = FakeProcess()
    monkeypatch.setattr(runner, "_pid_matches_state", lambda _state: True)
    terminated = []
    monkeypatch.setattr(runner, "_terminate_process_group", lambda pid, process: terminated.append((pid, process)) or -15)

    result = runner.stop_round(project)

    assert terminated and terminated[0][0] == 43210
    assert result["status"] == "interrupted"
    assert result["resumable"] is True
    assert checkpoint.read_text() == '{"native": true}'
    assert json.loads(state_file.read_text())["status"] == "interrupted"


def test_duplicate_stop_is_safe(monkeypatch, project):
    write_run_state(project, status="interrupted")
    first = runner.stop_round(project)
    second = runner.stop_round(project)
    assert first["status"] == second["status"] == "interrupted"


def test_read_only_status_inspector_matches_workspace_status(project):
    query = Path(project["checkpoint_dir"]) / "query_001"
    query.mkdir()
    (query / "checkpoint.json").write_text("{}", encoding="utf-8")
    state_file = write_run_state(project, status="interrupted")
    before = state_file.read_text(encoding="utf-8")

    inspected = runner.inspect_round_status(project["checkpoint_dir"])
    workspace = runner.round_status(project)

    assert inspected["round"] == workspace["round"] == 0
    assert inspected["status"] == workspace["status"] == "interrupted"
    assert inspected["resumable"] is workspace["resumable"] is True
    assert inspected["recorded_state"] is True
    assert "recorded_state" not in workspace
    assert state_file.read_text(encoding="utf-8") == before


def test_already_dead_recorded_process_becomes_interrupted(monkeypatch, project):
    class DeadProcess:
        def poll(self): return -15
    write_run_state(project)
    runner._PROCESSES[str(Path(project["project_root"]).resolve())] = DeadProcess()
    monkeypatch.setattr(runner, "_pid_matches_state", lambda _state: False)
    assert runner.stop_round(project)["status"] == "interrupted"


def test_unrelated_live_pid_is_never_terminated(monkeypatch, project):
    write_run_state(project)
    runner._PROCESSES[str(Path(project["project_root"]).resolve())] = FakeProcess()
    monkeypatch.setattr(runner, "_pid_matches_state", lambda _state: False)
    with pytest.raises(runner.RoundLaunchError) as error:
        runner.stop_round(project)
    assert error.value.code == "process_identity_mismatch"


def test_completed_round_cannot_be_stopped(project):
    write_run_state(project)
    (Path(project["checkpoint_dir"]) / "memory_report.md").write_text("done", encoding="utf-8")
    with pytest.raises(runner.RoundLaunchError) as error:
        runner.stop_round(project)
    assert error.value.code == "round_already_completed"


def test_resubmit_nonresumable_round_archives_attempt_and_restarts_same_round(monkeypatch, project, backend):
    round_dir = Path(project["checkpoint_dir"])
    (round_dir / "round_activity.jsonl").write_text('{"event":"round_started"}\n', encoding="utf-8")
    (round_dir / "partial.txt").write_text("preserved", encoding="utf-8")
    write_run_state(project, status="interrupted")
    captured = {}
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda command, **kwargs: (captured.update(command=command, kwargs=kwargs) or FakeProcess()),
    )

    result = runner.resubmit_round(project, env=launch_env(backend))

    assert result["round"] == 0 and result["status"] == "starting"
    assert result["resubmitted"] is True
    archives = list((Path(project["project_root"]) / "checkpoint" / "failed_runs").glob("round_0_*"))
    assert len(archives) == 1
    assert (archives[0] / "partial.txt").read_text(encoding="utf-8") == "preserved"
    assert not (round_dir / "partial.txt").exists()
    config = json.loads((round_dir / "config.json").read_text(encoding="utf-8"))["config"]
    assert config["num_queries"] == 2
    assert captured["command"][-2:] == ["--num_rounds", "1"]


def test_resubmit_rejects_round_with_native_checkpoint(project, backend):
    query = Path(project["checkpoint_dir"]) / "query_001"
    query.mkdir()
    (query / "checkpoint.json").write_text("{}", encoding="utf-8")
    write_run_state(project, status="interrupted")

    with pytest.raises(runner.RoundLaunchError) as error:
        runner.resubmit_round(project, env=launch_env(backend))

    assert error.value.code == "round_is_resumable"
    assert Path(project["checkpoint_dir"]).is_dir()


def test_resubmit_continuation_restarts_same_round_with_selected_hypothesis(monkeypatch, project, backend):
    source, selection = make_continuation(project)
    target = source.parent / "round_4"
    target.mkdir()
    (target / "config.json").write_text((source / "config.json").read_text(encoding="utf-8"), encoding="utf-8")
    (target / "round_activity.jsonl").write_text('{"event":"round_started"}\n', encoding="utf-8")
    write_run_state(project, status="interrupted", round_number=4)
    captured = {}
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda command, **kwargs: (captured.update(command=command, kwargs=kwargs) or FakeProcess()),
    )

    result = runner.resubmit_round(project, env=launch_env(backend))

    assert result["round"] == 4 and result["resubmitted"] is True
    assert captured["command"][-3:] == [
        "--selected-hypothesis-file", str(selection), "--generate_plan",
    ]
    assert not (source.parent / "round_5").exists()
    assert list((Path(project["project_root"]) / "checkpoint" / "failed_runs").glob("round_4_*"))


@pytest.mark.parametrize("query_budget", [5, 12])
def test_resubmit_continuation_preserves_archived_query_budget(
    monkeypatch, project, backend, query_budget,
):
    source, _selection = make_continuation(project, query_budget=query_budget)
    target = source.parent / "round_4"
    target.mkdir()
    target_config = target / "config.json"
    target_config.write_text((source / "config.json").read_text(encoding="utf-8"), encoding="utf-8")
    (target / "round_activity.jsonl").write_text('{"event":"round_started"}\n', encoding="utf-8")
    write_run_state(project, status="interrupted", round_number=4)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    result = runner.resubmit_round(project, env=launch_env(backend))

    archives = list(
        (Path(project["project_root"]) / "checkpoint" / "failed_runs").glob("round_4_*")
    )
    active = json.loads(target_config.read_text(encoding="utf-8"))["config"]
    archived = json.loads((archives[0] / "config.json").read_text(encoding="utf-8"))["config"]
    assert result["round"] == 4 and result["resubmitted"] is True
    assert len(archives) == 1
    assert active["num_queries"] == query_budget
    assert archived["num_queries"] == query_budget


def test_process_group_stop_uses_term_then_kill_on_timeout(monkeypatch):
    calls = []
    class SlowProcess:
        waits = 0
        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("backend", timeout)
            return -9
    monkeypatch.setattr(runner.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(runner.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    assert runner._terminate_process_group(43210, SlowProcess(), timeout=.01) == -9
    assert calls == [(43210, signal.SIGTERM), (43210, signal.SIGKILL)]


def test_process_group_stop_finishes_after_graceful_term(monkeypatch):
    calls = []
    class GracefulProcess:
        def wait(self, timeout):
            assert timeout == 3.0
            return -15
    monkeypatch.setattr(runner.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(runner.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    assert runner._terminate_process_group(43210, GracefulProcess()) == -15
    assert calls == [(43210, signal.SIGTERM)]


def test_resume_reuses_same_round_config_and_contains_no_secret(monkeypatch, project, backend):
    source, selection = make_continuation(project, target_incomplete=True)
    target = source.parent / "round_4"
    target_config = target / "config.json"
    target_payload = json.loads(target_config.read_text(encoding="utf-8"))
    target_payload["config"]["selected_hypothesis_file"] = str(selection)
    target_config.write_text(json.dumps(target_payload), encoding="utf-8")
    original_config = (target / "config.json").read_text()
    write_run_state(project, status="interrupted", round_number=4)
    captured = {}
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda command, **kwargs: (captured.update(command=command, kwargs=kwargs) or FakeProcess()))

    result = runner.resume_round(project, env=launch_env(backend))

    assert result["round"] == 4 and result["resumed"] is True
    assert captured["command"] == [str(backend[1]), str(backend[2]), "--config-file", str(target / "config.json"), "--num_rounds", "1"]
    assert "test-secret-never-persist" not in " ".join(captured["command"])
    assert captured["kwargs"]["env"]["ANTHROPIC_API_KEY"] == "test-secret-never-persist"
    assert (target / "config.json").read_text() == original_config
    assert json.loads(original_config)["config"]["selected_hypothesis_file"] == str(selection)
    assert not (source.parent / "round_5").exists()


def test_resume_applies_skill_imported_after_interruption(monkeypatch, project, backend):
    source, _selection = make_continuation(project, target_incomplete=True)
    target_config = source.parent / "round_4" / "config.json"
    expected = _install_test_skill(project)
    write_run_state(project, status="interrupted", round_number=4)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    runner.resume_round(project, env=launch_env(backend))

    config = json.loads(target_config.read_text(encoding="utf-8"))["config"]
    assert config["execution_skills_path"] == str(expected)
    assert config["num_queries"] == 12
    assert config["working_directory"] == "/lustre/old/code"


def test_resume_drops_skill_path_removed_after_interruption(monkeypatch, project, backend):
    source, _selection = make_continuation(project, target_incomplete=True)
    target_config = source.parent / "round_4" / "config.json"
    skill_root = _install_test_skill(project)
    payload = json.loads(target_config.read_text(encoding="utf-8"))
    payload["config"]["execution_skills_path"] = str(skill_root)
    target_config.write_text(json.dumps(payload), encoding="utf-8")
    shutil.rmtree(skill_root / "test-skill")
    write_run_state(project, status="interrupted", round_number=4)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    runner.resume_round(project, env=launch_env(backend))

    config = json.loads(target_config.read_text(encoding="utf-8"))["config"]
    assert "execution_skills_path" not in config
    assert config["num_queries"] == 12
    assert config["working_directory"] == "/lustre/old/code"


def test_resume_with_unchanged_skills_keeps_config_byte_identical(monkeypatch, project, backend):
    source, _selection = make_continuation(project, target_incomplete=True)
    target_config = source.parent / "round_4" / "config.json"
    skill_root = _install_test_skill(project)
    payload = json.loads(target_config.read_text(encoding="utf-8"))
    payload["config"]["execution_skills_path"] = str(skill_root)
    target_config.write_text(json.dumps(payload, indent=3), encoding="utf-8")
    original = target_config.read_text(encoding="utf-8")
    write_run_state(project, status="interrupted", round_number=4)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    runner.resume_round(project, env=launch_env(backend))

    assert target_config.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("query_budget", [5, 12])
def test_resume_preserves_existing_query_budget(monkeypatch, project, backend, query_budget):
    source, _selection = make_continuation(
        project, target_incomplete=True, query_budget=query_budget,
    )
    target_config = source.parent / "round_4" / "config.json"
    original_config = target_config.read_text(encoding="utf-8")
    write_run_state(project, status="interrupted", round_number=4)
    monkeypatch.setattr(runner.threading, "Thread", NoopThread)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())

    result = runner.resume_round(project, env=launch_env(backend))

    config = json.loads(target_config.read_text(encoding="utf-8"))["config"]
    assert result["round"] == 4 and result["resumed"] is True
    assert config["num_queries"] == query_budget
    assert target_config.read_text(encoding="utf-8") == original_config


def test_backend_cli_num_queries_default_remains_five():
    parser_source = (
        Path(runner.__file__).resolve().parents[2]
        / "discoveryagent" / "AIR" / "utils" / "arg_parsers.py"
    ).read_text(encoding="utf-8")

    assert re.search(
        r'add_argument\("--num_queries",\s*type=int,\s*default=5,',
        parser_source,
    )


def test_latest_native_checkpoint_matches_backend_numeric_semantics(project):
    round_dir = Path(project["checkpoint_dir"])
    for name in ("query_002", "query_010", "query_9"):
        query = round_dir / name
        query.mkdir()
        (query / "checkpoint.json").write_text("{}", encoding="utf-8")
    assert runner._latest_native_checkpoint(round_dir).parent.name == "query_010"


def test_resume_requires_checkpoint_and_rejects_completed_or_running(monkeypatch, project, backend):
    write_run_state(project, status="interrupted")
    with pytest.raises(runner.RoundLaunchError) as missing:
        runner.resume_round(project, env=launch_env(backend))
    assert missing.value.code == "round_not_resumable"

    (Path(project["checkpoint_dir"]) / "memory_report.md").write_text("done", encoding="utf-8")
    with pytest.raises(runner.RoundLaunchError) as completed:
        runner.resume_round(project, env=launch_env(backend))
    assert completed.value.code == "round_already_completed"

    (Path(project["checkpoint_dir"]) / "memory_report.md").unlink()
    write_run_state(project, status="running")
    monkeypatch.setattr(runner, "_pid_matches_state", lambda _state: True)
    with pytest.raises(runner.RoundLaunchError) as running:
        runner.resume_round(project, env=launch_env(backend))
    assert running.value.code == "round_already_running"
