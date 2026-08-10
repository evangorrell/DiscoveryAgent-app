// Unit tests for the pure Hypothesis Studio event-model helpers
// (da_selection_ui/static/hypothesis_events.js). Run with: node --test
//
// These verify the active-version semantics that back the "revisions are
// appended chronological events, never in-place mutation" behavior.
const test = require('node:test');
const assert = require('node:assert');
const HSE = require('../da_selection_ui/static/hypothesis_events.js');

function genSet(id, indices) {
    return { kind: 'hypotheses', id: id, hypotheses: indices.map((i) => ({ index: i, state_id: 's' + i + '-v1', hypothesis: 'H' + i + ' v1' })) };
}
function revision(id, index, ver) {
    return { kind: 'revision', id: id, index: index, source: 'revised', hypothesis: { index: index, state_id: 's' + index + '-' + ver, hypothesis: 'H' + index + ' ' + ver } };
}
function regeneration(id, index, ver) {
    return { kind: 'revision', id: id, index: index, source: 'regenerated', hypothesis: { index: index, state_id: 's' + index + '-' + ver, hypothesis: 'H' + index + ' ' + ver } };
}

test('generated set: every hypothesis is its own active version', () => {
    const events = [genSet('e1', [1, 2, 3])];
    const active = HSE.computeActiveVersions(events);
    assert.equal(active[1].eventId, 'e1');
    assert.equal(active[2].eventId, 'e1');
    assert.equal(active[3].eventId, 'e1');
});

test('a revision does not mutate the original set event, but becomes active', () => {
    const set = genSet('e1', [1, 2, 3]);
    const events = [set, { kind: 'message', role: 'user', content: 'focus hyp 2' }, revision('r1', 2, 'v2')];
    const active = HSE.computeActiveVersions(events);
    // Original set event is unchanged.
    assert.equal(set.hypotheses[1].hypothesis, 'H2 v1');
    // Hypothesis 2's active version is now the revision; 1 and 3 unchanged.
    assert.equal(active[2].eventId, 'r1');
    assert.equal(active[2].hypothesis.hypothesis, 'H2 v2');
    assert.equal(active[1].eventId, 'e1');
    assert.equal(active[3].eventId, 'e1');
    // The set's rows for 2 are no longer the active version.
    assert.equal(HSE.isActiveVersion(events, 'e1', 2), false);
    assert.equal(HSE.isActiveVersion(events, 'r1', 2), true);
});

test('repeated revisions: newest wins, earlier versions remain historical', () => {
    const events = [genSet('e1', [1, 2]), revision('r1', 2, 'v2'), revision('r2', 2, 'v3')];
    const active = HSE.computeActiveVersions(events);
    assert.equal(active[2].eventId, 'r2');
    assert.equal(active[2].hypothesis.hypothesis, 'H2 v3');
    // v1 (set) and v2 (r1) are no longer active.
    assert.equal(HSE.isActiveVersion(events, 'e1', 2), false);
    assert.equal(HSE.isActiveVersion(events, 'r1', 2), false);
    assert.equal(HSE.isActiveVersion(events, 'r2', 2), true);
});

test('selection resolves to the newest active version', () => {
    const events = [genSet('e1', [1, 2]), revision('r1', 2, 'v2'), revision('r2', 2, 'v3')];
    const sel = HSE.activeHypothesis(events, 2);
    assert.equal(sel.state_id, 's2-v3');
    assert.equal(sel.hypothesis, 'H2 v3');
});

test('full-set regeneration starts fresh lineages; prior revisions go historical', () => {
    const events = [genSet('e1', [1, 2]), revision('r1', 2, 'v2'), genSet('e2', [1, 2, 3])];
    const active = HSE.computeActiveVersions(events);
    // Active versions come from the LATEST set (e2), not the earlier revision.
    assert.equal(active[1].eventId, 'e2');
    assert.equal(active[2].eventId, 'e2');
    assert.equal(active[3].eventId, 'e2');
    assert.equal(HSE.isActiveVersion(events, 'r1', 2), false);
});

