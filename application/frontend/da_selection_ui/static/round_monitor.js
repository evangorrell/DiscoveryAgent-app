// Round Monitor: self-contained polling + tree rendering + observer chat.
// Deliberately does not touch script.js -- this page is a separate, read-only
// view of filesystem evidence for the latest round. The chat panel mirrors the
// Hypothesis Agent Chat markup/classes (purple user bubbles, grey assistant
// bubbles, Markdown rendering via marked + DOMPurify) without loading
// script.js's larger machinery.

const ROUND_STATUS_POLL_MS = 8000;
const ROUND_TREE_POLL_MS = 8000;
const ROUND_ACTIVITY_POLL_MS = 5000;

const GREETING_TEXT = "Ask me what DiscoveryAgent is doing, what evidence it has found, or what files have " +
    "been created so far this round. I can only use current round files — I can't steer or interrupt the run.";

let chatMode = 'queries';

async function apiCall(endpoint, data = null) {
    const options = {
        method: data ? 'POST' : 'GET',
        headers: { 'Content-Type': 'application/json' },
    };
    if (data) {
        options.body = JSON.stringify(data);
    }
    // Rendered only via the canonical /projects/<slug>/rounds/<n> route: pin
    // every request (status/tree/activity/chat) to that exact round, so this
    // page can never silently drift to a different "active" round.
    if (typeof window.__PINNED_ROUND__ === 'number') {
        const sep = endpoint.includes('?') ? '&' : '?';
        endpoint = endpoint + sep + 'round=' + encodeURIComponent(window.__PINNED_ROUND__);
    }
    const response = await fetch(endpoint, options);
    return await response.json();
}

function formatStateLabel(state) {
    const labels = {
        round_monitor: 'Round in progress',
        round_complete_needs_review: 'Round complete',
        needs_hypothesis_selection: 'Awaiting selection',
        needs_hypothesis_generation: 'Awaiting generation',
        selected_waiting_for_backend: 'Waiting for backend',
        error_or_unknown: 'Unknown state',
    };
    return labels[state] || state || 'Unknown';
}

async function refreshRoundStatus() {
    try {
        const result = await apiCall('/api/round_status');
        const badge = document.getElementById('rm-status-badge');
        if (!result.success) {
            if (badge) badge.textContent = 'Status unavailable';
            return;
        }
        if (badge) {
            badge.textContent = formatStateLabel(result.state);
            badge.className = `view-badge ${result.state === 'round_complete_needs_review' ? 'full' : 'summary'}`;
        }
        if (result.state === 'round_complete_needs_review') {
            window.location.href = '/report?type=round';
        }
    } catch (err) {
        console.error('round_status error', err);
    }
}

function renderSelectedHypothesisSummary(treeResult) {
    const el = document.getElementById('rm-selected-hypothesis');
    if (!el) return;
    const summary = treeResult.summary || {};
    if (summary.selected_hypothesis_text) {
        el.textContent = summary.selected_hypothesis_text;
    } else {
        el.textContent = summary.selected_hypothesis_message
            || 'Selected hypothesis source not found, but query artifacts indicate this round has started.';
    }
}

function statusMarker(status) {
    const markers = {
        present: '✓', missing: '✗', completed: '✓',
        in_progress: '●', running: '●', discovered: '○', unknown: '○', failed: '✗',
    };
    return markers[status] || '?';
}

function formatQueryStatusLabel(status) {
    const labels = {
        completed: 'completed', in_progress: 'in progress', running: 'running',
        discovered: 'discovered', unknown: 'unknown', failed: 'failed', started: 'started',
    };
    return labels[status] || status || 'unknown';
}

function renderPaths(checkpointPath, roundPath) {
    const cpEl = document.getElementById('rm-checkpoint-path');
    const roundRow = document.getElementById('rm-round-path-row');
    const roundEl = document.getElementById('rm-round-path');

    if (!cpEl || !roundRow || !roundEl) return;

    const norm = p => (p || '').replace(/\/+$/, '');
    const same = norm(checkpointPath) === norm(roundPath);

    if (same) {
        cpEl.closest('.rm-path-row').querySelector('.badge').textContent = 'Round Path';
        if (checkpointPath) cpEl.textContent = checkpointPath;
        roundRow.classList.add('hidden');
    } else {
        cpEl.closest('.rm-path-row').querySelector('.badge').textContent = 'Checkpoint path';
        if (checkpointPath) cpEl.textContent = checkpointPath;
        roundRow.classList.remove('hidden');
        if (roundPath) roundEl.textContent = roundPath;
    }
}

// Renders the status line, hypothesis summary, constellation, and paths from
// /api/round_tree; #rm-tree-status is a small status/error line only.
let lastConstellationSignature = null;

async function refreshRoundTree() {
    const statusEl = document.getElementById('rm-tree-status');
    try {
        const result = await apiCall('/api/round_tree');
        if (!result.success) {
            if (statusEl) statusEl.textContent = result.error || 'Directory tree unavailable.';
            return;
        }
        if (statusEl) statusEl.textContent = '';
        renderSelectedHypothesisSummary(result);

        // Skip the full constellation teardown/re-layout when neither the
        // data nor the container width changed since the last poll.
        const constellationEl = document.getElementById('rm-constellation');
        const containerWidth = constellationEl
            ? Math.round(constellationEl.getBoundingClientRect().width) : 0;
        const signature = containerWidth + '|' + JSON.stringify(result.constellation || null);
        if (signature !== lastConstellationSignature) {
            lastConstellationSignature = signature;
            renderConstellation(result.constellation);
        }

        renderPaths(result.checkpoint_path, result.round_path);
    } catch (err) {
        if (statusEl) statusEl.textContent = 'Error loading directory tree.';
        console.error('round_tree error', err);
    }
}

// --- Round Constellation -------------------------------------------------
// Low-text visual concept-map, laid out and rendered from /api/round_tree's
// `constellation` field (built by _build_round_constellation in app.py). This
// module only positions data the backend already computed: it never infers
// edges/lanes, never calls an LLM, and never hardcodes dataset text.

// Small per-lane horizontal nudge (percent) on top of each node's branch
// anchor -- a gentle color bias, never enough to override branch structure.
const CONSTELLATION_LANE_X_BIAS = {
    analysis: -6,
    validation: 3,
    other: 0,
    synthesis: 4,
    literature: 7,
};

// Vertical band (percent) the query rows occupy -- under the origin box,
// capped short of the bottom edge so the last row isn't clipped.
const CONSTELLATION_TOP_QUERY_Y = 22;
const CONSTELLATION_BOTTOM_QUERY_Y = 90;

// Node x clamp (percent), tighter than the panel edges so a node scaled up
// 1.35x on hover/select still fits inside the panel.
const CONSTELLATION_NODE_X_MIN = 16;
const CONSTELLATION_NODE_X_MAX = 84;

// Approx anchor (percent) for edges from the Selected Hypothesis box -- its
// position is fixed in CSS (.rm-constellation-origin), not measured here.
const CONSTELLATION_ORIGIN_ANCHOR = { xPct: 50, yPct: 10 };

const CONSTELLATION_HOVER_DWELL_MS = 300;
const CONSTELLATION_KNOWN_LANES = new Set(['analysis', 'literature', 'validation', 'synthesis', 'other']);

let constellationHoverTimer = null;
let constellationSelectedQueryId = null; // persists until Escape / background click / a new render
// { nodesById, edges, positions } for the currently-rendered constellation,
// used by click/hover handlers to look up related nodes without re-querying the DOM.
let lastConstellationData = null;
let constellationDismissHandlersAttached = false;

