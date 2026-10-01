// Pure, DOM-free layout helpers for Hypothesis Studio.
//
// Visual placement only: hypotheses remain in their original DOM/tab order.
(function (root, factory) {
    if (typeof module !== 'undefined' && module.exports) module.exports = factory();
    else root.HSLayout = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    function packCards(heights, columns, gap) {
        const count = Math.max(1, Math.min(3, Math.floor(Number(columns) || 1)));
        const bottoms = Array(count).fill(0);
        return heights.map(function (height, index) {
            // Seed the first row left-to-right, then choose the shortest column.
            // Stable left-to-right tie breaking makes placement deterministic.
            const column = index < count ? index : bottoms.indexOf(Math.min.apply(null, bottoms));
            const top = bottoms[column];
            bottoms[column] = top + Math.max(0, Number(height) || 0) + Math.max(0, Number(gap) || 0);
            return { column: column + 1, top: top };
        });
    }

    function hypothesisExpansionPanelRatio(currentRatio, floorRatio) {
        const current = Number.isFinite(Number(currentRatio)) ? Number(currentRatio) : 0;
        const floor = Number.isFinite(Number(floorRatio)) ? Number(floorRatio) : 0.5;
        return Math.max(floor, current);
    }

    function visibleExpandedState(expanded, regenerating) {
        const visible = {};
        Object.keys(expanded || {}).forEach(function (key) {
            const index = Number(key);
            if (!expanded[key]) return;
            visible[index] = true;
        });
        return visible;
    }

    function isOpeningFirstVisibleExpansion(expanded, regenerating, index, nextExpanded) {
        const before = visibleExpandedState(expanded, regenerating);
        const afterState = Object.assign({}, expanded || {});
        afterState[Number(index)] = !!nextExpanded;
        const after = visibleExpandedState(afterState, regenerating);
        return Object.keys(before).length === 0 && Object.keys(after).length === 1;
    }

    return {
        packCards: packCards,
        hypothesisExpansionPanelRatio: hypothesisExpansionPanelRatio,
        visibleExpandedState: visibleExpandedState,
        isOpeningFirstVisibleExpansion: isOpeningFirstVisibleExpansion,
    };
});
