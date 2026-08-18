const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/project_home.js'), 'utf8');
const sharedCss = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/style.css'), 'utf8');
const workspaceCss = fs.readFileSync(path.join(ROOT, 'da_selection_ui/static/round_workspace.css'), 'utf8');

test('Project Home renders only the canonical Research Status payload', () => {
  assert.match(client, /buildResearchStatus\(state\.research_status \|\| \{\}\)/);
  assert.doesNotMatch(client, /buildAttention\(/);
  assert.doesNotMatch(client, /Needs Your Attention/i);
  assert.match(client, /label\('Research Status'\)/);
});

test('Research Status uses one exact action for start, resume, or resubmit', () => {
  assert.match(client, /data-start-round-number/);
  assert.match(client, /cta_action === 'resume_round'/);
  assert.match(client, /cta_action === 'resubmit_round'/);
  assert.match(client, /'\/api\/rounds\/resume'/);
  assert.match(client, /'\/api\/rounds\/resubmit'/);
  assert.match(client, /Resubmit this round\?/);
  assert.match(client, /DiscoveryAgent will resubmit this round and run it again from the beginning\./);
});

test('running status and Round Workspace share the reduced-motion activity pulse', () => {
  assert.match(client, /v2-pill-active/);
  assert.match(sharedCss, /\.v2-pill-active \.v2-pill-dot,[\s\S]*\.rw-activity-state-icon\.active/);
  assert.match(sharedCss, /@keyframes v2-round-activity-pulse/);
  assert.match(sharedCss, /@media \(prefers-reduced-motion: reduce\)/);
  assert.doesNotMatch(workspaceCss, /@keyframes rw-activity-pulse/);
});
