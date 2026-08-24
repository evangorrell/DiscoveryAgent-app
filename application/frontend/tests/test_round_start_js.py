"""Run browser-free contract tests for the shared V2 round-start client."""
import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parent.parent
JS_TEST = REPO / "tests" / "round_start.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_round_start_js_contract():
    result = subprocess.run(
        ["node", "--test", str(JS_TEST)],
        cwd=str(REPO), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
