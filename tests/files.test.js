const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const api = vm.createContext({ URLSearchParams, Intl });
vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/relay/web/files.js'), 'utf8'), api);

test('directory URLs preserve the channel and Unicode path without injecting query values', () => {
  const view = { channel: 'work & review', kind: 'shared', path: 'docs/검토 #1 & final', offset: 200 };
  const url = api.folderURL(view, view.path, view.offset);
  assert.ok(url.startsWith('/files?'));
  assert.deepEqual(JSON.parse(JSON.stringify(api.fileView(url.split('?')[1]))), view);
  const nested = api.fileView(api.folderURL(view, 'docs/notes').split('?')[1]);
  assert.equal(nested.offset, 0);
  assert.equal(nested.channel, view.channel);
  assert.equal(api.fileView(api.folderURL(view, '', 0, 'logs').split('?')[1]).kind, 'logs');
});

test('breadcrumbs return every ancestor and keep the shared root inside its channel', () => {
  const view = api.fileView('?channel=other&kind=shared&path=docs%2F日本語%2Ffinal');
  const crumbs = JSON.parse(JSON.stringify(api.fileCrumbs(view)));
  assert.deepEqual(crumbs.map(item => item.path), ['', 'docs', 'docs/日本語', 'docs/日本語/final']);
  for (const crumb of crumbs) {
    const destination = api.fileView(api.folderURL(view, crumb.path).split('?')[1]);
    assert.equal(destination.channel, 'other');
    assert.equal(destination.kind, 'shared');
  }
});

test('invalid pagination falls back safely and path validation remains on the server', () => {
  for (const offset of ['-1', 'NaN', 'Infinity', '1.5', '9007199254740992']) {
    assert.equal(api.fileView('?offset=' + offset).offset, 0);
  }
  assert.equal(api.fileView('?path=..%2Fsecret').path, '../secret');
  assert.equal(api.fileView('?path=docs%5Cnotes').path, 'docs/notes');
  assert.equal(api.fileView('').channel, 'general');
});

test('file sizes include empty files and use the selected number formatting', () => {
  assert.equal(api.fileSize(0, 'en'), '0 B');
  assert.equal(api.fileSize(1536, 'en'), '1.5 KiB');
  assert.equal(api.fileSize(1536, 'de'), '1,5 KiB');
  assert.equal(api.fileSize(1024 * 1024, 'ko'), '1 MiB');
});
