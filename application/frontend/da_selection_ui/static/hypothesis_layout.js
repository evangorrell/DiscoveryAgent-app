// Pure, DOM-free layout helpers for Hypothesis Studio.
//
// The fourth card remains fourth in data/DOM order. This helper only chooses
// the visual column where that single overflow card should be displayed.
(function (root, factory) {
    if (typeof module !== 'undefined' && module.exports) module.exports = factory();
    else root.HSLayout = factory();
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    function singleOverflowColumn(cards, expanded) {
        if (!Array.isArray(cards) || cards.length !== 4) return null;
        const topCards = cards.slice(0, 3);
        const isExpanded = function (column) {
            const card = topCards[column - 1];
            return !!(card && expanded && expanded[Number(card.index)]);
        };

        // Keep the established centered default while it is available. If it
        // is not, prefer the collapsed card on the left, then the right.
        if (!isExpanded(2)) return 2;
        if (!isExpanded(1)) return 1;
        if (!isExpanded(3)) return 3;
        return 2;
    }

    function packedOverflowPreferences(cards, expanded) {
        if (!Array.isArray(cards) || cards.length < 5) return null;
        const topCards = cards.slice(0, 3);
        const anyExpanded = topCards.some(function (card) {
            return !!(card && expanded && expanded[Number(card.index)]);
        });
        if (!anyExpanded) return null;

        // Overflow cards first fill the still-collapsed top-card columns from
        // left to right. Any remaining card is assigned at render time to the
        // currently shortest column after real card heights are measured.
        const collapsedColumns = [];
        topCards.forEach(function (card, index) {
            if (!(expanded && expanded[Number(card.index)])) collapsedColumns.push(index + 1);
        });
        return cards.slice(3).map(function (_card, index) {
            return index < collapsedColumns.length ? collapsedColumns[index] : null;
        });
    }

    function hypothesisExpansionPanelRatio(currentRatio, floorRatio) {
        const current = Number.isFinite(Number(currentRatio)) ? Number(currentRatio) : 0;
        const floor = Number.isFinite(Number(floorRatio)) ? Number(floorRatio) : 0.5;
        return Math.max(floor, current);
    }

    return {
        singleOverflowColumn: singleOverflowColumn,
        packedOverflowPreferences: packedOverflowPreferences,
        hypothesisExpansionPanelRatio: hypothesisExpansionPanelRatio,
    };
});