test('slot regeneration history starts with the generated candidate', () => {
    const history = HSE.regenerationHistory([genSet('set1', [1, 2])], 2);
    assert.equal(history.results.length, 1);
    assert.equal(history.results[0].hypothesis.hypothesis, 'H2 v1');
    assert.equal(history.activePosition, 0);
});

test('slot regeneration history retains repeated results in order', () => {
    const events = [genSet('set1', [1]), regeneration('regen1', 1, 'v2'), regeneration('regen2', 1, 'v3')];
    const history = HSE.regenerationHistory(events, 1);
    assert.deepEqual(history.results.map((r) => r.hypothesis.hypothesis), ['H1 v1', 'H1 v2', 'H1 v3']);
    assert.equal(history.activePosition, 2);
    assert.equal(HSE.computeActiveVersions(events)[1].hypothesis.hypothesis, 'H1 v3');
});

test('ordinary edits update one regeneration result without adding a counter entry', () => {
    const events = [
        genSet('set1', [1]),
        regeneration('regen1', 1, 'v2'),
        revision('edit1', 1, 'v2-edited'),
    ];
    const history = HSE.regenerationHistory(events, 1);
    assert.equal(history.results.length, 2);
    assert.equal(history.results[1].hypothesis.hypothesis, 'H1 v2-edited');
    assert.equal(history.activePosition, 1);
});

test('LLM edits append versions while later manual edits update that version in place', () => {
    const events = [
        genSet('set1', [1]),
        regeneration('llm-edit-1', 1, 'v2'),
        revision('manual-edit', 1, 'v2-manual'),
        regeneration('llm-edit-2', 1, 'v3'),
    ];
    const history = HSE.regenerationHistory(events, 1);
    assert.deepEqual(
        history.results.map((result) => result.hypothesis.hypothesis),
        ['H1 v1', 'H1 v2-manual', 'H1 v3'],
    );
    assert.deepEqual(history.results.map((result) => result.eventId), [
        'set1', 'llm-edit-1', 'llm-edit-2',
    ]);
    assert.equal(history.activePosition, 2);
    assert.equal(HSE.activeHypothesis(events, 1).state_id, 's1-v3');
});

test('editing an earlier result makes it active without deleting later results', () => {
    const events = [
        genSet('set1', [1]),
        regeneration('regen1', 1, 'v2'),
        regeneration('regen2', 1, 'v3'),
        {
            kind: 'revision', id: 'edit-old', index: 1, source: 'historical-edit', regenerationPosition: 0,
            hypothesis: { index: 1, state_id: 'fresh-current', hypothesis: 'H1 v1 edited' },
        },
    ];
    const history = HSE.regenerationHistory(events, 1);
    assert.equal(history.results.length, 3);
    assert.equal(history.activePosition, 0);
    assert.equal(history.results[0].hypothesis.state_id, 'fresh-current');
    assert.equal(history.results[2].hypothesis.hypothesis, 'H1 v3');
    assert.equal(HSE.activeHypothesis(events, 1).state_id, 'fresh-current');
});

test('historical activation resolves by durable result identity before position', () => {
    const events = [
        genSet('set1', [1]),
        regeneration('regen1', 1, 'v2'),
        regeneration('regen2', 1, 'v3'),
        {
            kind: 'revision', id: 'edit-old', index: 1, source: 'historical-edit',
            regenerationPosition: 2, // deliberately wrong; identity is authoritative
            sourceResultEventId: 'set1',
            hypothesis: { index: 1, state_id: 'fresh', hypothesis: 'H1 v1 edited', thread_recoverable: true },
        },
    ];
    const history = HSE.regenerationHistory(events, 1);
    assert.equal(history.activePosition, 0);
    assert.equal(history.results[0].eventId, 'set1');
    assert.equal(history.results[0].threadRecoverable, true);
});

