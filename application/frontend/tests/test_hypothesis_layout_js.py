"""Run the DOM-free Hypothesis Studio layout-state tests in Node."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
JS_TEST = REPO / "tests" / "hypothesis_layout.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_hypothesis_layout_js_unit_tests():
    result = subprocess.run(
        ["node", "--test", str(JS_TEST)],
        cwd=str(REPO), capture_output=True, text=True,
    )
    assert result.returncode == 0, (result.stdout + "\n" + result.stderr)


def test_controller_uses_visual_grid_placement_without_reordering_cards():
    static = REPO / "da_selection_ui" / "static"
    javascript = (static / "hypothesis_studio.js").read_text(encoding="utf-8")
    css = (static / "hypothesis_studio.css").read_text(encoding="utf-8")

    assert "cards.forEach((h) => grid.appendChild(renderCard(h, selectedIndex)))" in javascript
    assert "const visibleExpanded = visibleExpandedHypotheses();" in javascript
    assert "HSL.singleOverflowColumn(cards, visibleExpanded)" in javascript
    assert "HSL.packedOverflowPreferences(cards, visibleExpanded)" in javascript
    assert "HSL.visibleExpandedState(state.expanded, state.regenerationRequests)" in javascript
    assert "syncOverflowCardLayout(" in javascript
    assert "function syncPackedOverflowCards" in javascript
    assert "function primePackedOverflowGrid" in javascript
    assert "cards.slice(0, 3).forEach" in javascript
    assert "card.style.gridColumn" in javascript
    assert "card.style.gridRow = '1'" in javascript
    assert "columnBottoms[candidate - 1] < columnBottoms[column - 1]" in javascript
    assert "appendChild(card)" not in javascript
    assert ".uses-available-overflow-space > .hs-card:nth-child(4)" in css
    assert ".uses-packed-overflow-space > .hs-card:nth-child(1)" in css
    assert ".uses-packed-overflow-space > .hs-card:nth-child(2)" in css
    assert ".uses-packed-overflow-space > .hs-card:nth-child(3)" in css
    assert "grid-row: 1" in css
    assert "margin-top: calc(var(--hs-overflow-card-offset, 0px) + 14px)" in css