function clampPct(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

// The root query is simply the earliest-order node -- order-based, never a
// hardcoded query number or id.
function constellationRootId(nodes) {
    let root = null;
    nodes.forEach((node) => {
        if (!root || (node.order || 0) < (root.order || 0)) root = node;
    });
    return root ? root.query_id : null;
}

// Minimum vertical gap (percent) between a node and each of its
// primary_flow/convergence parents -- makes "child below parent" a real
// constraint, not just a byproduct of averaging.
const CONSTELLATION_MIN_LAYER_GAP_PCT = 9;

// y-rank per node (percent) = max of two floors, whichever is further down:
//   1. parent floor -- rank[parent] + the layer gap, for every parent, so a
//      child always ranks strictly below all its parents.
//   2. chronological floor -- proportional to how far into the round this
//      query's ORDER falls, so a late query attached to an early parent
//      still can't render near the top.
// Nodes are walked in `order`, and the backend guarantees a parent is always
// an earlier-order node, so every parent's rank is final when a child is reached.
function computeConstellationRankY(nodes, edges) {
    const parentIdsByTarget = {};
    edges.forEach((edge) => {
        if (edge.edge_type !== 'primary_flow' && edge.edge_type !== 'convergence') return;
        (parentIdsByTarget[edge.target] = parentIdsByTarget[edge.target] || []).push(edge.source);
    });

    const ordered = nodes.slice().sort((a, b) => (a.order || 0) - (b.order || 0));
    const maxOrder = ordered.length ? ordered[ordered.length - 1].order || 1 : 1;
    const originRank = CONSTELLATION_TOP_QUERY_Y - CONSTELLATION_MIN_LAYER_GAP_PCT;

    // Pass 1: raw, unclamped ranks -- a long chain can need more vertical
    // room than [TOP_Y, BOTTOM_Y]; clamping per node here would collapse a
    // tail of late nodes onto the bottom edge and break the strict
    // child-below-parent ordering. Pass 2 rescales once instead.
    const rawRankY = {};
    ordered.forEach((node) => {
        const parentIds = parentIdsByTarget[node.query_id] || [];
        let parentFloor = originRank + CONSTELLATION_MIN_LAYER_GAP_PCT; // origin's virtual children floor = CONSTELLATION_TOP_QUERY_Y
        parentIds.forEach((parentId) => {
            const parentRank = parentId === CONSTELLATION_ORIGIN_ID ? originRank : (rawRankY[parentId] !== undefined ? rawRankY[parentId] : originRank);
            const floorFromParent = parentRank + CONSTELLATION_MIN_LAYER_GAP_PCT;
            if (floorFromParent > parentFloor) parentFloor = floorFromParent;
        });

        const orderFrac = maxOrder > 1 ? ((node.order || 1) - 1) / (maxOrder - 1) : 0;
        const chronologicalFloor = CONSTELLATION_TOP_QUERY_Y + orderFrac * (CONSTELLATION_BOTTOM_QUERY_Y - CONSTELLATION_TOP_QUERY_Y);

        rawRankY[node.query_id] = Math.max(parentFloor, chronologicalFloor);
    });

    // Pass 2: rescale [TOP_Y, maxRawRank] onto [TOP_Y, BOTTOM_Y]. This
    // monotonic affine transform preserves every strict inequality from pass 1.
    const rawValues = Object.values(rawRankY);
    const maxRawRank = rawValues.length ? Math.max(...rawValues) : CONSTELLATION_TOP_QUERY_Y;
    const rawSpan = maxRawRank - CONSTELLATION_TOP_QUERY_Y;
    const targetSpan = CONSTELLATION_BOTTOM_QUERY_Y - CONSTELLATION_TOP_QUERY_Y;

    const rankY = {};
    ordered.forEach((node) => {
        const raw = rawRankY[node.query_id];
        rankY[node.query_id] = rawSpan > 0
            ? CONSTELLATION_TOP_QUERY_Y + ((raw - CONSTELLATION_TOP_QUERY_Y) / rawSpan) * targetSpan
            : CONSTELLATION_TOP_QUERY_Y;
    });
    return rankY;
}

// How coarsely nearby y-ranks are grouped into a shared visual "row" for
// horizontal-spread purposes only (doesn't change the computed y), so
// near-rank siblings spread side-by-side rather than each self-centering.
const CONSTELLATION_ROW_GROUP_PCT = 10;

// Distinct descendants reachable from each node via primary_flow/convergence
// edges only (never secondary_reference). Used to tell the main research
// trunk from side branches. Visited-set DFS so a node reachable by more than
// one path (e.g. a convergence node) is counted once.
function computeConstellationDownstreamCounts(nodes, edges) {
    const childIdsByParent = {};
    edges.forEach((edge) => {
        if (edge.edge_type !== 'primary_flow' && edge.edge_type !== 'convergence') return;
        (childIdsByParent[edge.source] = childIdsByParent[edge.source] || []).push(edge.target);
    });

    const counts = {};
    nodes.forEach((node) => {
        const visited = new Set();
        const stack = (childIdsByParent[node.query_id] || []).slice();
        while (stack.length) {
            const cur = stack.pop();
            if (visited.has(cur)) continue;
            visited.add(cur);
            (childIdsByParent[cur] || []).forEach((c) => stack.push(c));
        }
        counts[node.query_id] = visited.size;
    });
    return counts;
}

// Fan `ids` around `center`, ordered by weight (= downstream_count + 1) so
// the most-active branch stays nearest center and side branches move outward.
// Shared by branch-seed placement and per-parent sibling fan-out (see opts):
//   - 1 id: at `center`.
//   - 2 ids, opts.symmetric: straddle `center`, earlier-order left.
//   - 2 ids otherwise: top-weight at `center`, the other offset to one side.
//   - 3+ ids: top-weight at `center`, rest alternate left/right by descending
//     weight, each successive pair offset wider (capped at opts.maxOffset).
function assignCentralityFannedAnchors(anchors, ids, center, downstreamCounts, orderById, opts) {
    if (ids.length === 0) return;
    if (ids.length === 1) {
        anchors[ids[0]] = center;
        return;
    }

    const weighted = ids
        .map((id) => ({ id, weight: (downstreamCounts[id] || 0) + 1, order: orderById[id] || 0 }))
        .sort((a, b) => (b.weight - a.weight) || (a.order - b.order));

    if (ids.length === 2) {
        const [main, side] = weighted;
        if (opts.symmetric) {
            // STRADDLE the center: the two children sit on OPPOSITE sides of
            // the parent so they read as a fan/triangle rather than "parent
            // continues straight down + one node hanging off to the side."
            // Earlier-order child goes left, later goes right (reduces
            // crossings); which is "main" by weight doesn't matter for a
            // symmetric split, only for the central-corridor (non-symmetric)
            // branch-seed case below.
            const mainGoLeft = main.order <= side.order;
            anchors[main.id] = clampPct(center + (mainGoLeft ? -1 : 1) * opts.baseOffset, 0, 100);
            anchors[side.id] = clampPct(center + (mainGoLeft ? 1 : -1) * opts.baseOffset, 0, 100);
            return;
        }
        anchors[main.id] = center;
        const goLeft = side.order < main.order;
        anchors[side.id] = clampPct(center + (goLeft ? -1 : 1) * opts.baseOffset, 0, 100);
        return;
    }

    anchors[weighted[0].id] = center;
    for (let i = 1; i < weighted.length; i += 1) {
        const pairIndex = Math.floor((i - 1) / 2);
        const side = (i - 1) % 2 === 0 ? -1 : 1;
        const offset = Math.min(opts.maxOffset, opts.baseOffset + pairIndex * opts.stepPerPair);
        anchors[weighted[i].id] = clampPct(center + side * offset, 0, 100);
    }
}

// Widest fan in the layout: the root's branch seeds should read as distinct
// avenues.
const CONSTELLATION_BRANCH_FAN_OPTS = { baseOffset: 25, stepPerPair: 5, maxOffset: 34 };

// Narrower, symmetric fan for a parent's single-parent children, so a chain
// doesn't stay pinned to one x hop after hop while staying anchored to its branch.
const CONSTELLATION_SIBLING_FAN_OPTS = { baseOffset: 12, stepPerPair: 5, maxOffset: 22, symmetric: true };

// Per-generation horizontal zig-zag (percent) added to a single-parent child's
// fan center, alternating by graph depth -- the main "break straight chains"
// mechanism: without it a long single-child chain inherits the same branch
// anchor x every hop (a vertical line). Deterministic (depth parity, no random).
const CONSTELLATION_CHAIN_ZIGZAG_PCT = 9;

// Horizontal anchor (percent) per node, driven by which BRANCH it belongs to
// (not chronological order or lane), returned as query_id -> x:
//   - root anchors at 50.
//   - branch seeds (parent set is exactly {root}) get a centrality-aware fan
//     either side of 50. Structural, not tied to the backend's reason string.
//   - a single-parent node shares its parent's anchor (plus the depth zig-zag)
//     as the fan center for that parent's children.
//   - a convergence node (2+ parents) takes the branch_weight-weighted average
//     of its parents' anchors, settling between the branches it merges.
// Entirely graph-shape-driven -- no hardcoded query numbers.
function computeConstellationBranchAnchors(nodes, edges) {
    const parentIdsByTarget = {};
    edges.forEach((edge) => {
        if (edge.edge_type !== 'primary_flow' && edge.edge_type !== 'convergence') return;
        (parentIdsByTarget[edge.target] = parentIdsByTarget[edge.target] || []).push(edge.source);
    });

    const downstreamCounts = computeConstellationDownstreamCounts(nodes, edges);
    const orderById = {};
    nodes.forEach((node) => { orderById[node.query_id] = node.order || 0; });

    const ordered = nodes.slice().sort((a, b) => (a.order || 0) - (b.order || 0));
    const rootId = constellationRootId(nodes);
    const anchors = {};
    if (rootId) anchors[rootId] = 50;

    // Generation depth per node, used only for the chain zig-zag: root 0,
    // branch seeds 1, otherwise one deeper than the deepest parent.
    const depthById = {};
    if (rootId) depthById[rootId] = 0;

    // Even depth zigs left, odd zigs right, so consecutive generations of a
    // single-child chain never land on the same x.
    const chainZigzag = (depth) => (depth % 2 === 0 ? -1 : 1) * CONSTELLATION_CHAIN_ZIGZAG_PCT;

    const branchSeedIds = ordered
        .filter((node) => node.query_id !== rootId)
        .filter((node) => {
            const parentIds = parentIdsByTarget[node.query_id] || [];
            return parentIds.length > 0 && parentIds.every((pid) => pid === rootId);
        })
        .map((node) => node.query_id);
    branchSeedIds.forEach((id) => { depthById[id] = 1; });
    assignCentralityFannedAnchors(anchors, branchSeedIds, 50, downstreamCounts, orderById, CONSTELLATION_BRANCH_FAN_OPTS);

    // Remaining single-parent nodes grouped by their sole parent, so a whole
    // sibling group is fanned out together when its first member is reached.
    const singleParentChildrenByParent = {};
    ordered.forEach((node) => {
        if (anchors[node.query_id] !== undefined) return; // root or a branch seed, already handled
        const parentIds = parentIdsByTarget[node.query_id] || [];
        if (parentIds.length === 1) {
            (singleParentChildrenByParent[parentIds[0]] = singleParentChildrenByParent[parentIds[0]] || []).push(node.query_id);
        }
    });

    const parentDepth = (pid) => (depthById[pid] !== undefined ? depthById[pid] : 0);

    ordered.forEach((node) => {
        if (anchors[node.query_id] !== undefined) return; // root or branch seed, already anchored
        const parentIds = parentIdsByTarget[node.query_id] || [];

        if (parentIds.length === 1) {
            const parentAnchor = anchors[parentIds[0]] !== undefined ? anchors[parentIds[0]] : 50;
            const depth = parentDepth(parentIds[0]) + 1;
            const siblings = singleParentChildrenByParent[parentIds[0]] || [node.query_id];
            // Shift the whole sibling group's fan center by the depth zig-zag
            // (siblings share a depth, so their relative fan is preserved).
            const fanCenter = clampPct(parentAnchor + chainZigzag(depth), 0, 100);
            siblings.forEach((sid) => { depthById[sid] = depth; });
            assignCentralityFannedAnchors(anchors, siblings, fanCenter, downstreamCounts, orderById, CONSTELLATION_SIBLING_FAN_OPTS);
        } else {
            const parentWeights = parentIds
                .map((pid) => ({ anchor: anchors[pid], weight: (downstreamCounts[pid] || 0) + 1 }))
                .filter((p) => p.anchor !== undefined);
            const totalWeight = parentWeights.reduce((sum, p) => sum + p.weight, 0);
            anchors[node.query_id] = totalWeight
                ? parentWeights.reduce((sum, p) => sum + p.anchor * p.weight, 0) / totalWeight
                : 50;
            depthById[node.query_id] = parentIds.reduce((mx, pid) => Math.max(mx, parentDepth(pid)), 0) + 1;
        }
    });

    return anchors;
}

// Backstop spread (percent) for a visual row with 2+ nodes, on top of each
// node's branch anchor, so a row can't collapse into the center when its
// anchors happen to land close together.
const CONSTELLATION_ROW_SPREAD_BASE = 10; // for exactly 2 nodes in a row
const CONSTELLATION_ROW_SPREAD_STEP = 8;  // per node beyond 2
const CONSTELLATION_ROW_SPREAD_MAX = 32;

// Full layout: (1) rankY sets y; (2) branch anchors set x; (3) lane bias +
// row spread nudge x; (4) relaxation and collision resolution finalize x.
function computeConstellationLayout(nodes, edges, containerRect) {
    const edgeList = edges || [];
    const rankY = computeConstellationRankY(nodes, edgeList);
    const branchAnchorX = computeConstellationBranchAnchors(nodes, edgeList);
    const rootId = constellationRootId(nodes);

    const rowGroups = {};
    nodes.forEach((node) => {
        const rowKey = Math.round(rankY[node.query_id] / CONSTELLATION_ROW_GROUP_PCT) * CONSTELLATION_ROW_GROUP_PCT;
        (rowGroups[rowKey] = rowGroups[rowKey] || []).push(node);
    });

    const positions = {};
    Object.keys(rowGroups).forEach((rowKey) => {
        // Group siblings by branch anchor first (so nodes on the same
        // branch stay visually together), order as a tie-break only.
        const group = rowGroups[rowKey].slice().sort((a, b) => {
            const anchorDiff = (branchAnchorX[a.query_id] || 50) - (branchAnchorX[b.query_id] || 50);
            if (Math.abs(anchorDiff) > 0.01) return anchorDiff;
            return (a.order || 0) - (b.order || 0);
        });

        const rowSpread = group.length > 1
            ? Math.min(CONSTELLATION_ROW_SPREAD_MAX, CONSTELLATION_ROW_SPREAD_BASE + CONSTELLATION_ROW_SPREAD_STEP * (group.length - 2))
            : 0;
        const rowStep = group.length > 1 ? (2 * rowSpread) / (group.length - 1) : 0;

        group.forEach((node, i) => {
            // Root renders at exactly x=50, centered under the origin box --
            // no lane bias or row offset.
            if (node.query_id === rootId) {
                positions[node.query_id] = { xPct: 50, yPct: rankY[node.query_id] };
                return;
            }
            const anchor = branchAnchorX[node.query_id] !== undefined ? branchAnchorX[node.query_id] : 50;
            const rowOffset = group.length > 1 ? (i - (group.length - 1) / 2) * rowStep : 0;
            const lane = CONSTELLATION_KNOWN_LANES.has(node.lane) ? node.lane : 'other';
            const bias = CONSTELLATION_LANE_X_BIAS[lane] || 0;
            positions[node.query_id] = {
                xPct: clampPct(anchor + rowOffset + bias, CONSTELLATION_NODE_X_MIN, CONSTELLATION_NODE_X_MAX),
                yPct: rankY[node.query_id],
            };
        });
    });

    // Deterministic x-only relaxation to spread the graph horizontally into a
    // web (see relaxConstellationX) -- y is left untouched, so chronological/
    // downstream flow is preserved -- then collisions are resolved on the
    // relaxed positions.
    relaxConstellationX(nodes, edgeList, positions, branchAnchorX, rootId);
    resolveConstellationCollisions(nodes, positions, containerRect, rootId);
    return positions;
}

// --- Deterministic x-only relaxation ------------------------------------
// Fixed-iteration force pass on the HORIZONTAL axis only (y is untouched, so
// chronological/downstream flow is preserved) to spread the map into a web
// rather than a spine. Four forces, strongest first: node-node repulsion
// (only within a vertical band), weak branch-anchor attraction, very weak
// edge spring (kept weaker than repulsion so it can't re-straighten chains),
// and a faint lane bias. Deterministic: fixed iterations, no Math.random,
// movement and positions clamped every iteration.
const CONSTELLATION_RELAX_ITERATIONS = 60;
const CONSTELLATION_RELAX_Y_WINDOW_PCT = 18;   // only nodes within this vertical distance repel
const CONSTELLATION_RELAX_REPULSION = 9;       // horizontal repulsion strength (percent-space)
const CONSTELLATION_RELAX_REPULSION_SOFTEN = 30; // softening so very-close nodes don't fling apart
const CONSTELLATION_RELAX_ANCHOR_PULL = 0.05;  // weak pull back toward branch anchor
const CONSTELLATION_RELAX_EDGE_SPRING = 0.015; // very weak parent<->child x attraction
const CONSTELLATION_RELAX_LANE_BIAS = 0.01;    // faint per-lane nudge
const CONSTELLATION_RELAX_DAMPING = 0.5;
const CONSTELLATION_RELAX_MAX_STEP_PCT = 2.2;  // clamp per-node movement per iteration

function relaxConstellationX(nodes, edges, positions, branchAnchorX, rootId) {
    const ids = nodes.map((n) => n.query_id).filter((id) => positions[id]);
    if (ids.length < 2) return;

    const laneById = {};
    nodes.forEach((n) => {
        laneById[n.query_id] = CONSTELLATION_KNOWN_LANES.has(n.lane) ? n.lane : 'other';
    });

    // Only primary_flow/convergence edges spring (a bare secondary_reference
    // shouldn't pull two nodes together in the layout).
    const springEdges = edges.filter(
        (e) => (e.edge_type === 'primary_flow' || e.edge_type === 'convergence')
            && positions[e.source] && positions[e.target],
    );

    for (let iter = 0; iter < CONSTELLATION_RELAX_ITERATIONS; iter += 1) {
        const force = {};
        ids.forEach((id) => { force[id] = 0; });

        // Repulsion between nodes sharing a vertical band.
        for (let i = 0; i < ids.length; i += 1) {
            for (let j = i + 1; j < ids.length; j += 1) {
                const a = ids[i];
                const b = ids[j];
                const pa = positions[a];
                const pb = positions[b];
                if (Math.abs(pa.yPct - pb.yPct) > CONSTELLATION_RELAX_Y_WINDOW_PCT) continue;
                let dx = pa.xPct - pb.xPct;
                // Deterministic tie-break when exactly overlapping in x: earlier
                // id in the list goes left. Never Math.random.
                if (dx === 0) dx = i < j ? -0.01 : 0.01;
                const push = (CONSTELLATION_RELAX_REPULSION * dx)
                    / (dx * dx + CONSTELLATION_RELAX_REPULSION_SOFTEN);
                force[a] += push;
                force[b] -= push;
            }
        }

        // Branch-anchor attraction + lane bias (per node).
        ids.forEach((id) => {
            const anchor = branchAnchorX[id] !== undefined ? branchAnchorX[id] : 50;
            force[id] += CONSTELLATION_RELAX_ANCHOR_PULL * (anchor - positions[id].xPct);
            const bias = CONSTELLATION_LANE_X_BIAS[laneById[id]] || 0;
            force[id] += CONSTELLATION_RELAX_LANE_BIAS * bias;
        });

        // Edge springs (very weak).
        springEdges.forEach((e) => {
            const dx = positions[e.target].xPct - positions[e.source].xPct;
            force[e.source] += CONSTELLATION_RELAX_EDGE_SPRING * dx;
            force[e.target] -= CONSTELLATION_RELAX_EDGE_SPRING * dx;
        });

        // Apply (root stays fixed at center so the map keeps a stable top anchor).
        ids.forEach((id) => {
            if (id === rootId) return;
            const step = clampPct(
                force[id] * CONSTELLATION_RELAX_DAMPING,
                -CONSTELLATION_RELAX_MAX_STEP_PCT,
                CONSTELLATION_RELAX_MAX_STEP_PCT,
            );
            positions[id].xPct = clampPct(
                positions[id].xPct + step,
                CONSTELLATION_NODE_X_MIN,
                CONSTELLATION_NODE_X_MAX,
            );
        });
    }
}

// Breathing room (px) added to the two nodes' radii when testing overlap, so
// a node scaled up 1.35x on hover doesn't touch a resting neighbor.
const CONSTELLATION_NODE_PADDING_PX = 20;
const CONSTELLATION_COLLISION_MAX_ITERATIONS = 8;

// Deterministic overlap resolution using each node's real rendered size, in
// two phases: (1) horizontal-only separation, preserving every node's y;
// (2) a vertical top-up for pairs phase 1 couldn't separate, only ever
// pushing the chronologically later node further DOWN. Positions are clamped
// to panel bounds after every pass.
function resolveConstellationCollisions(nodes, positions, containerRect, rootId) {
    const width = (containerRect && containerRect.width) || 420;
    const height = (containerRect && containerRect.height) || 420;
    if (!width || !height) return;

    const nodesById = {};
    nodes.forEach((n) => { nodesById[n.query_id] = n; });
    const ids = nodes.map((n) => n.query_id).filter((id) => positions[id]);

    const minDistPxFor = (a, b) => (
        constellationNodeDiameter(nodesById[a]) / 2
        + constellationNodeDiameter(nodesById[b]) / 2
        + CONSTELLATION_NODE_PADDING_PX
    );

    for (let iter = 0; iter < CONSTELLATION_COLLISION_MAX_ITERATIONS; iter += 1) {
        let moved = false;
        for (let i = 0; i < ids.length; i += 1) {
            for (let j = i + 1; j < ids.length; j += 1) {
                const a = ids[i];
                const b = ids[j];
                const pa = positions[a];
                const pb = positions[b];
                const dxPx = (pb.xPct - pa.xPct) / 100 * width;
                const dyPx = (pb.yPct - pa.yPct) / 100 * height;
                const dist = Math.sqrt(dxPx * dxPx + dyPx * dyPx);
                const minDist = minDistPxFor(a, b);
                if (dist >= minDist) continue;
                const dir = dxPx === 0 ? (i < j ? -1 : 1) : Math.sign(dxPx);
                // The root stays centered under Selected Hypothesis (requirement:
                // "Root remains centered"), so it never moves here -- if one
                // side of the pair is the root, the OTHER node absorbs the full
                // separation instead of each moving half.
                const aFixed = a === rootId;
                const bFixed = b === rootId;
                const total = (minDist - dist) + 1.0;
                const aShare = aFixed ? 0 : (bFixed ? total : total / 2);
                const bShare = bFixed ? 0 : (aFixed ? total : total / 2);
                pa.xPct = clampPct(pa.xPct - (dir * aShare / width) * 100, CONSTELLATION_NODE_X_MIN, CONSTELLATION_NODE_X_MAX);
                pb.xPct = clampPct(pb.xPct + (dir * bShare / width) * 100, CONSTELLATION_NODE_X_MIN, CONSTELLATION_NODE_X_MAX);
                moved = true;
            }
        }
        if (!moved) break;
    }

    for (let iter = 0; iter < CONSTELLATION_COLLISION_MAX_ITERATIONS; iter += 1) {
        let moved = false;
        for (let i = 0; i < ids.length; i += 1) {
            for (let j = i + 1; j < ids.length; j += 1) {
                const a = ids[i];
                const b = ids[j];
                const pa = positions[a];
                const pb = positions[b];
                const dxPx = (pb.xPct - pa.xPct) / 100 * width;
                const dyPx = (pb.yPct - pa.yPct) / 100 * height;
                const dist = Math.sqrt(dxPx * dxPx + dyPx * dyPx);
                const minDist = minDistPxFor(a, b);
                if (dist >= minDist) continue;
                const laterId = (nodesById[a].order || 0) <= (nodesById[b].order || 0) ? b : a;
                const laterPos = positions[laterId];
                const nudgePx = (minDist - dist) * 0.6 + 0.5;
                laterPos.yPct = clampPct(laterPos.yPct + (nudgePx / height) * 100, CONSTELLATION_TOP_QUERY_Y, CONSTELLATION_BOTTOM_QUERY_Y);
                moved = true;
            }
        }
        if (!moved) break;
    }
}

function constellationNodeClass(node) {
    const lane = CONSTELLATION_KNOWN_LANES.has(node.lane) ? node.lane : 'other';
    let cls = `rm-constellation-node rm-constellation-node--${lane}`;
    if (node.status === 'completed') cls += ' is-completed';
    if (node.status === 'running') cls += ' is-running';
    return cls;
}

const CONSTELLATION_NODE_DIAMETER_MIN = 52;
const CONSTELLATION_NODE_DIAMETER_RANGE = 38; // min + range = 90px max

// Continuous node diameter from artifact_richness (0.0-1.0, see
// _compute_artifact_richness in app.py): 52px at 0, 90px at 1, linear
// between, applied via the --node-size custom property per node.
function constellationNodeDiameter(node) {
    const richness = typeof node.artifact_richness === 'number' ? node.artifact_richness : 0;
    const clamped = Math.min(1, Math.max(0, richness));
    return CONSTELLATION_NODE_DIAMETER_MIN + clamped * CONSTELLATION_NODE_DIAMETER_RANGE;
}

function createConstellationNodeButton(node, position) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = constellationNodeClass(node);
    btn.style.left = `${position.xPct}%`;
    btn.style.top = `${position.yPct}%`;
    btn.style.setProperty('--node-size', `${constellationNodeDiameter(node)}px`);
    btn.textContent = node.display_label || node.query_id;
    btn.dataset.queryId = node.query_id;
    btn.setAttribute('aria-label', `${node.display_label || node.query_id}, ${formatQueryStatusLabel(node.status)}`);
    return btn;
}