test('result-specific discussion histories remain distinct across regenerations', () => {
    const events = [
        genSet('set1', [1]),
        { kind: 'message', role: 'user', content: 'Question A', hypothesisIndex: 1, resultEventId: 'set1' },
        { kind: 'message', role: 'assistant', content: 'Answer A', hypothesisIndex: 1, resultEventId: 'set1' },
        regeneration('regen1', 1, 'v2'),
        { kind: 'message', role: 'user', content: 'Question B', hypothesisIndex: 1, resultEventId: 'regen1' },
    ];
    assert.deepEqual(
        HSE.regenerationThread(events, 1, 'set1').map((m) => m.content),
        ['Question A', 'Answer A'],
    );
    assert.deepEqual(
        HSE.regenerationThread(events, 1, 'regen1').map((m) => m.content),
        ['Question B'],
    );
});

test('LLM edit acknowledgement remains on the source version thread', () => {
    const events = [
        genSet('set1', [1]),
        { kind: 'message', role: 'user', content: 'Make it narrower', hypothesisIndex: 1, resultEventId: 'set1' },
        regeneration('version2', 1, 'v2'),
        { kind: 'message', role: 'assistant', content: 'Created Version 2.', hypothesisIndex: 1, resultEventId: 'set1' },
    ];

    assert.deepEqual(HSE.regenerationThread(events, 1, 'set1'), [
        { role: 'user', content: 'Make it narrower' },
        { role: 'assistant', content: 'Created Version 2.' },
    ]);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'version2'), []);
});

test('LLM revision result renders only as the new version, never as a chat turn', () => {
    const revised = '**Title**: Narrow mechanism\n\n**Hypothesis**: H1 revised';
    const events = [
        genSet('set1', [1]),
        { kind: 'message', role: 'user', content: 'Make it narrower', hypothesisIndex: 1, resultEventId: 'set1' },
        {
            kind: 'message', role: 'assistant', content: revised,
            hypothesisIndex: 1, resultEventId: 'set1',
        },
        {
            kind: 'revision', id: 'version2', index: 1, source: 'regenerated',
            sourceResultEventId: 'set1', resultEventId: 'version2',
            hypothesis: { index: 1, state_id: 's1-v2', full_text: revised, hypothesis: 'H1 revised' },
        },
        {
            kind: 'message', role: 'assistant', content: 'Created Version 2.',
            hypothesisIndex: 1, resultEventId: 'set1',
        },
    ];

    assert.equal(HSE.regenerationHistory(events, 1).results[1].hypothesis.full_text, revised);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'set1'), [
        { role: 'user', content: 'Make it narrower' },
        { role: 'assistant', content: 'Created Version 2.' },
    ]);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'version2'), []);
});

test('a manual edit updates the card without adding a chat message', () => {
    const events = [
        genSet('set1', [1]),
        { kind: 'message', role: 'user', content: 'Question A', hypothesisIndex: 1, resultEventId: 'set1' },
        { kind: 'message', role: 'assistant', content: 'Answer A', hypothesisIndex: 1, resultEventId: 'set1' },
        {
            kind: 'revision', id: 'edit1', index: 1, source: 'revised', resultEventId: 'set1',
            hypothesis: { index: 1, state_id: 'edited-a', hypothesis: 'H1 v1 edited' },
        },
        regeneration('regen1', 1, 'v2'),
        { kind: 'message', role: 'user', content: 'Question B', hypothesisIndex: 1, resultEventId: 'regen1' },
    ];
    assert.deepEqual(
        HSE.regenerationThread(events, 1, 'set1').map((m) => m.content),
        ['Question A', 'Answer A'],
    );
    assert.deepEqual(
        HSE.regenerationThread(events, 1, 'regen1').map((m) => m.content),
        ['Question B'],
    );
});

