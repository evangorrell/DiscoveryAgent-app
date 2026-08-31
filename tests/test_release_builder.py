from __future__ import annotations

import json
import os
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
        )

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