// The active node's immediate upstream (parents) and downstream (children),
// one hop each way via primary_flow/convergence edges only.
function constellationUpstreamDownstream(queryId) {
    const upstream = new Set();
    const downstream = new Set();
    if (!lastConstellationData) return { upstream, downstream };
    const node = lastConstellationData.nodesById[queryId];
    if (node && Array.isArray(node.parents)) {
        node.parents.forEach((parentId) => upstream.add(parentId));
    }
    lastConstellationData.edges.forEach((edge) => {
        if (edge.edge_type === 'secondary_reference') return;
        if (edge.source === queryId) downstream.add(edge.target);
    });
    return { upstream, downstream };
}

// Applies is-active/is-upstream/is-downstream/is-connected/is-dimmed to nodes
// and edges. A highlighted primary/convergence edge also swaps its marker-end
// to the accent arrowhead, since shared SVG <marker> defs can't recolor via CSS.
function applyConstellationActiveState(queryId) {
    const nodesContainer = document.getElementById('rm-constellation-nodes');
    const edgesSvg = document.getElementById('rm-constellation-edges');
    if (!nodesContainer || !edgesSvg) return;
    const { upstream, downstream } = queryId
        ? constellationUpstreamDownstream(queryId)
        : { upstream: new Set(), downstream: new Set() };
    const related = queryId ? new Set([queryId, ...upstream, ...downstream]) : null;

    nodesContainer.querySelectorAll('.rm-constellation-node').forEach((btn) => {
        const id = btn.dataset.queryId;
        const isActive = id === queryId;
        const isUpstream = upstream.has(id);
        const isDownstream = downstream.has(id);
        btn.classList.toggle('is-active', isActive);
        btn.classList.toggle('is-upstream', isUpstream);
        btn.classList.toggle('is-downstream', isDownstream);
        btn.classList.toggle('is-connected', isActive || isUpstream || isDownstream);
        btn.classList.toggle('is-dimmed', Boolean(related) && !related.has(id));
    });

    edgesSvg.querySelectorAll('line').forEach((line) => {
        const { source, target, edgeType } = line.dataset;

        if (edgeType === 'secondary_reference') {
            // Not part of the upstream/downstream traversal: a faint reference
            // edge is only visible when the active node is one of its two ends.
            const touchesActive = Boolean(queryId) && (source === queryId || target === queryId);
            line.classList.toggle('is-highlighted', touchesActive);
            line.classList.toggle('is-dimmed', Boolean(queryId) && !touchesActive);
            return;
        }

        const isUpstreamEdge = Boolean(queryId) && target === queryId && upstream.has(source);
        const isDownstreamEdge = Boolean(queryId) && source === queryId && downstream.has(target);
        const bothEndsRelated = Boolean(related) && related.has(source) && related.has(target);

        line.classList.toggle('is-upstream', isUpstreamEdge);
        line.classList.toggle('is-downstream', isDownstreamEdge);
        line.classList.toggle('is-highlighted', bothEndsRelated);
        line.classList.toggle('is-dimmed', Boolean(related) && !bothEndsRelated);

        line.setAttribute(
            'marker-end',
            `url(#${bothEndsRelated ? CONSTELLATION_ARROW_HIGHLIGHTED_ID : CONSTELLATION_ARROW_ID})`,
        );
    });
}

