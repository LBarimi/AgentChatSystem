const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Run the actual dialog loading functions with deferred requests and a small DOM facade.
const app = fs.readFileSync(path.join(__dirname, '../src/relay/web/app.js'), 'utf8');
const functions = app.slice(app.indexOf('function setSetupPromptLoading()'), app.indexOf('\nel("setup-prompts-retry").addEventListener'));

function fixture() {
  const nodes = new Map();
  const requests = [];
  const copies = [{ disabled: false }, { disabled: false }, { disabled: false }];
  const el = (id) => {
    if (!nodes.has(id)) nodes.set(id, { textContent: 'old', value: '', hidden: false, open: true });
    return nodes.get(id);
  };
  const state = {
    setupRequest: 0, setupAgent: 'codex', activeChannel: 'general', current: null, iconInitializations: 0,
    document: { documentElement: { lang: 'ko' } }, languageSelect: { selectedOptions: [{ textContent: '한국어' }] },
    roleName: { codex: 'Codex', claude: 'Claude' }, URLSearchParams, el,
    setupDialog: { open: true, querySelectorAll: () => copies, setAttribute() {}, removeAttribute() {}, showModal() { this.open = true; } },
    showMessage(id, message) { el(id).textContent = message; el(id).hidden = !message; },
    initializeIconEditor() { state.iconInitializations++; },
    t(key, values) { return state.document.documentElement.lang + ':' + key + (values ? ':' + Object.values(values).join(',') : ''); },
    request(url) { return new Promise((resolve, reject) => requests.push({ url, resolve, reject })); },
  };
  vm.createContext(state);
  vm.runInContext(functions, state);
  const data = (language, id = 'codex') => ({ id, name: id, role: id, config: 'mcpServers', install_prompt: language + ':install', prompt: language + ':listen' });
  return { state, el, requests, copies, data };
}

test('opening a dialog loads the requested language and enables copying only after completion', async () => {
  const { state, el, requests, copies, data } = fixture();
  state.setupDialog.open = false;
  const load = state.openSetup('codex');
  assert.match(requests[0].url, /agent=codex&lang=ko/);
  assert.ok(copies.every(button => button.disabled));
  assert.equal(el('setup-prompt').textContent, '');
  requests[0].resolve(data('ko'));
  await load;
  assert.ok(state.setupDialog.open);
  assert.equal(state.iconInitializations, 1);
  assert.equal(el('setup-prompt').textContent, 'ko:listen');
  assert.equal(el('setup-prompt').lang, 'ko');
  assert.ok(copies.every(button => !button.disabled));
});

test('language refresh keeps nickname/icon drafts and expanded sections intact', async () => {
  const { state, el, requests, copies, data } = fixture();
  el('agent-name').value = 'Unfinished nickname';
  state.document.documentElement.lang = 'ja';
  state.languageSelect.selectedOptions[0].textContent = '日本語';
  const load = state.openSetup('codex', true);
  requests[0].resolve(data('ja'));
  await load;
  assert.equal(el('agent-name').value, 'Unfinished nickname');
  assert.equal(state.iconInitializations, 0);
  assert.equal(el('setup-install-preview').open, true);
  assert.equal(el('setup-manual-config').open, true);
  assert.equal(el('setup-config').textContent, 'mcpServers');
  assert.equal(el('setup-install-prompt').lang, 'ja');
  assert.equal(el('setup-language').textContent, 'ja:promptLanguage:日本語');
  assert.ok(copies.every(button => !button.disabled));
});

test('a slower previous language request cannot overwrite the latest prompt or report a stale error', async () => {
  const { state, el, requests, data } = fixture();
  const older = state.openSetup('codex', true);
  state.document.documentElement.lang = 'de';
  const newer = state.openSetup('codex', true);
  requests[1].resolve(data('de'));
  await newer;
  requests[0].reject(new Error('old request failure'));
  await older;
  assert.equal(el('setup-prompt').textContent, 'de:listen');
  assert.ok(el('setup-prompts-error').hidden);
});

test('closing during refresh prevents a late response from reopening the dialog', async () => {
  const { state, el, requests, data } = fixture();
  const load = state.openSetup('codex', true);
  state.setupDialog.open = false;
  state.setupRequest++;
  requests[0].resolve(data('ko'));
  await load;
  assert.equal(state.setupDialog.open, false);
  assert.equal(el('setup-prompt').textContent, '');
});

test('failed refresh offers retry and never copies stale content', async () => {
  const { state, el, requests, copies, data } = fixture();
  const load = state.openSetup('codex', true);
  requests[0].reject(new Error('offline'));
  await load;
  assert.equal(el('setup-prompts-retry').hidden, false);
  assert.match(el('setup-prompts-error').textContent, /ko:promptLoadFailed/);
  assert.equal(el('setup-install-prompt').textContent, '');
  assert.ok(copies.every(button => button.disabled));
  const retry = state.openSetup('codex', true);
  requests[1].resolve(data('ko'));
  await retry;
  assert.ok(el('setup-prompts-retry').hidden);
  assert.ok(el('setup-prompts-error').hidden);
  assert.ok(copies.every(button => !button.disabled));
});
