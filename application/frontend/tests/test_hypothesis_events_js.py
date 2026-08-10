"""Run the Node-based unit tests for the pure Hypothesis Studio event-model
helpers (static/hypothesis_events.js) as part of the pytest suite.

These cover the active-version semantics behind "revisions are appended
chronological events, never in-place mutation". Skipped if Node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS_TEST = REPO / "tests" / "hypothesis_events.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_hypothesis_events_js_unit_tests():
    result = subprocess.run(
        ["node", "--test", str(JS_TEST)],
        cwd=str(REPO), capture_output=True, text=True,
    )
    assert result.returncode == 0, (result.stdout + "\n" + result.stderr)