test('every slot and regeneration version has an independent transcript', () => {
    const events = [
        genSet('set1', [1, 2, 3]),
        { kind: 'message', role: 'user', content: 'A version 1', hypothesisIndex: 1, resultEventId: 'set1' },
        { kind: 'message', role: 'user', content: 'B version 1', hypothesisIndex: 2, resultEventId: 'set1' },
        { kind: 'message', role: 'user', content: 'C version 1', hypothesisIndex: 3, resultEventId: 'set1' },
        regeneration('a2', 1, 'v2'),
        { kind: 'message', role: 'user', content: 'A version 2', hypothesisIndex: 1, resultEventId: 'a2' },
        regeneration('b2', 2, 'v2'),
        { kind: 'message', role: 'user', content: 'B version 2', hypothesisIndex: 2, resultEventId: 'b2' },
        regeneration('a3', 1, 'v3'),
        { kind: 'message', role: 'user', content: 'A version 3', hypothesisIndex: 1, resultEventId: 'a3' },
        regeneration('b3', 2, 'v3'),
        { kind: 'message', role: 'user', content: 'B version 3', hypothesisIndex: 2, resultEventId: 'b3' },
    ];

    assert.deepEqual(HSE.regenerationHistory(events, 1).results.map((r) => r.eventId), ['set1', 'a2', 'a3']);
    assert.deepEqual(HSE.regenerationHistory(events, 2).results.map((r) => r.eventId), ['set1', 'b2', 'b3']);
    assert.deepEqual(HSE.regenerationHistory(events, 3).results.map((r) => r.eventId), ['set1']);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'set1').map((m) => m.content), ['A version 1']);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'a2').map((m) => m.content), ['A version 2']);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'a3').map((m) => m.content), ['A version 3']);
    assert.deepEqual(HSE.regenerationThread(events, 2, 'set1').map((m) => m.content), ['B version 1']);
    assert.deepEqual(HSE.regenerationThread(events, 2, 'b2').map((m) => m.content), ['B version 2']);
    assert.deepEqual(HSE.regenerationThread(events, 2, 'b3').map((m) => m.content), ['B version 3']);
    assert.deepEqual(HSE.regenerationThread(events, 3, 'set1').map((m) => m.content), ['C version 1']);
});

test('history normalization preserves safe result identities and thread availability', () => {
    let n = 0;
    const events = HSE.normalizeHistoryEvents({ transcript: [
        {
            kind: 'hypothesis_set_generated', result_event_id: 'set-durable',
            hypotheses: [{ index: 1, hypothesis: 'A', thread_recoverable: true }],
        },
        {
            kind: 'user_message', hypothesis_index: 1, result_event_id: 'set-durable',
            content: 'Discuss A',
        },
        {
            kind: 'hypothesis_regenerated', hypothesis_index: 1,
            result_event_id: 'regen-durable',
            after: { index: 1, hypothesis: 'B', thread_recoverable: false },
        },
    ] }, () => 'local-' + (++n));
    assert.equal(events[0].id, 'set-durable');
    assert.equal(events[1].resultEventId, 'set-durable');
    assert.equal(events[2].id, 'regen-durable');
    const history = HSE.regenerationHistory(events, 1);
    assert.equal(history.results[0].threadRecoverable, true);
    assert.equal(history.results[1].threadRecoverable, false);
});

test('regeneration before-snapshot restores legacy prior-result thread availability', () => {
    const events = HSE.normalizeHistoryEvents({ transcript: [
        {
            kind: 'hypothesis_set_generated', result_event_id: 'set-legacy',
            hypotheses: [{ index: 1, hypothesis: 'A', thread_recoverable: false }],
        },
        {
            kind: 'hypothesis_revised', hypothesis_index: 1, result_event_id: 'set-legacy',
            before: { index: 1, hypothesis: 'A', thread_recoverable: true },
            after: { index: 1, hypothesis: 'A edited', thread_recoverable: true },
        },
        {
            kind: 'hypothesis_regenerated', hypothesis_index: 1, result_event_id: 'regen-new',
            before: { index: 1, hypothesis: 'A edited', thread_recoverable: true },
            after: { index: 1, hypothesis: 'B', thread_recoverable: true },
        },
    ] });
    const history = HSE.regenerationHistory(events, 1);
    assert.equal(history.results[0].threadRecoverable, true);
    assert.equal(history.results[1].threadRecoverable, true);
    assert.equal(events[1].resultEventId, 'set-legacy');
    assert.equal(events[2].before.thread_recoverable, true);
});

