const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const app = fs.readFileSync(path.join(__dirname, '../src/relay/web/app.js'), 'utf8');
const loadRoom = app.slice(app.indexOf('async function loadRoom()'), app.indexOf('\nfunction switchChannel('));

function fixture(channel = 'general') {
  const requests = [], selections = [], snapshots = [], errors = [];
  const state = {
    activeChannel: channel, roomRequest: 0, drafts: new Map([[channel, { body: 'Draft' }]]),
    request(url) { return new Promise((resolve, reject) => requests.push({ url, resolve, reject })); },
    switchChannel(id) { selections.push(id); state.activeChannel = id; },
    applyRoom(snapshot) { snapshots.push(snapshot); },
    showMessage(id, message) { errors.push(message); },
  };
  vm.createContext(state);
  vm.runInContext(loadRoom, state);
  return { state, requests, selections, snapshots, errors };
}

test('a deleted channel clears the selection and its draft, including General', async () => {
  for (const channel of ['general', 'ch-test']) {
    const { state, requests, selections, errors } = fixture(channel);
    const pending = state.loadRoom();
    assert.equal(requests[0].url, '/api/room?channel=' + channel);
    requests[0].reject({ status: 404, message: 'Deleted' });
    await pending;
    assert.deepEqual(selections, ['']);
    assert.equal(state.drafts.has(channel), false);
    assert.deepEqual(errors, []);
  }
});

test('an explicit empty selection loads channel navigation without selecting a channel', async () => {
  const { state, requests, selections, snapshots } = fixture('');
  const pending = state.loadRoom();
  assert.equal(requests[0].url, '/api/room?channel=');
  const snapshot = { channel: null, channels: [{ id: 'ch-other' }], participants: [], messages: [] };
  requests[0].resolve(snapshot);
  await pending;
  assert.deepEqual(snapshots, [snapshot]);
  assert.deepEqual(selections, []);
});

test('a delayed missing-channel response cannot clear a newer selection', async () => {
  const { state, requests, selections, snapshots } = fixture();
  const old = state.loadRoom();
  state.activeChannel = 'ch-new';
  const newer = state.loadRoom();
  const snapshot = { channel: { id: 'ch-new' } };
  requests[1].resolve(snapshot);
  await newer;
  requests[0].reject({ status: 404, message: 'Deleted' });
  await old;
  assert.deepEqual(selections, []);
  assert.deepEqual(snapshots, [snapshot]);
  assert.equal(state.activeChannel, 'ch-new');
});

test('saved empty selections survive reload; only a fresh browser selects General', () => {
  const initializer = app.match(/let activeChannel = ([^;]+);/)[1];
  for (const [saved, expected] of [[null, 'general'], ['', ''], ['ch-existing', 'ch-existing']]) {
    assert.equal(vm.runInNewContext(initializer, {
      defaultChannel: 'general', localStorage: { getItem: () => saved },
    }), expected);
  }
});
