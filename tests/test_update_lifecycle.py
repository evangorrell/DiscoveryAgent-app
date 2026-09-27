from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from manager.discoveryagent_cli.errors import UpdateError
from manager.discoveryagent_cli.state import InstalledReleaseState, load_state, write_state_atomic
from manager.discoveryagent_cli.testing import create_fake_installation
from manager.discoveryagent_cli.update import (
    UpdateDependencies,
    download_artifact,
    extract_install_artifact,
    fetch_manifest,
    run_new_installer,
    update_installed_application,
)
from manager.discoveryagent_cli.update_manifest import (
    INTERNAL_UPDATE_MANIFEST_URL_ENV,
    PUBLIC_UPDATE_MANIFEST_URL,
    parse_update_manifest,
    platform_key,
    update_manifest_url,
)


class UpdateManifestTests(unittest.TestCase):
    def manifest(self, *, release_type="production", url=None) -> bytes:
        filename = "discoveryagent-prod-new-macos-arm64.tar.gz"
        return json.dumps({
            "schema_version": 1,
            "channel": "stable",
            "release_id": "prod-new",
            "release_type": release_type,
            "release_tag": "v1.2.3",
            "platforms": {
                "macos-arm64": {
                    "artifact_filename": filename,
                    "artifact_url": url or f"https://github.com/owencqueen/DiscoveryAgent-app/releases/download/v1.2.3/{filename}",
                    "artifact_sha256": "a" * 64,
                }
            },
        }).encode()

    def test_schema_platform_and_public_url_contract(self) -> None:
        manifest = parse_update_manifest(self.manifest())
        self.assertEqual(manifest.release_id, "prod-new")
        self.assertEqual(platform_key("darwin", "arm64"), "macos-arm64")
        self.assertEqual(update_manifest_url({}), (PUBLIC_UPDATE_MANIFEST_URL, False))
        self.assertEqual(
            update_manifest_url({INTERNAL_UPDATE_MANIFEST_URL_ENV: "http://127.0.0.1/update.json"}),
            ("http://127.0.0.1/update.json", True),
        )
        with self.assertRaises(UpdateError):
            platform_key("win32", "arm64")

    def test_malformed_development_and_mutable_urls_are_rejected(self) -> None:
        with self.assertRaises(UpdateError):
            parse_update_manifest(b"{}")
        with self.assertRaisesRegex(UpdateError, "development"):
            parse_update_manifest(self.manifest(release_type="development"))
        parse_update_manifest(self.manifest(release_type="development"), internal=True)
        with self.assertRaisesRegex(UpdateError, "immutable"):
            parse_update_manifest(
                self.manifest(url="https://github.com/owencqueen/DiscoveryAgent-app/releases/latest/download/discoveryagent-prod-new-macos-arm64.tar.gz")
            )


class UpdateFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths, state = create_fake_installation(self.root / "install")
        self.state = replace(state, architecture="arm64")
        write_state_atomic(self.paths.state_file, self.state)
        self.output = io.StringIO()
        self.events: list[str] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def manifest(self, release_id: str) -> bytes:
        filename = f"discoveryagent-{release_id}-macos-arm64.tar.gz"
        return json.dumps({
            "schema_version": 1,
            "channel": "stable",
            "release_id": release_id,
            "release_type": "development",
            "release_tag": "internal-test",
            "platforms": {"macos-arm64": {
                "artifact_filename": filename,
                "artifact_url": f"http://127.0.0.1/{filename}",
                "artifact_sha256": "b" * 64,
            }},
        }).encode()

    def dependencies(self, release_id: str, **overrides) -> UpdateDependencies:
        payload = self.root / "payload"
        payload.mkdir(exist_ok=True)

        def downloader(_url, destination):
            self.events.append("download")
            destination.write_bytes(b"fixture")
            return "b" * 64

        def installer(_payload, _paths, old_state):
            self.events.append("install")
            releases = dict(old_state.installed_releases)
            releases[release_id] = InstalledReleaseState(path=f"releases/{release_id}")
            self.paths.release_root(release_id).mkdir(parents=True, exist_ok=True)
            write_state_atomic(
                self.paths.state_file,
                replace(old_state, active_release_id=release_id, installed_releases=releases),
            )

        values = {
            "manifest_fetcher": lambda _url: self.manifest(release_id),
            "artifact_downloader": downloader,
            "extractor": lambda _archive, _destination: payload,
            "payload_validator": lambda _payload: SimpleNamespace(
                release_id=release_id, release_type="development"
            ),
            "research_guard": lambda _state, **_kwargs: self.events.append("research"),
            "frontend_stopper": lambda _paths, _state: self.events.append("frontend") or False,
            "installer_runner": installer,
        }
        values.update(overrides)
        return UpdateDependencies(**values)

    def run_update(self, dependencies):
        return update_installed_application(
            self.paths,
            environ={INTERNAL_UPDATE_MANIFEST_URL_ENV: "http://127.0.0.1/update.json"},
            stdout=self.output,
            dependencies=dependencies,
        )

    def test_same_release_is_up_to_date_without_guard_download_or_frontend(self) -> None:
        forbidden = lambda *_args, **_kwargs: self.fail("same release must return immediately")
        dependencies = UpdateDependencies(
            manifest_fetcher=lambda _url: self.manifest(self.state.active_release_id),
            artifact_downloader=forbidden,
            research_guard=forbidden,
            frontend_stopper=forbidden,
            installer_runner=forbidden,
        )
        self.assertEqual(self.run_update(dependencies), 0)
        self.assertEqual(
            self.output.getvalue().splitlines(),
            ["Checking for updates...", "DiscoveryAgent is up to date."],
        )

    def test_different_release_orders_guard_download_stop_install_and_keeps_old(self) -> None:
        new_release = "dev-aaaaaaaaaaaa-bbbbbbbbbbbb"
        self.assertEqual(self.run_update(self.dependencies(new_release)), 0)
        self.assertEqual(self.events, ["research", "download", "frontend", "install"])
        state = load_state(self.paths.state_file)
        self.assertEqual(state.active_release_id, new_release)
        self.assertIn(self.state.active_release_id, state.installed_releases)
        self.assertIn("updated to " + new_release, self.output.getvalue())
        self.assertIn("Run `discoveryagent` to launch.", self.output.getvalue())

    def test_default_stable_update_downloads_public_assets_without_github_cli(self) -> None:
        release_id = "prod-aaaaaaaaaaaa-bbbbbbbbbbbb"
        archive = b"synthetic production package"
        manifest = json.loads(self.manifest(release_id))
        manifest.update(release_type="production", release_tag="v1.0.0")
        artifact = manifest["platforms"]["macos-arm64"]
        artifact["artifact_url"] = (
            "https://github.com/evangorrell/DiscoveryAgent-app/releases/download/"
            "v1.0.0/" + artifact["artifact_filename"]
        )
        artifact["artifact_sha256"] = hashlib.sha256(archive).hexdigest()
        dependencies = self.dependencies(
            release_id,
            manifest_fetcher=fetch_manifest,
            artifact_downloader=download_artifact,
            payload_validator=lambda _payload: SimpleNamespace(
                release_id=release_id, release_type="production"
            ),
        )
        with mock.patch(
            "manager.discoveryagent_cli.update.urllib.request.urlopen",
            side_effect=[io.BytesIO(json.dumps(manifest).encode()), io.BytesIO(archive)],
        ) as network, mock.patch(
            "manager.discoveryagent_cli.update.subprocess.run",
            side_effect=AssertionError("public update must not invoke gh"),
        ):
            # Exercise the default channel, with no internal manifest override.
            # Extraction and installation use the existing isolated state fixture.
            self.assertEqual(update_installed_application(
                self.paths, environ={}, stdout=self.output, dependencies=dependencies,
            ), 0)
        self.assertEqual(
            [call.args[0].full_url for call in network.call_args_list],
            [PUBLIC_UPDATE_MANIFEST_URL, artifact["artifact_url"]],
        )
        self.assertEqual(self.events, ["research", "frontend", "install"])
        state = load_state(self.paths.state_file)
        self.assertEqual(state.active_release_id, release_id)
        self.assertEqual(state.data_root, self.state.data_root)
        self.assertIn(self.state.active_release_id, state.installed_releases)

    def test_sha_and_install_failures_preserve_old_state(self) -> None:
        before = self.paths.state_file.read_bytes()
        bad_sha = self.dependencies(
            "dev-aaaaaaaaaaaa-bbbbbbbbbbbb",
            artifact_downloader=lambda _url, destination: destination.write_bytes(b"x") or "0" * 64,
        )
        with self.assertRaisesRegex(UpdateError, "SHA-256 mismatch"):
            self.run_update(bad_sha)
        self.assertEqual(self.paths.state_file.read_bytes(), before)

        failed = self.dependencies(
            "dev-aaaaaaaaaaaa-bbbbbbbbbbbb",
            installer_runner=lambda *_args: (_ for _ in ()).throw(UpdateError("injected failure")),
        )
        with self.assertRaisesRegex(UpdateError, "injected failure"):
            self.run_update(failed)
        self.assertEqual(self.paths.state_file.read_bytes(), before)

    def test_guard_refusal_prevents_download_frontend_and_install(self) -> None:
        forbidden = lambda *_args, **_kwargs: self.fail("maintenance continued after refusal")
        dependencies = self.dependencies(
            "dev-aaaaaaaaaaaa-bbbbbbbbbbbb",
            research_guard=lambda *_args, **_kwargs: (_ for _ in ()).throw(UpdateError("active")),
            artifact_downloader=forbidden,
            frontend_stopper=forbidden,
            installer_runner=forbidden,
        )
        with self.assertRaisesRegex(UpdateError, "active"):
            self.run_update(dependencies)

    def test_download_failure_propagates_before_frontend_stop(self) -> None:
        dependencies = self.dependencies(
            "dev-aaaaaaaaaaaa-bbbbbbbbbbbb",
            artifact_downloader=lambda *_args: (_ for _ in ()).throw(UpdateError("download failed")),
        )
        with self.assertRaisesRegex(UpdateError, "download failed"):
            self.run_update(dependencies)
        self.assertEqual(self.events, ["research"])


