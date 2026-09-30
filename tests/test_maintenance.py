from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from manager.discoveryagent_cli.errors import ActiveResearchError, MaintenanceError
from manager.discoveryagent_cli.maintenance import (
    ProcessIdentity,
    ResearchActivity,
    inspect_process_identity,
    inspect_research_activity,
    require_idle_research,
    stop_idle_frontend,
)
from manager.discoveryagent_cli.runtime_state import (
    FRONTEND_RECORD_SCHEMA_VERSION,
    FrontendRuntimeRecord,
    write_frontend_record_atomic,
)
from manager.discoveryagent_cli.testing import create_fake_installation


class ActiveResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.children: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
        self.temporary.cleanup()

    def _project(self, project_root: Path, project_id: str, *, registry: Path) -> Path:
        checkpoint = project_root / "checkpoint"
        config = checkpoint / "checkpoints" / "round_0" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text("{}\n", encoding="utf-8")
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(
            json.dumps({"projects": [{"id": project_id, "project_root": str(project_root)}]}),
            encoding="utf-8",
        )
        return config

    def _live_state(self, project_root: Path, project_id: str, config: Path) -> subprocess.Popen:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", "run_hb_sherlock.py", str(config)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.children.append(child)
        checkpoint = project_root / "checkpoint"
        (checkpoint / "run_state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "project_id": project_id,
                    "round": 0,
                    "status": "starting",
                    "pid": child.pid,
                    "backend_root": str(self.root / "backend"),
                    "config_file": str(config),
                }
            ),
            encoding="utf-8",
        )
        (checkpoint / ".round_0.lock").write_text("", encoding="utf-8")
        return child

    def _child_probe(self, pid, _config, _launcher, _platform):
        child = next((candidate for candidate in self.children if candidate.pid == pid), None)
        if child is None or child.poll() is not None:
            return ProcessIdentity.DEAD
        return ProcessIdentity.MATCH

    def test_managed_and_imported_live_rounds_are_detected_from_one_registry(self) -> None:
        data = self.root / "data"
        managed = data / "projects" / "managed-name"
        registry = data / "projects.json"
        config = self._project(managed, "prj_managed", registry=registry)
        self._live_state(managed, "prj_managed", config)
        before = {path: path.read_bytes() for path in data.rglob("*") if path.is_file()}
        result = inspect_research_activity(
            data, platform_name="darwin", process_probe=self._child_probe
        )
        after = {path: path.read_bytes() for path in data.rglob("*") if path.is_file()}
        self.assertIs(result.activity, ResearchActivity.ACTIVE)
        self.assertEqual(before, after)

        self.children[0].terminate()
        self.children[0].wait(timeout=5)
        imported_root = self.root / "external" / "imported-project"
        imported_config = imported_root / "checkpoint/checkpoints/round_0/config.json"
        imported_config.parent.mkdir(parents=True)
        imported_config.write_text("{}\n", encoding="utf-8")
        payload = json.loads(registry.read_text(encoding="utf-8"))
        payload["projects"].append({
            "id": "legacy-project-123456",
            "project_root": str(imported_root),
            "imported_in_place": True,
        })
        registry.write_text(json.dumps(payload), encoding="utf-8")
        self._live_state(imported_root, "legacy-project-123456", imported_config)
        self.assertIs(
            inspect_research_activity(
                data, platform_name="darwin", process_probe=self._child_probe
            ).activity,
            ResearchActivity.ACTIVE,
        )

    def test_retired_registries_and_unregistered_directories_are_ignored(self) -> None:
        data = self.root / "data"
        retired_root = data / "projects" / "prj_retired"
        config = self._project(
            retired_root, "prj_retired", registry=data / "v2_projects.json",
        )
        self._live_state(retired_root, "prj_retired", config)
        result = inspect_research_activity(
            data, platform_name="darwin", process_probe=self._child_probe
        )
        self.assertIs(result.activity, ResearchActivity.IDLE)
        self.assertEqual(result.state_paths, ())

    def test_stale_dead_pid_does_not_block_and_one_active_among_many_does(self) -> None:
        data = self.root / "data"
        stale = data / "projects" / "prj_stale"
        config = self._project(stale, "prj_stale", registry=data / "projects.json")
        checkpoint = stale / "checkpoint"
        (checkpoint / "run_state.json").write_text(
            json.dumps({
                "schema_version": 1, "project_id": "prj_stale", "round": 0,
                "status": "running", "pid": 99999999,
                "backend_root": str(self.root / "backend"), "config_file": str(config),
            }),
            encoding="utf-8",
        )
        (checkpoint / ".round_0.lock").write_text("", encoding="utf-8")
        self.assertIs(
            inspect_research_activity(
                data, platform_name="darwin", process_probe=self._child_probe
            ).activity,
            ResearchActivity.IDLE,
        )

        active = data / "projects" / "prj_active"
        active_config = active / "checkpoint/checkpoints/round_0/config.json"
        active_config.parent.mkdir(parents=True)
        active_config.write_text("{}", encoding="utf-8")
        registry = json.loads((data / "projects.json").read_text())
        registry["projects"].append({"id": "prj_active", "project_root": str(active)})
        (data / "projects.json").write_text(json.dumps(registry), encoding="utf-8")
        self._live_state(active, "prj_active", active_config)
        self.assertIs(
            inspect_research_activity(
                data, platform_name="darwin", process_probe=self._child_probe
            ).activity,
            ResearchActivity.ACTIVE,
        )

    def test_live_lock_with_unverifiable_process_is_ambiguous_and_refused(self) -> None:
        paths, state = create_fake_installation(self.root / "install")
        project = Path(state.data_root) / "projects" / "prj_ambiguous"
        config = self._project(
            project,
            "prj_ambiguous",
            registry=Path(state.data_root) / "projects.json",
        )
        checkpoint = project / "checkpoint"
        (checkpoint / "run_state.json").write_text(
            json.dumps({
                "schema_version": 1, "project_id": "prj_ambiguous", "round": 0,
                "status": "starting", "pid": 42,
                "backend_root": str(self.root / "backend"), "config_file": str(config),
            }),
            encoding="utf-8",
        )
        (checkpoint / ".round_0.lock").write_text("", encoding="utf-8")
        inspection = inspect_research_activity(
            Path(state.data_root),
            platform_name="darwin",
            process_probe=lambda *_args: ProcessIdentity.UNKNOWN,
        )
        self.assertIs(inspection.activity, ResearchActivity.AMBIGUOUS)
        with self.assertRaisesRegex(ActiveResearchError, "before updating"):
            require_idle_research(
                state,
                action="updating",
                inspector=lambda *_args, **_kwargs: inspection,
            )

    def test_process_identity_uses_exact_launcher_and_config_arguments(self) -> None:
        completed = subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                "/runtime/python /release/scripts/run_hb_sherlock.py "
                "--config-file /data/config file.json --num_rounds 1\n"
            ),
            stderr="",
        )
        identity = inspect_process_identity(
            42,
            "/data/config file.json",
            "/release/scripts/run_hb_sherlock.py",
            "darwin",
            pid_alive=lambda _pid: True,
            runner=lambda *_args, **_kwargs: completed,
        )
        self.assertIs(identity, ProcessIdentity.MATCH)

    def test_schema_two_and_terminal_resumability_matrix(self):
        data = self.root / 'data'
        project = data / 'projects' / 'test'
        config = self._project(project, 'test', registry=data / 'projects.json')
        path = project / 'checkpoint/run_state.json'
        for schema in (1, 2):
            for status in ('interrupted', 'stopped', 'completed', 'failed', 'cancelled', 'not_started'):
                with self.subTest(schema=schema, status=status):
                    payload = dict(schema_version=schema, project_id='test', round=0,
                                   run_id='a' * 32, status=status, pid=99999999,
                                   backend_root=str(self.root / 'backend'), config_file=str(config), resumable=True)
                    path.write_text(json.dumps(payload))
                    (path.parent / '.round_0.lock').touch()
                    before = path.read_bytes()
                    result = inspect_research_activity(data, platform_name='darwin', process_probe=lambda *_: ProcessIdentity.DEAD)
                    self.assertIs(result.activity, ResearchActivity.IDLE)
                    self.assertEqual(path.read_bytes(), before)
                    # A matching live process always wins over a terminal label.
                    self.assertIs(inspect_research_activity(data, platform_name='darwin',
                                  process_probe=lambda *_: ProcessIdentity.MATCH).activity, ResearchActivity.ACTIVE)

    def test_starting_queued_stopping_unknown_and_schema_drift_fail_closed(self):
        data = self.root / 'data'
        project = data / 'projects' / 'test'
        self._project(project, 'test', registry=data / 'projects.json')
        path = project / 'checkpoint/run_state.json'
        for schema, status in [(2, s) for s in ('queued', 'starting', 'running', 'stopping', 'future-status')] + [(3, 'interrupted')]:
            with self.subTest(schema=schema, status=status):
                path.write_text(json.dumps(dict(schema_version=schema, status=status, pid=None)))
                self.assertIs(inspect_research_activity(data, platform_name='darwin').activity, ResearchActivity.AMBIGUOUS)