test('reading an earlier slot result does not alter active-version projection', () => {
    const events = [genSet('set1', [1]), regeneration('regen1', 1, 'v2')];
    const history = HSE.regenerationHistory(events, 1);
    const preview = history.results[0].hypothesis;
    assert.equal(preview.hypothesis, 'H1 v1');
    assert.equal(HSE.activeHypothesis(events, 1).hypothesis, 'H1 v2');
});

test('full generation resets history while removal preserves and reindexes surviving histories', () => {
    const events = [
        genSet('set1', [1, 2]), regeneration('old-regen', 2, 'v2'),
        genSet('set2', [1, 2]), regeneration('new-regen', 2, 'v3'),
    ];
    assert.deepEqual(
        HSE.regenerationHistory(events, 2).results.map((r) => r.eventId),
        ['set2', 'new-regen'],
    );

    const removed = events.concat({
        kind: 'removal', id: 'remove1', index: 1,
        hypotheses: [{ index: 1, state_id: 'remaining', hypothesis: 'Former H2' }],
    });
    const history = HSE.regenerationHistory(removed, 1);
    assert.deepEqual(history.results.map((result) => result.eventId), ['set2', 'new-regen']);
    assert.equal(history.results[0].hypothesis.hypothesis, 'H2 v1');
    assert.equal(history.results[1].hypothesis.hypothesis, 'Former H2');
});

test('removing one candidate preserves every version transcript of the survivor', () => {
    const events = [
        genSet('set1', [1, 2]),
        { kind: 'message', role: 'user', content: 'A1 discussion', hypothesisIndex: 1, resultEventId: 'set1' },
        { kind: 'message', role: 'user', content: 'B1 discussion', hypothesisIndex: 2, resultEventId: 'set1' },
        regeneration('b2', 2, 'v2'),
        { kind: 'message', role: 'user', content: 'B2 discussion', hypothesisIndex: 2, resultEventId: 'b2' },
        regeneration('b3', 2, 'v3'),
        { kind: 'message', role: 'user', content: 'B3 discussion', hypothesisIndex: 2, resultEventId: 'b3' },
        {
            kind: 'removal', id: 'remove-a', index: 1,
            hypotheses: [{ index: 1, state_id: 'b3-current', hypothesis: 'H2 v3' }],
        },
    ];

    const history = HSE.regenerationHistory(events, 1);
    assert.deepEqual(history.results.map((result) => result.eventId), ['set1', 'b2', 'b3']);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'set1').map((m) => m.content), ['B1 discussion']);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'b2').map((m) => m.content), ['B2 discussion']);
    assert.deepEqual(HSE.regenerationThread(events, 1, 'b3').map((m) => m.content), ['B3 discussion']);
});

test('history normalization preserves durable chronological event order', () => {
    let n = 0;
    const events = HSE.normalizeHistoryEvents({
        transcript: [
            { kind: 'user_message', content: 'Focus on axon growth.' },
            { kind: 'assistant_message', content: 'I will prioritize axon growth.' },
            { kind: 'hypothesis_set_generated', hypotheses: [{ index: 1, hypothesis: 'H1' }], generation_id: 7 },
            { kind: 'user_message', content: 'Make H1 narrower.' },
            {
                kind: 'hypothesis_revised',
                hypothesis_index: 1,
                before: { index: 1, hypothesis: 'H1' },
                after: { index: 1, hypothesis: 'H1 revised' },
            },
            {
                kind: 'hypothesis_revised',
                hypothesis_index: 1,
                action: 'edit_regeneration_version',
                regeneration_position: 0,
                after: { index: 1, hypothesis: 'H1' },
            },
            {
                kind: 'hypothesis_regenerated',
                hypothesis_index: 1,
                after: { index: 1, hypothesis: 'H1 regenerated' },
            },
            { kind: 'hypothesis_selected', hypothesis: { index: 1, hypothesis: 'H1 regenerated' } },
        ],
    }, () => 'e' + (++n));

    assert.deepEqual(events.map((e) => e.kind), [
        'message', 'message', 'hypotheses', 'message', 'revision', 'revision', 'revision', 'selection',
    ]);
    assert.equal(events[0].role, 'user');
    assert.equal(events[1].role, 'assistant');
    assert.equal(events[2].generationId, 7);
    assert.equal(events[4].source, 'revised');
    assert.equal(events[4].hypothesis.hypothesis, 'H1 revised');
    assert.equal(events[5].source, 'historical-edit');
    assert.equal(events[5].regenerationPosition, 0);
    assert.equal(events[6].source, 'regenerated');
    assert.equal(events[7].hypothesis.hypothesis, 'H1 regenerated');
});