// Compact tooltip card anchored to the active node.
const CONSTELLATION_CARD_MARGIN_PX = 10; // breathing room from the panel's own edges
const CONSTELLATION_CARD_NODE_GAP_PX = 14; // gap between the card and the node it describes

// Positions the card against the panel's real pixel box so it stays fully
// inside the panel: prefers sitting above the node, flips below near the top
// edge, and clamps left/right/bottom independently.
function showConstellationCard(node, position) {
    const card = document.getElementById('rm-constellation-card');
    const container = document.getElementById('rm-constellation');
    if (!card || !container) return;
    card.replaceChildren();

    const title = document.createElement('div');
    title.className = 'rm-constellation-card-title';
    title.textContent = node.display_label || node.query_id;
    card.appendChild(title);

    const status = document.createElement('div');
    status.className = 'rm-constellation-card-status';
    status.textContent = `(${formatQueryStatusLabel(node.status)})`;
    card.appendChild(status);

    const lane = CONSTELLATION_KNOWN_LANES.has(node.lane) ? node.lane : 'other';
    const lanePill = document.createElement('span');
    lanePill.className = `rm-constellation-card-pill rm-constellation-node--${lane}`;
    lanePill.textContent = lane;
    card.appendChild(lanePill);

    if (node.summary) {
        const summary = document.createElement('div');
        summary.className = 'rm-constellation-card-summary';
        summary.textContent = node.summary;
        card.appendChild(summary);
    }

    // Make visible (but not yet correctly positioned) so its rendered size
    // can be measured below -- min/max-width in CSS bound that size.
    card.style.left = '0px';
    card.style.top = '0px';
    card.classList.remove('hidden');

    const containerRect = container.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const cardWidth = cardRect.width || 260;
    const cardHeight = cardRect.height || 60;

    const nodeX = (position.xPct / 100) * containerRect.width;
    const nodeY = (position.yPct / 100) * containerRect.height;

    let left = nodeX - cardWidth / 2;
    let top = nodeY - cardHeight - CONSTELLATION_CARD_NODE_GAP_PX;
    if (top < CONSTELLATION_CARD_MARGIN_PX) {
        // Not enough room above the node (e.g. it's near the top) -- place
        // it below instead.
        top = nodeY + CONSTELLATION_CARD_NODE_GAP_PX;
    }

    const maxLeft = containerRect.width - cardWidth - CONSTELLATION_CARD_MARGIN_PX;
    left = Math.min(Math.max(left, CONSTELLATION_CARD_MARGIN_PX), Math.max(maxLeft, CONSTELLATION_CARD_MARGIN_PX));

    const maxTop = containerRect.height - cardHeight - CONSTELLATION_CARD_MARGIN_PX;
    top = Math.min(Math.max(top, CONSTELLATION_CARD_MARGIN_PX), Math.max(maxTop, CONSTELLATION_CARD_MARGIN_PX));

    card.style.left = `${left}px`;
    card.style.top = `${top}px`;
}