class FrontendMaintenanceTests(unittest.TestCase):
    def test_unverifiable_running_app_requests_close_not_stop_round(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            paths, state = create_fake_installation(Path(temporary_name) / "install")
            record = FrontendRuntimeRecord(
                FRONTEND_RECORD_SCHEMA_VERSION, 4242, state.active_release_id,
                "http://127.0.0.1:43123/", "http://127.0.0.1:43123/api/health", time.time(),
            )
            write_frontend_record_atomic(paths.frontend_runtime_file, record)
            with self.assertRaises(MaintenanceError) as raised:
                stop_idle_frontend(paths, state, pid_alive=lambda _pid: True,
                                   health_probe=lambda _url: False)
            self.assertIn("Close the app and retry", str(raised.exception))
            self.assertNotIn("Stop the round", str(raised.exception))
            self.assertTrue(paths.frontend_runtime_file.exists())

    def test_verified_idle_frontend_is_stopped_and_matching_record_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            paths, state = create_fake_installation(Path(temporary_name) / "install")
            record = FrontendRuntimeRecord(
                FRONTEND_RECORD_SCHEMA_VERSION,
                4242,
                state.active_release_id,
                "http://127.0.0.1:43123/",
                "http://127.0.0.1:43123/api/health",
                time.time(),
            )
            write_frontend_record_atomic(paths.frontend_runtime_file, record)
            alive = {4242: True}
            calls = []

            def terminate(pid, _url, **_kwargs):
                calls.append(pid)
                alive[pid] = False

            stopped = stop_idle_frontend(
                paths,
                state,
                pid_alive=lambda pid: alive.get(pid, False),
                health_probe=lambda _url: True,
                terminator=terminate,
            )
            self.assertTrue(stopped)
            self.assertEqual(calls, [4242])
            self.assertFalse(paths.frontend_runtime_file.exists())


if __name__ == "__main__":
    unittest.main()