test('history normalization supports current transcript aliases and flat fallback', () => {
    let n = 0;
    const transcriptEvents = HSE.normalizeHistoryEvents({
        transcript: [
            { kind: 'message', role: 'assistant', content: 'Ready.' },
            { kind: 'hypotheses', hypotheses: [{ index: 1, hypothesis: 'H1' }] },
            { kind: 'selection', hypothesis: { index: 1, hypothesis: 'H1' } },
        ],
    }, () => 'e' + (++n));

    assert.deepEqual(transcriptEvents.map((e) => e.kind), ['message', 'hypotheses', 'selection']);
    assert.equal(transcriptEvents[0].role, 'assistant');

    const flatEvents = HSE.normalizeHistoryEvents({
        generated_hypotheses: [{ index: 1, hypothesis: 'Old H1' }],
        selected_hypothesis: { hypothesis: 'Old H1' },
    }, () => 'f' + (++n));

    assert.deepEqual(flatEvents.map((e) => e.kind), ['hypotheses', 'selection']);
    assert.equal(flatEvents[0].hypotheses[0].hypothesis, 'Old H1');
});

test('generation command parsing only catches explicit structured-generation requests', () => {
    assert.deepEqual(HSE.parseGenerationRequest('Generate two more hypotheses'), { count: 2, guidance: null });
    assert.deepEqual(HSE.parseGenerationRequest('Generate 2 more hypotheses'), { count: 2, guidance: null });
    assert.deepEqual(HSE.parseGenerationRequest('Generate two new hypotheses'), { count: 2, guidance: null });
    assert.deepEqual(HSE.parseGenerationRequest('Generate 5 hypotheses'), { count: 5, guidance: null });
    assert.deepEqual(HSE.parseGenerationRequest('Generate three research directions'), { count: 3, guidance: null });
    assert.deepEqual(HSE.parseGenerationRequest('Create two new hypotheses'), { count: 2, guidance: null });
    assert.deepEqual(HSE.parseGenerationRequest('Propose four new hypotheses'), { count: 4, guidance: null });
    assert.deepEqual(HSE.parseGenerationRequest('Generate five hypotheses focused on metabolism'), {
        count: 5,
        guidance: 'focused on metabolism',
    });
    assert.deepEqual(HSE.parseGenerationRequest('generate hypotheses'), { count: null, guidance: null });
    assert.equal(HSE.parseGenerationRequest('How would you generate two new hypotheses?'), null);
    assert.equal(HSE.parseGenerationRequest('Do you think we should generate two more hypotheses?'), null);
    assert.equal(HSE.parseGenerationRequest('Should we generate again?'), null);
    assert.equal(HSE.parseGenerationRequest('Compare these and tell me whether another set would help.'), null);
    assert.equal(HSE.parseGenerationRequest("I don't like those hypotheses, generate again"), null);
    assert.equal(HSE.parseGenerationRequest("I don't like these hypotheses."), null);
    assert.equal(HSE.parseGenerationRequest('I do not want to generate hypotheses'), null);
});

test('history normalization preserves generated sets as hypothesis events after chat turns', () => {
    let n = 0;
    const events = HSE.normalizeHistoryEvents({
        transcript: [
            { kind: 'message', role: 'user', content: 'Generate two more hypotheses' },
            { kind: 'hypotheses', hypotheses: [{ index: 2, title: 'T2', hypothesis: 'H2' }] },
            { kind: 'message', role: 'assistant', content: 'Follow-up answer.' },
        ],
    }, () => 'e' + (++n));

    assert.deepEqual(events.map((e) => e.kind), ['message', 'hypotheses', 'message']);
    assert.equal(events[1].hypotheses[0].title, 'T2');
});


