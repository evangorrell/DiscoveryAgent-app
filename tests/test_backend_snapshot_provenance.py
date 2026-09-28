"""Repository-level guard: backend source must remain a generated Git snapshot."""
import json
from pathlib import Path
import subprocess
import unittest


class CommittedBackendProvenanceTests(unittest.TestCase):
    root = Path(__file__).resolve().parents[1]

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], text=True).strip()

    def test_committed_backend_tree_matches_declared_upstream_tree(self):
        metadata = json.loads((self.root / 'release.json').read_text())['backend']
        self.assertEqual(self.git('rev-parse', 'HEAD:application/backend'), metadata['tree'])
        self.assertEqual(len(metadata['commit']), 40)
        self.assertFalse({'canonical_tree', 'canonical_commit', 'source_path'} & metadata.keys())
        import hashlib
        self.assertEqual(hashlib.sha256((self.root / 'application/backend/uv.lock').read_bytes()).hexdigest(),
                         metadata['uv_lock_sha256'])

    def test_generated_backend_has_no_independent_working_tree_edits(self):
        # Ignore normal ignored bytecode, but reject both tracked edits and new
        # non-ignored files. Regenerate/commit before running the release matrix.
        self.assertEqual(self.git('diff', 'HEAD', '--', 'application/backend'), '')
        self.assertEqual(self.git('ls-files', '--others', '--exclude-standard',
                                  'application/backend'), '')