function hideConstellationCard() {
    const card = document.getElementById('rm-constellation-card');
    if (card) card.classList.add('hidden');
}

function selectConstellationNode(node, position) {
    constellationSelectedQueryId = node.query_id;
    clearTimeout(constellationHoverTimer);
    constellationHoverTimer = null;
    applyConstellationActiveState(node.query_id);
    showConstellationCard(node, position);
}

function clearConstellationSelection() {
    constellationSelectedQueryId = null;
    clearTimeout(constellationHoverTimer);
    constellationHoverTimer = null;
    applyConstellationActiveState(null);
    hideConstellationCard();
}

function attachConstellationNodeHandlers(btn, node, position) {
    const activate = () => {
        constellationHoverTimer = null;
        applyConstellationActiveState(node.query_id);
        showConstellationCard(node, position);
    };
    const startDwell = () => {
        if (constellationSelectedQueryId) return; // a persisted selection isn't disturbed by hovering elsewhere
        clearTimeout(constellationHoverTimer);
        constellationHoverTimer = setTimeout(activate, CONSTELLATION_HOVER_DWELL_MS);
    };
    const cancelDwell = () => {
        clearTimeout(constellationHoverTimer);
        constellationHoverTimer = null;
        if (constellationSelectedQueryId) return; // keep the persisted selection visible
        applyConstellationActiveState(null);
        hideConstellationCard();
    };

    btn.addEventListener('mouseenter', startDwell);
    btn.addEventListener('mouseleave', cancelDwell);
    btn.addEventListener('focus', startDwell);
    btn.addEventListener('blur', cancelDwell);
    btn.addEventListener('click', (event) => {
        event.stopPropagation();
        selectConstellationNode(node, position);
    });
}