test('removing the last hypothesis leaves no active versions', () => {
    const events = [
        { kind: 'hypotheses', id: 'set1', hypotheses: [{ index: 1, state_id: 's1', hypothesis: 'H1' }] },
        { kind: 'removal', id: 'rm1', index: 1, hypotheses: [] },
    ];

    const active = HSE.computeActiveVersions(events);
    assert.deepEqual(active, {});
    assert.equal(HSE.latestSetDefiningPos(events), 1);
});

test('removing a hypothesis preserves and reindexes surviving chat threads', () => {
    const events = [
        { kind: 'message', role: 'user', content: 'Chat one', hypothesisIndex: 1 },
        { kind: 'message', role: 'assistant', content: 'Reply one', hypothesisIndex: 1 },
        { kind: 'message', role: 'user', content: 'Chat two', hypothesisIndex: 2 },
        { kind: 'message', role: 'assistant', content: 'Reply two', hypothesisIndex: 2 },
        { kind: 'message', role: 'user', content: 'Chat three', hypothesisIndex: 3 },
        { kind: 'message', role: 'assistant', content: 'Reply three', hypothesisIndex: 3 },
        { kind: 'removal', id: 'rm2', index: 2, hypotheses: [
            { index: 1, hypothesis: 'H1' },
            { index: 2, hypothesis: 'H3' },
        ] },
    ];

    assert.deepEqual(HSE.computeThreads(events), {
        1: [
            { role: 'user', content: 'Chat one' },
            { role: 'assistant', content: 'Reply one' },
        ],
        2: [
            { role: 'user', content: 'Chat three' },
            { role: 'assistant', content: 'Reply three' },
        ],
    });
});

test('selection source resolves to generated candidate in original position', () => {
    const events = [
        { kind: 'hypotheses', id: 'set1', hypotheses: [
            { index: 1, state_id: 's1', full_text: 'A' },
            { index: 2, state_id: 's2', full_text: 'B' },
        ] },
        { kind: 'selection', id: 'sel', hypothesis: { index: 2, state_id: 's2', full_text: 'B' } },
    ];

    const source = HSE.findSelectionSource(events, HSE.latestSelection(events));
    assert.deepEqual(source, { eventId: 'set1', index: 2, key: 'set1:2' });
});

test('selection source prefers revised or regenerated version over original', () => {
    const events = [
        genSet('set1', [1, 2]),
        revision('rev1', 2, 'v2'),
        { kind: 'revision', id: 'regen1', index: 2, source: 'regenerated', hypothesis: { index: 2, state_id: 's2-v3', hypothesis: 'H2 regenerated' } },
        { kind: 'selection', id: 'sel', hypothesis: { index: 2, state_id: 's2-v3', hypothesis: 'H2 regenerated' } },
    ];

    const source = HSE.findSelectionSource(events, HSE.latestSelection(events));
    assert.deepEqual(source, { eventId: 'regen1', index: 2, key: 'regen1:2' });
});

test('selection from later generated set highlights later set candidate', () => {
    const events = [
        { kind: 'hypotheses', id: 'set1', hypotheses: [{ index: 1, state_id: 'old', full_text: 'Old' }] },
        { kind: 'hypotheses', id: 'set2', hypotheses: [{ index: 1, state_id: 'new', full_text: 'New' }] },
        { kind: 'selection', id: 'sel', hypothesis: { index: 1, state_id: 'new', full_text: 'New' } },
    ];

    const source = HSE.findSelectionSource(events, HSE.latestSelection(events));
    assert.deepEqual(source, { eventId: 'set2', index: 1, key: 'set2:1' });
});

