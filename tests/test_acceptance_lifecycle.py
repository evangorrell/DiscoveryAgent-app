"""Exercise actual update/uninstall guards against frontend-compatible state."""
from functools import partial
import io
import json
from pathlib import Path
import subprocess
import unittest

from manager.discoveryagent_cli.errors import ActiveResearchError
from manager.discoveryagent_cli.maintenance import (
    require_idle_research, inspect_research_activity, ProcessIdentity,
)
from manager.discoveryagent_cli.uninstall import (
    uninstall_installed_application, UninstallDependencies, MacOSUninstallHandoff,
)
from manager.discoveryagent_cli.path_integration import PreparedPathRemoval


def write_research(state, status):
    data = Path(state.data_root)
    project = data / 'projects' / 'acceptance'
    checkpoint = project / 'checkpoint'
    query = checkpoint / 'checkpoints/round_0/query_001'
    query.mkdir(parents=True, exist_ok=True)
    (query / 'checkpoint.json').write_text('{"native":true}')
    (data / 'projects.json').write_text(json.dumps({'projects': [{'id': 'acceptance', 'project_root': str(project)}]}))
    (checkpoint / 'run_state.json').write_text(json.dumps(dict(
        schema_version=2, project_id='acceptance', round=0, run_id='a' * 32,
        status=status, pid=12345, backend_root='/test/backend', config_file=str(query.parent / 'config.json'),
    )))
    return checkpoint


def guard(identity):
    return partial(require_idle_research, inspector=partial(
        inspect_research_activity, process_probe=lambda *_: identity))


class PackageLifecycleAcceptanceTests(unittest.TestCase):
    def test_real_update_guard_state_matrix(self):
        from test_update_lifecycle import UpdateFlowTests
        for status in ('running', 'starting', 'stopping', 'interrupted', 'stopped', 'completed', 'failed', 'cancelled'):
            with self.subTest(status=status):
                fixture = UpdateFlowTests()
                fixture.setUp()
                try:
                    checkpoint = write_research(fixture.state, status)
                    before = {str(p): p.read_bytes() for p in checkpoint.rglob('*') if p.is_file()}
                    active = status in {'running', 'starting', 'stopping'}
                    deps = fixture.dependencies('dev-next', research_guard=guard(
                        ProcessIdentity.MATCH if active else ProcessIdentity.DEAD))
                    if active:
                        with self.assertRaises(ActiveResearchError): fixture.run_update(deps)
                        self.assertNotIn('install', fixture.events)
                    else:
                        self.assertEqual(fixture.run_update(deps), 0)
                        self.assertIn('install', fixture.events)
                    self.assertEqual(before, {str(p): p.read_bytes() for p in checkpoint.rglob('*') if p.is_file()})
                finally: fixture.tearDown()

    def test_real_uninstall_guard_state_matrix(self):
        from test_uninstall_lifecycle import UninstallFlowTests
        for status in ('running', 'starting', 'stopping', 'interrupted', 'stopped', 'completed', 'failed', 'cancelled'):
            with self.subTest(status=status):
                fixture = UninstallFlowTests()
                fixture.setUp()
                try:
                    checkpoint = write_research(fixture.state, status)
                    before = {str(p): p.read_bytes() for p in checkpoint.rglob('*') if p.is_file()}
                    active = status in {'running', 'starting', 'stopping'}
                    calls = []
                    deps = UninstallDependencies(
                        research_guard=guard(ProcessIdentity.MATCH if active else ProcessIdentity.DEAD),
                        frontend_stopper=lambda *_: False,
                        target_validator=lambda *_: (fixture.paths.root, Path(fixture.state.cli.path)),
                        path_preparer=lambda *_: PreparedPathRemoval(),
                        handoff=lambda *_: calls.append('handoff'))
                    if active:
                        with self.assertRaises(ActiveResearchError):
                            uninstall_installed_application(fixture.paths, stdout=fixture.stdout, stderr=fixture.stderr, dependencies=deps)
                        self.assertFalse(calls)
                    else:
                        self.assertEqual(uninstall_installed_application(fixture.paths, stdout=fixture.stdout, stderr=fixture.stderr, dependencies=deps), 0)
                        self.assertEqual(calls, ['handoff'])
                    self.assertEqual(before, {str(p): p.read_bytes() for p in checkpoint.rglob('*') if p.is_file()})
                finally: fixture.tearDown()

    def test_update_rechecks_research_after_download_and_frontend_stop(self):
        from test_update_lifecycle import UpdateFlowTests
        fixture = UpdateFlowTests()
        fixture.setUp()
        try:
            write_research(fixture.state, 'interrupted')
            identity = [ProcessIdentity.DEAD]
            check = partial(require_idle_research, inspector=partial(inspect_research_activity, process_probe=lambda *_: identity[0]))
            def race(*_): identity[0] = ProcessIdentity.MATCH
            deps = fixture.dependencies('dev-next', research_guard=check, frontend_stopper=race)
            with self.assertRaises(ActiveResearchError): fixture.run_update(deps)
            self.assertNotIn('install', fixture.events)
        finally: fixture.tearDown()

    def test_uninstall_reinstall_preserves_stopped_checkpoint_bytes(self):
        from test_production_installer import ProductionInstallerTests
        fixture = ProductionInstallerTests()
        fixture.setUp()
        try:
            installed = fixture.install_release()
            checkpoint = write_research(installed.state, 'interrupted')
            before = {str(p): p.read_bytes() for p in checkpoint.rglob('*') if p.is_file()}
            # Real external deletion helper, restricted to this disposable install.
            def exec_in_child(program, args):
                subprocess.run(args, executable=program, check=True, capture_output=True)
                raise SystemExit(0)  # A successful exec handoff never returns.
            deps = UninstallDependencies(research_guard=guard(ProcessIdentity.DEAD), frontend_stopper=lambda *_: False,
                path_preparer=lambda *_: PreparedPathRemoval(), handoff=MacOSUninstallHandoff(execv=exec_in_child))
            with self.assertRaises(SystemExit) as exited:
                uninstall_installed_application(installed.paths, stdout=io.StringIO(), stderr=io.StringIO(), dependencies=deps)
            self.assertEqual(exited.exception.code, 0)
            self.assertFalse(fixture.install.exists())
            fixture.install_release()
            self.assertEqual(before, {str(p): p.read_bytes() for p in checkpoint.rglob('*') if p.is_file()})
        finally: fixture.tearDown()
