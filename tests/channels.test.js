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

test('channel activity appears beside busy channel names and clears when replies finish', () => {
  function element(tag, className = '', textContent = '') {
    return { tag, className, textContent, children: [], attributes: {}, dataset: {}, classList: { toggle() {} },
      append(...items) { this.children.push(...items); }, replaceChildren(...items) { this.children = items; },
      setAttribute(name, value) { this.attributes[name] = value; } };
  }
  const list = element('nav');
  const state = { current: { channels: [{ id: 'A', name: 'Working', pending: 2 }, { id: 'B', name: 'Idle', pending: 0 }] },
    activeChannel: 'B', closeChannelMenu() {}, element, icon: name => element('svg', name),
    channelName: channel => channel.name, el: () => list, t: (key, values) => key + ':' + (values.count ?? values.name) };
  vm.createContext(state);
  vm.runInContext(app.slice(app.indexOf('function showChannels()'), app.indexOf('\nfunction channelName(')), state);
  state.showChannels();
  const [busy, idle] = list.children.map(row => row.children[0]);
  assert.equal(busy.children[2].className, 'channel-activity');
  assert.equal(busy.children[2].title, 'channelWorking:2');
  assert.equal(busy.attributes['aria-label'], 'Working · channelWorking:2');
  assert.equal(idle.children.length, 2);
  state.current.channels[0].pending = 0;
  state.showChannels();
  assert.equal(list.children[0].children[0].children.length, 2);
});

test('switching immediately clears the previous roster and recipient while the new channel is loading', () => {
  const nodes = new Map();
  const el = id => {
    if (!nodes.has(id)) nodes.set(id, { textContent: 'Old channel', hidden: false, children: ['Old agent'],
      querySelector: () => null, replaceChildren(...items) { this.children = items; } });
    return nodes.get(id);
  };
  const dialog = () => ({ dataset: {}, close() {} });
  const state = {
    activeChannel: 'A', current: { channel: { id: 'A' } }, drafts: new Map(), input: { value: 'A draft' },
    chatFiles: [], chatDraftId: null, setupRequest: 0, el, recipient: el('recipient'),
    messages: el('messages'), scrollRegion: {}, CSS: { escape: value => value },
    localStorage: { setItem() {} }, t: key => key,
    RecipientSelection: class { constructor() { this.id = 'all'; this.name = ''; } },
    Option: class { constructor(text, value) { this.text = text; this.value = value; } },
    clearChatDialog: dialog(), foldersDialog: dialog(), setupDialog: dialog(), joinDialog: dialog(),
    agentDeleteDialog: dialog(), channelDeleteDialog: dialog(), channelDialog: dialog(),
    closeChannelMenu() {}, resetBoardChannel() {}, showChatFiles() {}, showMessage() {},
    showSidePanel() {}, showChannelView() {}, refreshControls() {}, connectRoomStream() {}, loadRoom() {},
  };
  vm.createContext(state);
  vm.runInContext(app.slice(app.indexOf('function switchChannel('), app.indexOf('\nfunction showChatFiles(')), state);
  state.switchChannel('B');
  assert.equal(state.current, null);
  assert.deepEqual(el('roster').children, []);
  assert.deepEqual(el('messages').children, []);
  assert.equal(el('recipient').children[0].value, 'all');
  assert.equal(state.input.value, '');
  assert.equal(state.drafts.get('A').body, 'A draft');
});
