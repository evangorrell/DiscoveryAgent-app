from __future__ import annotations

import json
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from release_builder.builder import (
    CANONICAL_FRONTEND_PATH,
    FRONTEND_IMPORT_PROVENANCE,
    PAPERCLIP_WHEEL,
    ReleaseBuildError,
    SourceRequest,
    build_release,
    snapshot_manifest,
    snapshot_git_tree,
    verify_backend_snapshot,
)


def _git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        input=input_bytes,
        check=True,
        capture_output=True,
    )
    return result.stdout.decode().strip()


def _write(path: Path, content: str | bytes, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class ReleaseBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _repository(
        self,
        name: str,
        kind: str,
        *,
        include_lock: bool = True,
        include_wheel: bool = True,
        on_main: bool = False,
    ) -> tuple[Path, str]:
        repository = self.root / name
        repository.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=v2-ui", str(repository)],
            check=True,
            capture_output=True,
        )
        _git(repository, "config", "user.name", "Release Builder Test")
        _git(repository, "config", "user.email", "release-builder@example.test")
        _git(repository, "remote", "add", "origin", f"https://example.test/org/{name}.git")

        source_root = (
            repository / CANONICAL_FRONTEND_PATH if kind == "app" else repository
        )
        _write(source_root / "pyproject.toml", f"[project]\nname = \"{name}\"\n")
        if include_lock:
            _write(source_root / "uv.lock", f"version = 1\n# {name}\n")
        if kind == "app":
            _write(repository / "install.sh", "#!/bin/sh\n# committed installer template\n")
            _write(repository / "manager/discoveryagent_cli/__init__.py", "")
            _write(repository / "tools/install_local_release.py", "print('install')\n")
            _write(source_root / "da_selection_ui/__init__.py", "")
            _write(source_root / "da_selection_ui/__main__.py", "def main(): pass\n")
            _write(source_root / "da_selection_ui/production_server.py", "APP = True\n")
            _write(source_root / "da_selection_ui/templates/base.html", "<main>test</main>\n")
            _write(source_root / "da_selection_ui/static/app.js", "export const ready = true;\n")
        elif kind == "backend":
            _write(repository / "AIR/__init__.py", "")
            _write(
                repository / "scripts/run_hb_sherlock.py",
                "#!/usr/bin/env python3\nprint('test')\n",
                executable=True,
            )
            if include_wheel:
                _write(repository / PAPERCLIP_WHEEL, b"test wheel bytes\n")
        else:
            raise AssertionError(f"Unknown fixture kind: {kind}")

        _git(repository, "add", "--all")
        _git(repository, "commit", "-m", "fixture")
        commit = _git(repository, "rev-parse", "HEAD")
        _git(repository, "update-ref", "refs/remotes/origin/v2-ui", commit)
        if on_main:
            _git(repository, "update-ref", "refs/remotes/origin/main", commit)
        remote = self.root / "remotes" / f"{name}.git"
        remote.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "clone", "--bare", str(repository), str(remote)], check=True, capture_output=True)
        _git(repository, "remote", "set-url", "origin", str(remote))
        if on_main:
            _git(remote, "update-ref", "refs/heads/main", commit)
        return repository, commit

    def _pair(self, **options) -> tuple[Path, str, Path, str]:
        frontend = self._repository(
            "DiscoveryAgent-app", "app", **options.get("app", {})
        )
        backend = self._repository(
            "backend-source", "backend", **options.get("backend", {})
        )
        return frontend[0], frontend[1], backend[0], backend[1]

    def _build(
        self,
        frontend: Path,
        frontend_sha: str,
        backend: Path,
        backend_sha: str,
        destination: Path,
        *,
        mode: str = "development",
    ) -> dict:
        return build_release(
            SourceRequest(frontend, frontend_sha),
            SourceRequest(backend, backend_sha),
            mode=mode,
            destination_root=destination,
            package_revision=1 if mode == "production" else 0,
        )

    def test_production_rejects_dirty_source_and_wrong_head(self):
        frontend, sha, backend, backend_sha = self._pair(app={"on_main": True}, backend={"on_main": True})
        _write(frontend / "untracked.txt", "private")
        with self.assertRaisesRegex(ReleaseBuildError, "clean working tree"):
            self._build(frontend, sha, backend, backend_sha, self.root / "out", mode="production")
        _git(frontend, "add", "untracked.txt")
        _git(frontend, "commit", "-m", "next")
        with self.assertRaisesRegex(ReleaseBuildError, "HEAD"):
            self._build(frontend, sha, backend, backend_sha, self.root / "out", mode="production")

    def test_production_refreshes_stale_main_and_rejects_modified_package(self):
        from release_builder.builder import verify_package_source
        frontend, sha, backend, backend_sha = self._pair(app={"on_main": True}, backend={"on_main": True})
        output = self.root / "production"
        metadata = self._build(frontend, sha, backend, backend_sha, output, mode="production")
        verify_package_source(output, json.loads(json.dumps(metadata)))
        from release_builder.install_artifact import build_install_artifact
        from manager.discoveryagent_cli.installer import validate_packaged_payload
        import tarfile
        artifact = build_install_artifact(output, self.root / 'artifact')
        with tarfile.open(artifact.archive) as bundle:
            bundle.extractall(self.root / 'extracted')
        validate_packaged_payload(self.root / 'extracted/discoveryagent-install')
        _write(output / "manager/discoveryagent_cli/__init__.py", "changed")
        with self.assertRaisesRegex(ReleaseBuildError, "differ"):
            verify_package_source(output, json.loads(json.dumps(metadata)))
        remote = _git(frontend, "remote", "get-url", "origin")
        _git(Path(remote), "update-ref", "-d", "refs/heads/main")
        with self.assertRaisesRegex(ReleaseBuildError, "origin/main"):
            self._build(frontend, sha, backend, backend_sha, self.root / "stale", mode="production")

    def test_production_ignores_committed_umbrella_backend_patches(self):
        frontend, sha, backend, backend_sha = self._pair(app={"on_main": True}, backend={"on_main": True})
        shutil.copytree(backend, frontend / 'application/backend', ignore=shutil.ignore_patterns('.git'))
        _write(frontend / 'application/backend/AIR/__init__.py', '# independent umbrella edit\n')
        _write(frontend / 'application/backend/umbrella_only.py', '# must never ship\n')
        # Even stale umbrella provenance cannot override explicit source selection.
        _write(frontend / 'release.json', json.dumps({'backend': {'commit': '0' * 40}}))
        _git(frontend, 'add', '--all')
        _git(frontend, 'commit', '-m', 'stale umbrella snapshot')
        sha = _git(frontend, 'rev-parse', 'HEAD')
        _git(frontend, 'push', 'origin', 'HEAD:refs/heads/main')
        output = self.root / 'canonical'
        metadata = self._build(frontend, sha, backend, backend_sha, output, mode='production')
        self.assertEqual((output / 'application/backend/AIR/__init__.py').read_text(), '')
        self.assertFalse((output / 'application/backend/umbrella_only.py').exists())
        self.assertEqual(metadata['backend']['commit'], backend_sha)
        self.assertEqual(metadata['backend']['tree'], _git(backend, 'rev-parse', f'{backend_sha}^{{tree}}'))
        self.assertEqual(metadata['backend']['uv_lock_sha256'], hashlib.sha256((backend / 'uv.lock').read_bytes()).hexdigest())
        self.assertFalse({'canonical_commit', 'canonical_tree', 'source_path'} & metadata['backend'].keys())
        self.assertEqual(snapshot_git_tree(output / 'application/backend'), metadata['backend']['tree'])
        with self.assertRaisesRegex(ReleaseBuildError, 'upstream Git tree'):
            verify_backend_snapshot(frontend / 'application/backend', metadata['backend'])

    def test_explicit_backend_commit_controls_generated_content(self):
        frontend, sha, backend, old_sha = self._pair()
        _write(backend / 'AIR/__init__.py', '# new upstream functionality\n')
        _git(backend, 'add', '--all')
        _git(backend, 'commit', '-m', 'upstream change')
        new_sha = _git(backend, 'rev-parse', 'HEAD')
        _git(backend, 'push', 'origin', 'HEAD:refs/heads/v2-ui')
        for backend_sha, content in ((old_sha, ''), (new_sha, '# new upstream functionality\n')):
            output = self.root / backend_sha
            metadata = self._build(frontend, sha, backend, backend_sha, output)
            self.assertEqual((output / 'application/backend/AIR/__init__.py').read_text(), content)
            self.assertEqual(metadata['backend']['commit'], backend_sha)
            self.assertEqual(snapshot_git_tree(output / 'application/backend'), _git(backend, 'rev-parse', f'{backend_sha}^{{tree}}'))

    def test_backend_verifier_rejects_content_inventory_mode_and_provenance_drift(self):
        frontend, sha, backend, backend_sha = self._pair()
        for change in ('content', 'extra', 'missing', 'mode', 'tree', 'lock', 'override'):
            with self.subTest(change=change):
                output = self.root / change
                metadata = self._build(frontend, sha, backend, backend_sha, output)
                root = output / 'application/backend'
                provenance = metadata['backend']
                if change == 'content':
                    _write(root / 'AIR/__init__.py', 'tampered')
                elif change == 'extra':
                    _write(root / 'extra.py', 'independent code')
                elif change == 'missing':
                    (root / 'AIR/__init__.py').unlink()
                elif change == 'mode':
                    (root / 'scripts/run_hb_sherlock.py').chmod(0o644)
                elif change == 'tree':
                    provenance['tree'] = '0' * 40
                elif change == 'lock':
                    provenance['uv_lock_sha256'] = '0' * 64
                else:
                    provenance['canonical_commit'] = sha
                with self.assertRaises(ReleaseBuildError):
                    verify_backend_snapshot(root, provenance)

    def test_backend_tree_verifier_preserves_symlinks_modes_and_git_sort_order(self):
        frontend, sha, backend, backend_sha = self._pair()
        _write(backend / 'a.c', 'file next to similarly named directory\n')
        _write(backend / 'a/nested.txt', 'nested\n')
        (backend / 'dangling-link').symlink_to('missing-target')
        _git(backend, 'add', '--all')
        _git(backend, 'commit', '-m', 'tree fidelity fixtures')
        backend_sha = _git(backend, 'rev-parse', 'HEAD')
        _git(backend, 'push', 'origin', 'HEAD:refs/heads/v2-ui')
        output = self.root / 'tree-fidelity'
        metadata = self._build(frontend, sha, backend, backend_sha, output)
        root = output / 'application/backend'
        verify_backend_snapshot(root, metadata['backend'])
        self.assertEqual(snapshot_git_tree(root), _git(backend, 'rev-parse', 'HEAD^{tree}'))
        self.assertEqual(os.readlink(root / 'dangling-link'), 'missing-target')
        (root / 'dangling-link').unlink()
        (root / 'dangling-link').symlink_to('different-target')
        with self.assertRaisesRegex(ReleaseBuildError, 'upstream Git tree'):
            verify_backend_snapshot(root, metadata['backend'])

    def test_package_verification_checks_upstream_tree_even_if_inventory_is_recomputed(self):
        from manager.discoveryagent_cli.package_identity import package_source_manifest
        from release_builder.builder import verify_package_source
        frontend, sha, backend, backend_sha = self._pair(app={'on_main': True}, backend={'on_main': True})
        output = self.root / 'production-tree'
        metadata = self._build(frontend, sha, backend, backend_sha, output, mode='production')
        _write(output / 'application/backend/AIR/__init__.py', '# independent edit\n')
        metadata['package_sources'] = json.loads(json.dumps(package_source_manifest(output)))
        with self.assertRaisesRegex(ReleaseBuildError, 'upstream Git tree'):
            verify_package_source(output, metadata)

    def test_identical_inputs_produce_identical_snapshots_and_metadata(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        first = self.root / "first"
        second = self.root / "second"

        first_metadata = self._build(
            frontend, frontend_sha, backend, backend_sha, first
        )
        second_metadata = self._build(
            frontend, frontend_sha, backend, backend_sha, second
        )

        self.assertEqual(first_metadata, second_metadata)
        self.assertEqual(snapshot_manifest(first), snapshot_manifest(second))
        self.assertEqual(
            (first / "release.json").read_bytes(),
            (second / "release.json").read_bytes(),
        )
        self.assertEqual(
            first_metadata["release_id"],
            f"dev-{first_metadata['frontend']['tree'][:12]}-{backend_sha[:12]}",
        )
        serialized = (first / "release.json").read_text(encoding="utf-8")
        self.assertNotIn(str(frontend), serialized)
        self.assertNotIn(str(backend), serialized)
        self.assertNotIn("timestamp", serialized.lower())

    def test_backend_only_refresh_preserves_frontend_and_its_local_environment(self):
        frontend, sha, backend, backend_sha = self._pair()
        output = self.root / 'refresh'
        self._build(frontend, sha, backend, backend_sha, output)
        _write(output / 'application/frontend/.venv/local-marker', 'retain local environment')
        _write(output / 'application/frontend/da_selection_ui/__init__.py', '# local frontend work\n')
        _write(output / 'application/backend/old_umbrella_patch.py', '# discard independent backend edit\n')
        before = snapshot_manifest(output / 'application/frontend')
        metadata = build_release(SourceRequest(frontend, sha), SourceRequest(backend, backend_sha),
                                 mode='development', destination_root=output, backend_only=True)
        self.assertEqual(snapshot_manifest(output / 'application/frontend'), before)
        self.assertFalse((output / 'application/backend/old_umbrella_patch.py').exists())
        verify_backend_snapshot(output / 'application/backend', metadata['backend'])

    def test_backend_only_refresh_cannot_bypass_production_build_contract(self):
        frontend, sha, backend, backend_sha = self._pair()
        with self.assertRaisesRegex(ReleaseBuildError, 'development mode'):
            build_release(SourceRequest(frontend, sha), SourceRequest(backend, backend_sha),
                          mode='production', package_revision=1,
                          destination_root=self.root / 'invalid', backend_only=True)

    def test_app_metadata_commit_does_not_change_frontend_release_identity(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        first = self._build(
            frontend,
            frontend_sha,
            backend,
            backend_sha,
            self.root / "first",
        )
        _write(frontend / "release-notes.md", "metadata-only change\n")
        _git(frontend, "add", "release-notes.md")
        _git(frontend, "commit", "-m", "metadata-only change")
        second_sha = _git(frontend, "rev-parse", "HEAD")
        _git(frontend, "update-ref", "refs/remotes/origin/v2-ui", second_sha)

        second = self._build(
            frontend,
            second_sha,
            backend,
            backend_sha,
            self.root / "second",
        )

        self.assertNotEqual(first["frontend"]["commit"], second["frontend"]["commit"])
        self.assertEqual(first["frontend"]["tree"], second["frontend"]["tree"])
        self.assertEqual(first["release_id"], second["release_id"])

    def test_dirty_and_untracked_source_files_are_ignored(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        committed_pyproject = _git(
            frontend,
            "show",
            f"{frontend_sha}:{CANONICAL_FRONTEND_PATH}/pyproject.toml",
        )
        _write(frontend / CANONICAL_FRONTEND_PATH / "pyproject.toml", "dirty working tree\n")
        _write(
            frontend / CANONICAL_FRONTEND_PATH / "checkpoint/private.json",
            "{}\n",
        )
        _write(backend / "untracked.txt", "must not ship\n")

        destination = self.root / "output"
        self._build(frontend, frontend_sha, backend, backend_sha, destination)

        self.assertEqual(
            (destination / "application/frontend/pyproject.toml")
            .read_text(encoding="utf-8")
            .strip(),
            committed_pyproject,
        )
        self.assertFalse((destination / "application/frontend/checkpoint").exists())
        self.assertFalse((destination / "application/backend/untracked.txt").exists())

    def test_executable_mode_is_preserved(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        destination = self.root / "output"
        self._build(frontend, frontend_sha, backend, backend_sha, destination)
        mode = stat.S_IMODE(
            (destination / "application/backend/scripts/run_hb_sherlock.py")
            .stat()
            .st_mode
        )
        self.assertEqual(mode, 0o755)
        self.assertEqual(stat.S_IMODE((destination / 'application/backend/pyproject.toml').stat().st_mode), 0o644)

    def test_invalid_repository_paths_are_rejected(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        invalid = self.root / "not-a-repository"
        invalid.mkdir()
        for frontend_path, backend_path in (
            (invalid, backend),
            (frontend, invalid),
        ):
            with self.subTest(frontend=frontend_path, backend=backend_path):
                with self.assertRaises(ReleaseBuildError):
                    self._build(
                        frontend_path,
                        frontend_sha,
                        backend_path,
                        backend_sha,
                        self.root / "output-invalid",
                    )

    def test_nonexistent_and_noncommit_revisions_are_rejected(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        with self.assertRaisesRegex(ReleaseBuildError, "does not resolve to a commit"):
            self._build(
                frontend,
                "0" * 40,
                backend,
                backend_sha,
                self.root / "missing-revision",
            )

        blob = _git(frontend, "hash-object", "-w", "--stdin", input_bytes=b"blob")
        with self.assertRaisesRegex(ReleaseBuildError, "does not resolve to a commit"):
            self._build(
                frontend,
                blob,
                backend,
                backend_sha,
                self.root / "blob-revision",
            )

    def test_required_lockfiles_and_wheel_are_enforced(self) -> None:
        cases = (
            ({"app": {"include_lock": False}}, "frontend snapshot.*uv.lock"),
            ({"backend": {"include_lock": False}}, "backend snapshot.*uv.lock"),
            ({"backend": {"include_wheel": False}}, "backend snapshot.*gxl_paperclip"),
        )
        for index, (options, pattern) in enumerate(cases):
            with self.subTest(pattern=pattern):
                case_root = self.root / f"case-{index}"
                case_root.mkdir()
                old_root = self.root
                self.root = case_root
                try:
                    frontend, frontend_sha, backend, backend_sha = self._pair(**options)
                    with self.assertRaisesRegex(ReleaseBuildError, pattern):
                        self._build(
                            frontend,
                            frontend_sha,
                            backend,
                            backend_sha,
                            case_root / "output",
                        )
                finally:
                    self.root = old_root

    def test_development_accepts_remote_feature_branch_and_records_validation(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        metadata = self._build(
            frontend,
            frontend_sha,
            backend,
            backend_sha,
            self.root / "development",
        )
        for source in (metadata["frontend"], metadata["backend"]):
            self.assertTrue(source["validation"]["reachable_from_known_remote"])
            self.assertFalse(source["validation"]["reachable_from_origin_main"])
            self.assertEqual(
                source["validation"]["reachable_remote_refs"],
                ["refs/remotes/origin/v2-ui"],
            )
        self.assertEqual(metadata["frontend"]["source_path"], CANONICAL_FRONTEND_PATH)
        self.assertEqual(
            metadata["frontend"]["provenance"],
            {
                "relationship": "historical_import",
                **FRONTEND_IMPORT_PROVENANCE,
            },
        )

    def test_local_commit_not_reachable_from_an_origin_ref_is_rejected(self) -> None:
        frontend, _, backend, backend_sha = self._pair()
        _write(frontend / "local-only.txt", "not published\n")
        _git(frontend, "add", "local-only.txt")
        _git(frontend, "commit", "-m", "local-only commit")
        local_commit = _git(frontend, "rev-parse", "HEAD")

        with self.assertRaisesRegex(ReleaseBuildError, "not reachable from any known origin ref"):
            self._build(
                frontend,
                local_commit,
                backend,
                backend_sha,
                self.root / "unpublished",
            )

    def test_forbidden_tracked_runtime_state_is_rejected(self) -> None:
        frontend, _, backend, backend_sha = self._pair()
        checkpoint = f"{CANONICAL_FRONTEND_PATH}/checkpoint/state.json"
        _write(frontend / checkpoint, "{}\n")
        _git(frontend, "add", checkpoint)
        _git(frontend, "commit", "-m", "forbidden fixture")
        forbidden_commit = _git(frontend, "rev-parse", "HEAD")
        _git(
            frontend,
            "update-ref",
            "refs/remotes/origin/v2-ui",
            forbidden_commit,
        )

        with self.assertRaisesRegex(ReleaseBuildError, "checkpoint"):
            self._build(
                frontend,
                forbidden_commit,
                backend,
                backend_sha,
                self.root / "forbidden",
            )

    def test_production_rejects_commits_not_reachable_from_origin_main(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        with self.assertRaisesRegex(ReleaseBuildError, "origin/main"):
            self._build(
                frontend,
                frontend_sha,
                backend,
                backend_sha,
                self.root / "production-rejected",
                mode="production",
            )

    def test_production_accepts_commits_reachable_from_origin_main(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair(
            app={"on_main": True}, backend={"on_main": True}
        )
        metadata = self._build(
            frontend,
            frontend_sha,
            backend,
            backend_sha,
            self.root / "production",
            mode="production",
        )
        self.assertEqual(metadata["release_type"], "production")
        self.assertTrue(
            metadata["frontend"]["validation"]["reachable_from_origin_main"]
        )
        self.assertTrue(
            metadata["backend"]["validation"]["reachable_from_origin_main"]
        )

    def test_failed_build_does_not_replace_an_existing_snapshot(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        destination = self.root / "active"
        self._build(frontend, frontend_sha, backend, backend_sha, destination)
        before = snapshot_manifest(destination)

        failing_root = self.root / "failing-fixture"
        failing_root.mkdir()
        old_root = self.root
        self.root = failing_root
        try:
            bad_backend, bad_backend_sha = self._repository(
                "backend-without-wheel", "backend", include_wheel=False
            )
        finally:
            self.root = old_root

        with self.assertRaises(ReleaseBuildError):
            self._build(
                frontend,
                frontend_sha,
                bad_backend,
                bad_backend_sha,
                destination,
            )
        self.assertEqual(before, snapshot_manifest(destination))

    def test_release_json_is_valid_and_matches_returned_metadata(self) -> None:
        frontend, frontend_sha, backend, backend_sha = self._pair()
        destination = self.root / "output"
        metadata = self._build(
            frontend, frontend_sha, backend, backend_sha, destination
        )
        parsed = json.loads((destination / "release.json").read_text(encoding="utf-8"))
        self.assertEqual(parsed, metadata)
        self.assertEqual(parsed["required_cpython_version"], "3.11.11")
        self.assertEqual(parsed["paperclip"]["vendored_wheel"], PAPERCLIP_WHEEL)
        self.assertEqual(parsed["frontend"]["repository"], "DiscoveryAgent-app")
        self.assertEqual(parsed["frontend"]["commit"], frontend_sha)
        self.assertEqual(
            parsed["frontend"]["tree"],
            _git(
                frontend,
                "rev-parse",
                f"{frontend_sha}:{CANONICAL_FRONTEND_PATH}",
            ),
        )
        self.assertEqual(parsed["frontend"]["source_path"], CANONICAL_FRONTEND_PATH)
        self.assertEqual(
            parsed["frontend"]["provenance"]["commit"],
            FRONTEND_IMPORT_PROVENANCE["commit"],
        )


if __name__ == "__main__":
    unittest.main()