test('selection resolves against the surviving set after a hypothesis removal', () => {
    const original = [
        { index: 1, state_id: 'a', hypothesis: 'Alpha' },
        { index: 2, state_id: 'b', hypothesis: 'Beta' },
        { index: 3, state_id: 'c', hypothesis: 'Gamma' },
    ];
    const surviving = [
        { index: 1, state_id: 'a', hypothesis: 'Alpha' },
        { index: 2, state_id: 'b', hypothesis: 'Beta' },
    ];
    const events = [
        { kind: 'hypotheses', id: 'set1', hypotheses: original },
        { kind: 'removal', id: 'remove3', index: 3, hypotheses: surviving },
        { kind: 'selection', id: 'selected', hypothesis: { hypothesis: 'Beta' } },
    ];

    const source = HSE.findSelectionSource(events, HSE.latestSelection(events));

    assert.deepEqual(source, { eventId: 'remove3', index: 2, key: 'remove3:2' });
});

test('pending selection follows its card when a different card is later removed', () => {
    const events = [
        { kind: 'hypotheses', id: 'set1', hypotheses: [
            { index: 1, state_id: 'a', hypothesis: 'Alpha' },
            { index: 2, state_id: 'b', hypothesis: 'Beta' },
            { index: 3, state_id: 'c', hypothesis: 'Gamma' },
        ] },
        { kind: 'selection', id: 'selected', hypothesis: {
            index: 2, state_id: 'b', hypothesis: 'Beta',
        } },
        { kind: 'removal', id: 'remove-a', index: 1, hypotheses: [
            { index: 1, state_id: 'b', hypothesis: 'Beta' },
            { index: 2, state_id: 'c', hypothesis: 'Gamma' },
        ] },
    ];

    const source = HSE.findSelectionSource(events, HSE.latestSelection(events));

    assert.deepEqual(source, { eventId: 'remove-a', index: 1, key: 'remove-a:1' });
});

test('pending selection remains on its lineage after a late version and repeated removals', () => {
    const events = [
        { kind: 'hypotheses', id: 'set1', hypotheses: [
            { index: 1, state_id: 'a', hypothesis: 'Alpha' },
            { index: 2, state_id: 'b-v1', hypothesis: 'Beta original' },
            { index: 3, state_id: 'c', hypothesis: 'Gamma' },
        ] },
        { kind: 'selection', id: 'selected', hypothesis: {
            index: 2, state_id: 'b-v1', hypothesis: 'Beta original',
        } },
        // This edit began before selection and completes afterward. The pending
        // selection still belongs to this card lineage, but to its original
        // version rather than the newly active version.
        { kind: 'revision', id: 'late-edit', index: 2, source: 'regenerated', hypothesis: {
            index: 2, state_id: 'b-v2', hypothesis: 'Beta revised',
        } },
        { kind: 'removal', id: 'remove-c', index: 3, hypotheses: [
            { index: 1, state_id: 'a', hypothesis: 'Alpha' },
            { index: 2, state_id: 'b-v2', hypothesis: 'Beta revised' },
        ] },
        { kind: 'removal', id: 'remove-a', index: 1, hypotheses: [
            { index: 1, state_id: 'b-v2', hypothesis: 'Beta revised' },
        ] },
    ];

    const source = HSE.findSelectionSource(events, HSE.latestSelection(events));

    assert.deepEqual(source, { eventId: 'remove-a', index: 1, key: 'remove-a:1' });
});

test('selection is no longer pending after its card is removed', () => {
    const events = [
        { kind: 'hypotheses', id: 'set1', hypotheses: [
            { index: 1, state_id: 'a', hypothesis: 'Alpha' },
            { index: 2, state_id: 'b', hypothesis: 'Beta' },
        ] },
        { kind: 'selection', id: 'selected', hypothesis: {
            index: 2, state_id: 'b', hypothesis: 'Beta',
        } },
        { kind: 'removal', id: 'remove-b', index: 2, hypotheses: [
            { index: 1, state_id: 'a', hypothesis: 'Alpha' },
        ] },
    ];

    assert.equal(HSE.findSelectionSource(events, HSE.latestSelection(events)), null);
});

test('unmatched selection has no source so UI can render minimal fallback', () => {
    const events = [
        genSet('set1', [1]),
        { kind: 'selection', id: 'sel', hypothesis: { index: 1, full_text: 'Something else' } },
    ];
    assert.equal(HSE.findSelectionSource(events, HSE.latestSelection(events)), null);
});
