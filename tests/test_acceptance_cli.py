import io
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

from manager.discoveryagent_cli.cli import main
from manager.discoveryagent_cli.installation import ReleaseMetadata
from manager.discoveryagent_cli.errors import ReleaseError
from manager.discoveryagent_cli.release_version import validate_release_version
from manager.discoveryagent_cli.terminal import local_url_link
from manager.discoveryagent_cli.testing import create_fake_installation
from manager.discoveryagent_cli.state import InstalledReleaseState, write_state_atomic
from release_builder import build_release, SourceRequest, ReleaseBuildError


class CliAcceptanceTests(unittest.TestCase):
    def test_semantic_version_and_verbose_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_fake_installation(Path(directory) / 'install')
            path = paths.release_root(state.active_release_id) / 'release.json'
            payload = json.loads(path.read_text())
            payload.update(release_version='1.0.0-rc.2', package_revision=0)
            path.write_text(json.dumps(payload))
            for args, verbose in [(['version'], False), (['version', '--verbose'], True)]:
                output = io.StringIO()
                self.assertEqual(main(args, install_root=paths.root, stdout=output, stderr=io.StringIO()), 0)
                lines = output.getvalue().splitlines()
                self.assertEqual(lines[0], 'DiscoveryAgent 1.0.0-rc.2')
                self.assertEqual(len(lines), 7 if verbose else 1)
                if verbose:
                    self.assertIn('Package:  0', lines)
                    self.assertIn('Release:  ' + payload['release_id'], lines)
            payload['package_revision'] = 7
            self.assertEqual(ReleaseMetadata.from_dict(payload).package_revision, 7)
            slot = ReleaseMetadata.from_dict(payload).installation_id
            self.assertEqual(slot, payload['release_id'] + '-pkg7')
            path.write_text(json.dumps(payload))
            path.parent.rename(paths.release_root(slot))
            write_state_atomic(paths.state_file, replace(state, active_release_id=slot,
                installed_releases={slot: InstalledReleaseState(path='releases/' + slot)}))
            output = io.StringIO()
            self.assertEqual(main(['version', '--verbose'], install_root=paths.root, stdout=output), 0)
            self.assertIn('Package:  7\n', output.getvalue())
            self.assertIn('Release:  ' + payload['release_id'] + '\n', output.getvalue())

    def test_legacy_and_malformed_version(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, state = create_fake_installation(Path(directory) / 'install')
            path = paths.release_root(state.active_release_id) / 'release.json'
            payload = json.loads(path.read_text())
            output = io.StringIO()
            self.assertEqual(main(['version'], install_root=paths.root, stdout=output), 0)
            self.assertEqual(output.getvalue(), 'DiscoveryAgent (version unavailable)\n')
            for value in (None, '', 1, 'v1.0.0', '1.0', '01.0.0', '1.0.0-01', '1.0.0\nunsafe'):
                with self.subTest(value=value):
                    payload['release_version'] = value
                    with self.assertRaises(ReleaseError):
                        ReleaseMetadata.from_dict(payload)
            path.write_text(json.dumps(payload))
            error = io.StringIO()
            self.assertNotEqual(main(['version'], install_root=paths.root, stdout=io.StringIO(), stderr=error), 0)
            self.assertIn('release_version must be a semantic version', error.getvalue())
            for value in ('1.0.0', '0.3.0-beta.2', '1.0.0-rc.1+build.3'):
                self.assertEqual(validate_release_version(value), value)

    def test_production_requires_explicit_semver_before_touching_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            source = SourceRequest(Path(directory) / 'absent', 'a' * 40)
            for version in (None, 'not-a-version'):
                with self.assertRaisesRegex(ReleaseBuildError, 'release_version'):
                    build_release(source, source, mode='production', package_revision=1,
                                  release_version=version, destination_root=Path(directory) / 'output')
            self.assertFalse((Path(directory) / 'output').exists())

    def test_terminal_url_is_copyable_and_preserves_dynamic_port(self):
        class Tty(io.StringIO):
            def isatty(self): return True
        url = 'http://127.0.0.1:52246/'
        for env in ({}, {'TERM_PROGRAM': 'Apple_Terminal'}, {'TERM_PROGRAM': 'iTerm.app', 'TERM': 'dumb'},
                    {'TERM_PROGRAM': 'iTerm.app', 'TMUX': 'session'}):
            self.assertEqual(local_url_link(url, Tty(), env), url)
        self.assertEqual(local_url_link(url, io.StringIO(), {'TERM_PROGRAM': 'iTerm.app'}), url)
        linked = local_url_link(url, Tty(), {'TERM_PROGRAM': 'iTerm.app'})
        self.assertEqual(linked, '\033]8;;' + url + '\033\\' + url + '\033]8;;\033\\')
        self.assertNotIn('\033', local_url_link(url + '\033', Tty(), {'TERM_PROGRAM': 'iTerm.app'}))