class ArchiveSafetyTests(unittest.TestCase):
    def test_traversal_links_and_special_entries_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            for index, member in enumerate((
                tarfile.TarInfo("../escape"),
                tarfile.TarInfo("discoveryagent-install/link"),
                tarfile.TarInfo("discoveryagent-install/fifo"),
                tarfile.TarInfo("discoveryagent-install/hard-link"),
            )):
                archive = root / f"unsafe-{index}.tar.gz"
                if index == 1:
                    member.type = tarfile.SYMTYPE
                    member.linkname = "/private/tmp/escape"
                if index == 2:
                    member.type = tarfile.FIFOTYPE
                if index == 3:
                    member.type = tarfile.LNKTYPE
                    member.linkname = "discoveryagent-install/target"
                with tarfile.open(archive, "w:gz") as bundle:
                    bundle.addfile(member)
                with self.assertRaises(UpdateError):
                    extract_install_artifact(archive, root / f"output-{index}")

    def test_contained_relative_symlink_from_real_artifact_contract_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            archive = root / "safe-link.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                contents = b"# installer fixture\n"
                info = tarfile.TarInfo(
                    "discoveryagent-install/tools/install_local_release.py"
                )
                info.size = len(contents)
                bundle.addfile(info, io.BytesIO(contents))
                link = tarfile.TarInfo(
                    "discoveryagent-install/application/backend/scripts/utils/"
                    "check_progress.py"
                )
                link.type = tarfile.SYMTYPE
                link.linkname = "internal_utils/check_progress.py"
                bundle.addfile(link)

            payload = extract_install_artifact(archive, root / "output")
            extracted_link = (
                payload
                / "application/backend/scripts/utils/check_progress.py"
            )
            self.assertTrue(extracted_link.is_symlink())
            self.assertEqual(
                extracted_link.readlink(),
                Path("internal_utils/check_progress.py"),
            )

    def test_new_installer_uses_owned_python_explicit_paths_and_no_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            paths, state = create_fake_installation(root / "install")
            payload = root / "payload"
            installer = payload / "tools" / "install_local_release.py"
            installer.parent.mkdir(parents=True)
            installer.write_text("", encoding="utf-8")
            captured = {}

            def fake_run(command, **kwargs):
                captured.update(command=command, kwargs=kwargs)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.dict(
                "manager.discoveryagent_cli.update.os.environ",
                {"ANTHROPIC_API_KEY": "secret", "PAPERCLIP_API_KEY": "secret", "PATH": "/usr/bin"},
                clear=True,
            ), mock.patch("manager.discoveryagent_cli.update.subprocess.run", side_effect=fake_run):
                run_new_installer(payload, paths, state)

            self.assertEqual(captured["command"][0], state.runtime.python_path)
            self.assertIn(str(installer), captured["command"])
            self.assertIn("--install-root", captured["command"])
            self.assertIn("--cli-path", captured["command"])
            self.assertIn("--data-root", captured["command"])
            self.assertNotIn("shell", captured["kwargs"])
            self.assertNotIn("ANTHROPIC_API_KEY", captured["kwargs"]["env"])
            self.assertNotIn("PAPERCLIP_API_KEY", captured["kwargs"]["env"])


if __name__ == "__main__":
    unittest.main()
