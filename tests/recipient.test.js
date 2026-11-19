const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/relay/web/recipient.js'), 'utf8');
const RecipientSelection = vm.runInNewContext(source + '\nRecipientSelection;');
const agents = [{ id: 'codex', name: 'Codex' }, { id: 'claude', name: 'Claude' }];
const translate = (key, values) => key === 'all' ? 'Everyone' : `${values.name} (unavailable)`;

test('each channel keeps its own explicit recipient and starts with Everyone', () => {
  const values = new Map([['relay-recipient', JSON.stringify({ id: 'claude', name: 'Claude' })]]);
  const saved = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
  assert.equal(new RecipientSelection(saved, 'general').id, 'claude');
  const other = new RecipientSelection(saved, 'ch-other');
  assert.equal(other.id, 'all');
  other.choose('codex-2', [{ id: 'codex-2', name: 'Other Codex' }]);
  assert.equal(new RecipientSelection(saved, 'ch-other').id, 'codex-2');
  assert.equal(new RecipientSelection(saved, 'general').id, 'claude');
  assert.equal(new RecipientSelection(saved, 'ch-new').id, 'all');
});

function storage(initial = null) {
  let value = initial;
  return { getItem: () => value, setItem: (_, next) => { value = next; } };
}

test('Everyone stays selected through initial, empty, and reordered participant lists', () => {
  const state = new RecipientSelection(storage());
  for (const participants of [[], agents, [...agents].reverse(), [], agents.slice(1)]) {
    const options = state.options(participants, translate);
    assert.equal(state.id, 'all');
    assert.equal(options[0].id, 'all');
    assert.equal(state.available(participants), participants.length > 0);
  }
});

test('an explicit choice survives roster changes, translated labels, and browser reload', () => {
  const saved = storage();
  const state = new RecipientSelection(saved);
  assert.equal(state.choose('claude', agents), true);
  state.options([...agents].reverse(), translate);
  state.options([{ id: 'claude', name: 'Renamed Claude' }, agents[0]], () => '모두');
  assert.equal(state.id, 'claude');
  assert.equal(new RecipientSelection(saved).id, 'claude');
});

test('removing the selected agent keeps its ID and blocks sending instead of choosing another target', () => {
  const saved = storage();
  const state = new RecipientSelection(saved);
  state.choose('claude', agents);
  for (const participants of [agents.slice(0, 1), []]) {
    const missing = state.options(participants, translate).find((entry) => entry.id === 'claude');
    assert.equal(missing.disabled, true);
    assert.match(missing.name, /Claude/);
    assert.equal(state.id, 'claude');
    assert.equal(state.available(participants), false);
  }
  assert.equal(new RecipientSelection(saved).id, 'claude');
  assert.equal(state.available(agents), true);
  assert.equal(state.choose('all', agents.slice(0, 1)), true);
  assert.equal(state.id, 'all');
});

test('invalid choices never replace the last explicit selection', () => {
  const state = new RecipientSelection(storage());
  state.choose('codex', agents);
  for (const id of ['', 'missing', null, undefined]) {
    assert.equal(state.choose(id, agents), false);
    assert.equal(state.id, 'codex');
  }
});

test('missing, malformed, or blocked storage safely defaults to Everyone', () => {
  for (const value of [null, 'broken', '{}', '{"id":5}', '{"id":""}']) {
    assert.equal(new RecipientSelection(storage(value)).id, 'all');
  }
  const blocked = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  const state = new RecipientSelection(blocked);
  assert.equal(state.id, 'all');
  assert.equal(state.choose('claude', agents), true);
  assert.equal(state.id, 'claude');
});
