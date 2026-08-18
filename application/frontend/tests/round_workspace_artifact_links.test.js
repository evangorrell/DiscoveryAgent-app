// Unit tests for exact-round trusted artifact references in Round Chat.
// Run with: node --test tests/round_workspace_artifact_links.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const ArtifactLinks = require('../da_selection_ui/static/round_workspace_artifact_links.js');

function file(path, overrides = {}) {
  return {
    round: 4,
    source: 'round-4-checkpoint',
    sourceKind: 'checkpoint',
    path,
    artifactPath: path,
    name: path.split('/').pop(),
    ...overrides,
  };
}

test('unique trusted basename and relative path resolve to the exact artifact', () => {
  const artifact = file('query_001/outputs/resilience_abundance_heatmap.png');
  const index = ArtifactLinks.buildIndex([artifact]);
  assert.equal(ArtifactLinks.resolveReference('resilience_abundance_heatmap.png', index).path, artifact.path);
  assert.equal(ArtifactLinks.resolveReference(artifact.path, index).path, artifact.path);
  assert.deepEqual(
    ArtifactLinks.findReferences('| `resilience_abundance_heatmap.png` | result |', index).map(match => match.text),
    ['resilience_abundance_heatmap.png'],
  );
});

test('activating a trusted reference prevents navigation and opens the existing artifact identity', () => {
  const artifact = file('query_001/outputs/resilience_abundance_heatmap.png');
  const events = [];
  const opened = [];
  ArtifactLinks.activateArtifact(
    { preventDefault: () => events.push('prevented'), stopPropagation: () => events.push('stopped') },
    artifact,
    selected => opened.push(selected),
  );
  assert.deepEqual(events, ['prevented', 'stopped']);
  assert.deepEqual(opened, [artifact]);
});

test('ambiguous basename stays plain while a full relative path disambiguates', () => {
  const first = file('query_001/outputs/shared.png');
  const second = file('query_002/outputs/shared.png', { source: 'round-4-other' });
  const index = ArtifactLinks.buildIndex([first, second]);
  assert.equal(ArtifactLinks.resolveReference('shared.png', index), null);
  assert.equal(ArtifactLinks.findReferences('shared.png', index).length, 0);
  assert.equal(ArtifactLinks.resolveReference(first.path, index).path, first.path);
  assert.equal(ArtifactLinks.resolveReference(second.path, index).path, second.path);
});

test('nonexistent, absolute, traversal, and Windows paths never resolve', () => {
  const artifact = file('query_001/outputs/figure.png');
  const index = ArtifactLinks.buildIndex([artifact]);
  assert.equal(ArtifactLinks.resolveReference('invented.png', index), null);
  assert.equal(ArtifactLinks.resolveReference('/lustre/run/query_001/outputs/figure.png', index), null);
  assert.equal(ArtifactLinks.resolveReference('../query_001/outputs/figure.png', index), null);
  assert.equal(ArtifactLinks.resolveReference('C:\\project\\figure.png', index), null);
  assert.equal(ArtifactLinks.findReferences('/lustre/run/query_001/outputs/figure.png', index).length, 0);
});

test('an index contains only the exact round and project inventory supplied to it', () => {
  const roundFour = file('round-four.png');
  const roundThree = file('round-three.png', { round: 3, source: 'round-3-checkpoint' });
  const otherProject = file('other-project.png', { source: 'other-project-round-4' });
  const currentIndex = ArtifactLinks.buildIndex([roundFour]);
  assert.ok(ArtifactLinks.resolveReference('round-four.png', currentIndex));
  assert.equal(ArtifactLinks.resolveReference('round-three.png', currentIndex), null);
  assert.equal(ArtifactLinks.resolveReference('other-project.png', currentIndex), null);
  assert.ok(ArtifactLinks.resolveReference('round-three.png', ArtifactLinks.buildIndex([roundThree])));
  assert.ok(ArtifactLinks.resolveReference('other-project.png', ArtifactLinks.buildIndex([otherProject])));
});

test('persisted text deterministically regains links when its trusted index is rebuilt', () => {
  const transcript = 'Open `query_001/outputs/resilience_abundance_heatmap.png`.';
  const inventory = [file('query_001/outputs/resilience_abundance_heatmap.png')];
  const beforeRefresh = ArtifactLinks.findReferences(transcript, ArtifactLinks.buildIndex(inventory));
  const afterRefresh = ArtifactLinks.findReferences(transcript, ArtifactLinks.buildIndex(JSON.parse(JSON.stringify(inventory))));
  assert.deepEqual(afterRefresh.map(match => match.text), beforeRefresh.map(match => match.text));
  assert.equal(afterRefresh[0].file.path, inventory[0].path);
  assert.equal(transcript.includes('http'), false);
});

test('ordinary URLs and Markdown-link destinations are not artifact candidates', () => {
  const index = ArtifactLinks.buildIndex([file('figure.png')]);
  assert.equal(ArtifactLinks.findReferences('https://example.test/figure.png', index).length, 0);
  assert.equal(ArtifactLinks.findReferences('/files/figure.png', index).length, 0);
});
