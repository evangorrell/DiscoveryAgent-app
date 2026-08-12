from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from manager.discoveryagent_cli.errors import ActiveResearchError
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

    def test_managed_and_legacy_live_rounds_are_detected_read_only(self) -> None:
        data = self.root / "data"
        managed = data / "projects" / "prj_managed"
        config = self._project(managed, "prj_managed", registry=data / "v2_projects.json")
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
        legacy_root = data / "legacy" / "local_disk" / "legacy-project"
        legacy_config = self._project(
            legacy_root,
            "legacy-project",
            registry=data / "legacy" / "local_disk" / "v2_projects.json",
        )
        self._live_state(legacy_root, "legacy-project", legacy_config)
        self.assertIs(
            inspect_research_activity(
                data, platform_name="darwin", process_probe=self._child_probe
            ).activity,
            ResearchActivity.ACTIVE,
        )

    def test_stale_dead_pid_does_not_block_and_one_active_among_many_does(self) -> None:
        data = self.root / "data"
        stale = data / "projects" / "prj_stale"
        config = self._project(stale, "prj_stale", registry=data / "v2_projects.json")
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
        registry = json.loads((data / "v2_projects.json").read_text())
        registry["projects"].append({"id": "prj_active", "project_root": str(active)})
        (data / "v2_projects.json").write_text(json.dumps(registry), encoding="utf-8")
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
            registry=Path(state.data_root) / "v2_projects.json",
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


class FrontendMaintenanceTests(unittest.TestCase):
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