// Attached once (guarded) rather than per-render, since the outer
// #rm-constellation element itself is never rebuilt -- only its
// nodes/edges children are, on every poll.
function setupConstellationDismissHandlers() {
    if (constellationDismissHandlersAttached) return;
    constellationDismissHandlersAttached = true;
    const container = document.getElementById('rm-constellation');
    if (!container) return;
    container.addEventListener('click', (event) => {
        if (event.target === container
            || event.target.id === 'rm-constellation-nodes'
            || event.target.id === 'rm-constellation-edges') {
            clearConstellationSelection();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') clearConstellationSelection();
    });
}

const SVG_NS = 'http://www.w3.org/2000/svg';
// Backend sentinel for the Selected Hypothesis "node" (see
// _CONSTELLATION_ORIGIN_ID in app.py) -- edges with this as `source` connect
// from the origin box rather than another query.
const CONSTELLATION_ORIGIN_ID = '__origin__';
const CONSTELLATION_ARROW_ID = 'rm-constellation-arrow';
const CONSTELLATION_ARROW_HIGHLIGHTED_ID = 'rm-constellation-arrow-highlighted';

// Downstream-pointing arrowhead in two color variants (neutral / accent) so a
// line's marker-end can be swapped on highlight without per-line markers. Only
// attached to primary_flow/convergence edges, never secondary_reference.
function addConstellationArrowMarkers(edgesSvg) {
    const defs = document.createElementNS(SVG_NS, 'defs');
    [
        [CONSTELLATION_ARROW_ID, 'rm-constellation-arrow-fill'],
        [CONSTELLATION_ARROW_HIGHLIGHTED_ID, 'rm-constellation-arrow-fill--highlighted'],
    ].forEach(([id, cls]) => {
        const marker = document.createElementNS(SVG_NS, 'marker');
        marker.setAttribute('id', id);
        marker.setAttribute('viewBox', '0 0 8 8');
        marker.setAttribute('refX', '7');
        marker.setAttribute('refY', '4');
        marker.setAttribute('markerWidth', '4.5');
        marker.setAttribute('markerHeight', '4.5');
        marker.setAttribute('orient', 'auto-start-reverse');
        const path = document.createElementNS(SVG_NS, 'path');
        path.setAttribute('d', 'M0,0 L8,4 L0,8 Z');
        path.setAttribute('class', cls);
        marker.appendChild(path);
        defs.appendChild(marker);
    });
    edgesSvg.appendChild(defs);
}

// Thin straight line segments. Appearance lives in CSS; this sets endpoints,
// edge_type (so CSS can fade secondary_reference edges), and an arrowhead on
// primary_flow/convergence edges only.
function renderConstellationEdges(edgesSvg, edges, positions) {
    addConstellationArrowMarkers(edgesSvg);
    edges.forEach((edge) => {
        const sourcePos = edge.source === CONSTELLATION_ORIGIN_ID ? CONSTELLATION_ORIGIN_ANCHOR : positions[edge.source];
        const targetPos = positions[edge.target];
        if (!sourcePos || !targetPos) return; // e.g. an edge to a non-numeric query_* dir with no node

        const edgeType = edge.edge_type === 'secondary_reference' ? 'secondary_reference'
            : (edge.edge_type === 'convergence' ? 'convergence' : 'primary_flow');

        const line = document.createElementNS(SVG_NS, 'line');
        line.setAttribute('x1', sourcePos.xPct);
        line.setAttribute('y1', sourcePos.yPct);
        line.setAttribute('x2', targetPos.xPct);
        line.setAttribute('y2', targetPos.yPct);
        line.dataset.source = edge.source;
        line.dataset.target = edge.target;
        line.dataset.edgeType = edgeType;
        line.classList.add(`rm-constellation-edge--${edgeType}`);
        if (edgeType !== 'secondary_reference') {
            line.setAttribute('marker-end', `url(#${CONSTELLATION_ARROW_ID})`);
        }
        edgesSvg.appendChild(line);
    });
}

function renderConstellation(constellation) {
    const nodesContainer = document.getElementById('rm-constellation-nodes');
    const edgesSvg = document.getElementById('rm-constellation-edges');
    const emptyEl = document.getElementById('rm-constellation-empty');
    if (!nodesContainer || !edgesSvg || !emptyEl) return;

    setupConstellationDismissHandlers();

    const previousSelectedId = constellationSelectedQueryId;
    nodesContainer.replaceChildren();
    edgesSvg.replaceChildren();
    hideConstellationCard();
    constellationSelectedQueryId = null;
    clearTimeout(constellationHoverTimer);
    constellationHoverTimer = null;

    const nodes = (constellation && Array.isArray(constellation.nodes)) ? constellation.nodes : [];
    const edges = (constellation && Array.isArray(constellation.edges)) ? constellation.edges : [];

    if (nodes.length === 0) {
        emptyEl.classList.remove('hidden');
        lastConstellationData = null;
        return;
    }
    emptyEl.classList.add('hidden');

    const constellationEl = document.getElementById('rm-constellation');
    const containerRect = constellationEl ? constellationEl.getBoundingClientRect() : null;
    const positions = computeConstellationLayout(nodes, edges, containerRect);
    const nodesById = {};
    nodes.forEach((node) => { nodesById[node.query_id] = node; });
    lastConstellationData = { nodesById, edges, positions };

    edgesSvg.setAttribute('viewBox', '0 0 100 100');
    edgesSvg.setAttribute('preserveAspectRatio', 'none');
    renderConstellationEdges(edgesSvg, edges, positions);

    nodes.forEach((node) => {
        const position = positions[node.query_id];
        if (!position) return;
        const btn = createConstellationNodeButton(node, position);
        attachConstellationNodeHandlers(btn, node, position);
        nodesContainer.appendChild(btn);
    });

    // Preserve the selection across polling refreshes if that query still exists.
    if (previousSelectedId && nodesById[previousSelectedId]) {
        selectConstellationNode(nodesById[previousSelectedId], positions[previousSelectedId]);
    }
}

// --- DiscoveryAgent Activity feed --------------------------------------
// Renders only the deterministic, pre-summarized events from
// /api/round_activity -- never raw log text.

// Treat the user as "at the bottom" within this many pixels, so
// auto-scroll-on-new-content doesn't need pixel-perfect positioning.
const NEAR_BOTTOM_THRESHOLD_PX = 40;

function isNearBottom(el) {
    return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_THRESHOLD_PX;
}

function scrollToBottom(el) {
    el.scrollTop = el.scrollHeight;
}

function formatActivityTimestamp(ts) {
    // Show time-of-day only, never a full date -- try to pull an HH:MM:SS
    // substring directly first (covers "2026-06-23 12:34:25", ISO strings
    // with a literal "T", etc. without any timezone-conversion surprises);
    // only fall back to Date parsing (e.g. for epoch millis) if that fails.
    if (!ts) return '--:--:--';
    const str = String(ts).trim();
    const direct = str.match(/(\d{2}:\d{2}:\d{2})/);
    if (direct) return direct[1];
    const date = new Date(str);
    if (!isNaN(date.getTime())) {
        const pad = (n) => String(n).padStart(2, '0');
        return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
    }
    return str;
}

// /api/round_activity's structured timeline, or null to fall back to the
// legacy flat `events` payload (see normalizeFallbackItem).
function pickStructuredTimeline(result) {
    return Array.isArray(result.discovery_activity) ? result.discovery_activity : null;
}

// Formats one backend director_activity entry ({kind, label, summary, ...})
// into a bullet string via a fixed template keyed off `kind`. Uses the
// backend's text verbatim; returns null when there's no summary to show.
function formatDirectorActivityEntry(entry) {
    entry = entry || {};
    const label = typeof entry.label === 'string' ? entry.label.trim() : '';
    const summary = typeof entry.summary === 'string' ? entry.summary.trim() : '';
    if (!summary) return null;
    switch (entry.kind) {
        case 'subagent':
            return label ? `Director called ${label} — ${summary}` : `Director — ${summary}`;
        case 'artifact':
            return `Director saved artifacts — ${summary}`;
        case 'error':
            return `Director encountered an error — ${summary}`;
        default:
            return label ? `${label} — ${summary}` : summary;
    }
}

// Normalizes one structured backend timeline entry. discovery_activity items
// use kind: 'query' for a per-query row (any other kind is a round-level row).
// Field names are kept flexible (query_id/query/id, summary/message); `actions`
// bullets come only from backend director_activity entries.
function normalizeStructuredItem(raw, index) {
    raw = raw || {};
    const queryIdRaw = raw.query_id || raw.query || raw.id || null;
    const isQuery = Boolean(queryIdRaw) && (raw.kind ? raw.kind === 'query' : true);
    const actions = Array.isArray(raw.director_activity)
        ? raw.director_activity.map(formatDirectorActivityEntry).filter(Boolean)
        : [];
    return {
        key: queryIdRaw ? String(queryIdRaw) : `evt-${index}-${raw.timestamp || ''}`,
        timestamp: raw.timestamp || null,
        isQuery,
        queryId: isQuery ? String(queryIdRaw) : null,
        status: raw.status || null,
        summary: raw.summary || raw.message || '',
        actions,
    };
}

const FALLBACK_STATUS_KEYWORDS = ['started', 'completed', 'failed', 'running'];

// Normalizes one entry of the legacy flat `events` payload into the same
// shape normalizeStructuredItem produces. `actions` is always [] here.
function normalizeFallbackItem(event, index) {
    event = event || {};
    const isQuery = event.kind === 'query_event' && Boolean(event.query);
    if (!isQuery) {
        return {
            key: `evt-${index}-${event.timestamp || ''}`,
            timestamp: event.timestamp || null,
            isQuery: false,
            queryId: null,
            status: null,
            summary: event.message || '',
            actions: [],
        };
    }
    const message = event.message || '';
    const colonIdx = message.indexOf(': ');
    const head = colonIdx >= 0 ? message.slice(0, colonIdx) : message;
    const rest = colonIdx >= 0 ? message.slice(colonIdx + 2) : '';
    const queryId = head.split(/\s+/)[0] || `query_${event.query}`;
    const status = FALLBACK_STATUS_KEYWORDS.find((kw) => head.includes(kw)) || 'unknown';
    return {
        key: queryId,
        timestamp: event.timestamp || null,
        isQuery: true,
        queryId,
        status,
        summary: rest || message,
        actions: [],
    };
}

// query_id -> expanded/collapsed, preserved across polling refreshes (a
// stable key, not row position, so it survives the list being rebuilt).
const expandedDiscoveryQueryIds = new Set();
let lastDiscoveryActivityItems = [];
let lastActivitySignature = null;

function toggleDiscoveryActivityItem(key) {
    if (expandedDiscoveryQueryIds.has(key)) {
        expandedDiscoveryQueryIds.delete(key);
    } else {
        expandedDiscoveryQueryIds.add(key);
    }
    renderDiscoveryActivity(lastDiscoveryActivityItems);
}

// Maps a query timeline status onto the present/missing/completed/... keys
// statusMarker() and the .rm-tree-marker CSS rules use.
function discoveryStatusMarkerKey(status) {
    const key = String(status || 'unknown').replace(/\s+/g, '_');
    if (key === 'in_progress' || key === 'started') return 'in_progress';
    return key; // completed / running / failed / discovered / unknown all match statusMarker() as-is
}

// Reuses the shared query-row classes (rm-tree-marker, rm-query-name,
// rm-query-status, rm-query-description); the time column and expand/collapse
// caret are specific to this feed. The caret sits after the time column so it
// stays grouped with the query id/status it toggles.
function renderDiscoveryActivityItem(item) {
    const wrapper = document.createElement('div');
    wrapper.className = 'discovery-activity-item';

    const hasActions = item.actions.length > 0;
    const isExpanded = hasActions && expandedDiscoveryQueryIds.has(item.key);

    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'discovery-activity-row' + (hasActions ? '' : ' no-details');
    row.setAttribute('aria-expanded', String(isExpanded));
    if (!hasActions) row.disabled = true;

    const time = document.createElement('span');
    time.className = 'discovery-activity-time';
    time.textContent = formatActivityTimestamp(item.timestamp);
    row.appendChild(time);

    const caret = document.createElement('span');
    caret.className = 'discovery-activity-caret';
    caret.textContent = hasActions ? (isExpanded ? '▾' : '▸') : '';
    row.appendChild(caret);

    const marker = document.createElement('span');
    const markerKey = item.isQuery ? discoveryStatusMarkerKey(item.status) : null;
    marker.className = 'discovery-activity-marker rm-tree-marker' + (markerKey ? ` ${markerKey}` : '');
    marker.textContent = markerKey ? statusMarker(markerKey) : '';
    row.appendChild(marker);

    const main = document.createElement('span');
    main.className = 'discovery-activity-main';

    if (item.isQuery && item.queryId) {
        const queryEl = document.createElement('strong');
        queryEl.className = 'discovery-activity-query rm-query-name';
        queryEl.textContent = item.queryId;
        main.appendChild(queryEl);

        const statusEl = document.createElement('span');
        statusEl.className = 'discovery-activity-status rm-query-status';
        statusEl.textContent = ` (${formatQueryStatusLabel(item.status)})`;
        main.appendChild(statusEl);
    }

    if (item.summary) {
        const summaryEl = document.createElement('span');
        summaryEl.className = 'discovery-activity-summary rm-query-description';
        summaryEl.textContent = item.isQuery && item.queryId ? ` — ${item.summary}` : item.summary;
        main.appendChild(summaryEl);
    }

    row.appendChild(main);

    if (hasActions) {
        row.addEventListener('click', () => toggleDiscoveryActivityItem(item.key));
    }

    wrapper.appendChild(row);

    if (hasActions) {
        const list = document.createElement('ul');
        list.className = 'discovery-activity-actions' + (isExpanded ? '' : ' hidden');
        item.actions.forEach((action) => {
            const li = document.createElement('li');
            li.textContent = action;
            list.appendChild(li);
        });
        wrapper.appendChild(list);
    }

    return wrapper;
}

function renderDiscoveryActivity(items) {
    const container = document.getElementById('rm-activity-content');
    if (!container) return;
    // This rebuilds the whole list every poll (and on every expand/collapse
    // toggle), which would otherwise reset scrollTop to 0 -- capture
    // position BEFORE clearing so it can be restored (or kept pinned to the
    // bottom) after rebuilding.
    const wasNearBottom = isNearBottom(container);
    const previousScrollTop = container.scrollTop;
    container.innerHTML = '';

    if (!items || items.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'rm-activity-empty';
        empty.textContent = 'No notable activity detected yet.';
        container.appendChild(empty);
        return;
    }

    items.forEach((item) => container.appendChild(renderDiscoveryActivityItem(item)));

    if (wasNearBottom) {
        scrollToBottom(container);
    } else {
        container.scrollTop = previousScrollTop;
    }
}

async function refreshRoundActivity() {
    const statusEl = document.getElementById('rm-log-status');
    if (!statusEl) return;
    try {
        const result = await apiCall('/api/round_activity');
        if (!result.success) {
            statusEl.textContent = result.error || 'Activity unavailable.';
            return;
        }
        // Events can be populated from query directory/file evidence alone
        // even when no log file exists yet, so always render them -- only
        // the status line differs (a graceful note instead of being empty).
        statusEl.textContent = result.exists ? '' : (result.message || 'No live log found yet.');

        const structuredTimeline = pickStructuredTimeline(result);
        const items = structuredTimeline
            ? structuredTimeline.map((raw, i) => normalizeStructuredItem(raw, i))
            : (result.events || []).map((event, i) => normalizeFallbackItem(event, i));
        // Skip the DOM rebuild when nothing changed since the last poll
        // (expand/collapse re-renders call renderDiscoveryActivity directly,
        // so they are unaffected by this guard).
        const signature = JSON.stringify(items);
        if (signature === lastActivitySignature) return;
        lastActivitySignature = signature;
        lastDiscoveryActivityItems = items;
        renderDiscoveryActivity(items);
    } catch (err) {
        statusEl.textContent = 'Error loading activity.';
        console.error('round_activity error', err);
    }
}

// --- Audit Agent Chat -----------------------------------------------------
// Stateless on the backend (each /api/round_chat call is a fresh single-turn
// request), so "reset" only needs to clear the client-side message list/DOM.

function isUserChatRole(role) {
    return String(role || '').toLowerCase() === 'user';
}

function renderMarkdownToElement(element, text) {
    if (!window.marked) {
        console.warn('[round_monitor] marked not loaded; falling back to plain text. Check CDN.');
        element.textContent = text || '';
        return;
    }
    try {
        // Custom renderer for chat context.
        // Headings are converted to <p class="chat-md-heading"> instead of <h1>-<h4>
        // to avoid inheriting .card h2/h3 document-style sizes and margins from style.css.
        const renderer = new window.marked.Renderer();
        renderer.heading = (token) => {
            const t = (token && token.text != null) ? token.text : String(token || '');
            return '<p class="chat-md-heading">' + t + '</p>\n';
        };

        let html;
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(
                window.marked.parse(text || '', { breaks: true, gfm: true, renderer })
            );
        } else {
            // DOMPurify unavailable: still render Markdown syntax, but escape any raw HTML
            // blocks or inline tags so they appear as visible text rather than being injected.
            console.warn('[round_monitor] DOMPurify not loaded; raw HTML in model output will be escaped, not rendered.');
            const escHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            renderer.html = (token) => escHtml(
                (token && token.text != null) ? token.text : String(token || '')
            );
            html = window.marked.parse(text || '', { breaks: true, gfm: true, renderer });
        }
        element.innerHTML = html;
    } catch (err) {
        console.warn('[round_monitor] Markdown render error, falling back to plain text:', err);
        element.textContent = text || '';
    }
}

