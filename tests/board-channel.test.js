const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const board = fs.readFileSync(path.join(__dirname, '../src/relay/web/board.js'), 'utf8');
const load = board.slice(board.indexOf('async function loadBoard()'), board.indexOf('\nfunction showBoardPage('));
const reset = board.slice(board.indexOf('function resetBoardChannel()'), board.indexOf('\nfunction showBoardHeading('));

function fixture() {
  const nodes = new Map(), requests = [];
  const node = () => ({ hidden: false, textContent: '', value: '', children: [], close() {}, reset() {},
    setAttribute() {}, replaceChildren(...children) { this.children = children; } });
  const el = id => { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); };
  const state = {
    activeChannel: 'A', boardOpen: true, boardRequest: 0, boardPages: [], boardSelected: null,
    boardRenderKey: '', boardNext: null, boardFiles: [], boardDraftId: null, boardPosting: false,
    boardView: node(), boardPost: node(), boardList: node(), boardForm: node(), boardDialog: node(), boardDeleteDialog: node(),
    el, URLSearchParams, boardCard: post => post.title, renderBoardPost() {}, showBoardFiles() {},
    showMessage(id, text) { el(id).textContent = text; }, t: key => key,
    request(url, payload) { return new Promise((resolve, reject) => requests.push({ url, payload, resolve, reject })); },
  };
  vm.createContext(state);
  vm.runInContext(load + reset, state);
  return { state, requests, el };
}

test('board queries carry channel scope and an older response cannot replace the new channel', async () => {
  const { state, requests } = fixture();
  const older = state.loadBoard();
  assert.equal(requests[0].url, '/api/board?channel=A');
  state.resetBoardChannel();
  state.activeChannel = 'B';
  state.boardOpen = true;
  const newer = state.loadBoard();
  assert.equal(requests[1].url, '/api/board?channel=B');
  requests[1].resolve({ posts: [{ title: 'B only' }], next_before: null });
  await newer;
  requests[0].resolve({ posts: [{ title: 'A secret' }], next_before: null });
  await older;
  assert.deepEqual(state.boardList.children, ['B only']);
});

test('switching channels clears board selection, files and drafts and cancels pending detail responses', async () => {
  const { state, requests } = fixture();
  state.boardSelected = 42;
  state.boardPages.push(99);
  state.boardFiles.push({ name: 'A.txt' });
  state.boardDraftId = 'A-draft';
  const pending = state.loadBoard();
  assert.match(requests[0].url, /channel=A.*id=42/);
  state.resetBoardChannel();
  assert.equal(state.boardSelected, null);
  assert.equal(state.boardFiles.length, 0);
  assert.equal(state.boardPages.length, 0);
  assert.equal(state.boardDraftId, null);
  assert.equal(state.boardOpen, false);
  requests[0].resolve({ id: 42, title: 'A secret' });
  await pending;
  assert.deepEqual(state.boardPost.children, []);
});

test('a pending board upload stays addressed to its original channel after navigation', async () => {
  const { state, requests, el } = fixture();
  let submit, finishUpload;
  state.boardForm.addEventListener = (_, callback) => { submit = callback; };
  state.uploadFiles = () => new Promise(resolve => { finishUpload = resolve; });
  state.discardUploads = async () => {};
  state.draftId = () => 'A-request';
  state.showBoardPage = () => assert.fail('An old submission must not navigate the new channel');
  el('board-title-input').value = 'A instructions';
  el('board-body-input').value = 'Only A';
  vm.runInContext(board.slice(board.indexOf('boardForm.addEventListener("submit"')), state);
  const pending = submit({ preventDefault() {} });
  state.resetBoardChannel();
  state.activeChannel = 'B';
  finishUpload([]);
  await new Promise(setImmediate);
  assert.equal(requests[0].payload.channel_id, 'A');
  assert.equal(requests[0].payload.body, 'Only A');
  requests[0].resolve({ id: 7 });
  await pending;
  assert.equal(state.boardSelected, null);
});