function appendChatBubble(role, text) {
    const messagesEl = document.getElementById('rm-chat-messages');
    if (!messagesEl) return null;
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${isUserChatRole(role) ? 'chat-user' : 'chat-assistant'}`;
    const label = document.createElement('div');
    label.className = 'chat-bubble-label';
    label.textContent = isUserChatRole(role) ? 'You' : 'Audit Agent';
    const content = document.createElement('div');
    if (isUserChatRole(role)) {
        content.className = 'chat-bubble-content';
        content.textContent = text || '';
    } else {
        content.className = 'chat-bubble-content rendered-markdown';
        renderMarkdownToElement(content, text || '');
    }
    bubble.appendChild(label);
    bubble.appendChild(content);
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
}

function appendLoadingBubble() {
    const messagesEl = document.getElementById('rm-chat-messages');
    if (!messagesEl) return null;
    const bubble = document.createElement('div');
    bubble.className = 'chat-bubble chat-assistant';
    const label = document.createElement('div');
    label.className = 'chat-bubble-label';
    label.textContent = 'Audit Agent';
    const body = document.createElement('div');
    body.className = 'chat-bubble-content';
    body.innerHTML =
        '<div class="chat-spinner" aria-label="Assistant is thinking">' +
        '<span></span><span></span><span></span></div>' +
        '<div class="chat-spinner-text">Thinking…</div>';
    bubble.appendChild(label);
    bubble.appendChild(body);
    messagesEl.appendChild(bubble);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return bubble;
}

function showGreeting() {
    const messagesEl = document.getElementById('rm-chat-messages');
    if (!messagesEl) return;
    messagesEl.innerHTML = '';
    appendChatBubble('assistant', GREETING_TEXT);
}

function resetRoundChat() {
    chatMode = 'queries';
    const toggle = document.getElementById('rm-chat-mode-toggle');
    if (toggle) {
        toggle.querySelectorAll('.chat-mode-button').forEach((btn) => {
            btn.classList.toggle('active', btn.dataset.mode === 'queries');
        });
    }
    showGreeting();
}

function setupChatModeToggle() {
    const toggle = document.getElementById('rm-chat-mode-toggle');
    if (!toggle) return;
    toggle.querySelectorAll('.chat-mode-button').forEach((btn) => {
        btn.addEventListener('click', () => {
            chatMode = btn.dataset.mode;
            toggle.querySelectorAll('.chat-mode-button').forEach((b) => {
                b.classList.toggle('active', b === btn);
            });
        });
    });
}

function setupChatPanel() {
    showGreeting();
    setupChatModeToggle();

    const resetBtn = document.getElementById('rm-chat-reset-btn');
    if (resetBtn) {
        resetBtn.addEventListener('click', resetRoundChat);
    }

    const form = document.getElementById('rm-chat-form');
    const input = document.getElementById('rm-chat-input');
    if (!form || !input) return;

    // Enter sends the message; Shift+Enter inserts a newline (same as the
    // Hypothesis Agent Chat's #chat-input handling in script.js).
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            form.requestSubmit();
        }
    });

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const message = input.value.trim();
        if (!message) return;
        appendChatBubble('user', message);
        input.value = '';
        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.disabled = true;

        const loadingBubble = appendLoadingBubble();
        try {
            const result = await apiCall('/api/round_chat', { message, mode: chatMode });
            if (loadingBubble && loadingBubble.parentNode) loadingBubble.parentNode.removeChild(loadingBubble);
            if (result.success) {
                appendChatBubble('assistant', result.answer);
            } else {
                appendChatBubble('assistant', `Error: ${result.error || 'Unable to answer right now.'}`);
            }
        } catch (err) {
            if (loadingBubble && loadingBubble.parentNode) loadingBubble.parentNode.removeChild(loadingBubble);
            appendChatBubble('assistant', `Error: ${err.message}`);
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    });
}

function refreshAll() {
    refreshRoundStatus();
    refreshRoundTree();
    refreshRoundActivity();
}

function init() {
    setupChatPanel();
    refreshAll();
    // Pause polling while the tab is hidden; catch up immediately on return.
    setInterval(() => { if (!document.hidden) refreshRoundStatus(); }, ROUND_STATUS_POLL_MS);
    setInterval(() => { if (!document.hidden) refreshRoundTree(); }, ROUND_TREE_POLL_MS);
    setInterval(() => { if (!document.hidden) refreshRoundActivity(); }, ROUND_ACTIVITY_POLL_MS);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) refreshAll();
    });
}

document.addEventListener('DOMContentLoaded', function () {
    // Before the first exploratory round has ever started, the blocking
    // "Start your first round" overlay owns the screen and the workspace shell
    // is inert. Do not initialize (no polling of status/tree/activity, no chat)
    // — there is no round to monitor yet.
    if (window.__PRE_ROUND_OVERLAY__) return;
    init();
});
