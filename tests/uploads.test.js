const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const policy = JSON.parse(execFileSync(process.platform === 'win32' ? 'python' : 'python3', [
  '-B', '-X', 'utf8', '-c',
  "import json,sys;sys.path.insert(0,'src');from relay.uploads import upload_policy;print(json.dumps(upload_policy()))",
], { cwd: root, encoding: 'utf8' }));
const source = fs.readFileSync(path.join(root, 'src/relay/web/uploads.js'), 'utf8');

function frontend(fetch = async () => { throw new Error('Unexpected request'); }) {
  const inputs = {};
  const context = {
    uploadPolicy: policy, fetch, URLSearchParams, AbortController, setTimeout, clearTimeout,
    document: { getElementById: (id) => inputs[id] ??= {} },
    t: (key) => key, apiErrorMessage: (result) => result.error,
  };
  vm.runInNewContext(source + '\nthis.api = { selectUploads, uploadFiles, discardUploads };', context);
  return { ...context.api, inputs };
}

test('both file inputs and selection use the server extension policy', () => {
  const ui = frontend();
  assert.equal(ui.inputs['chat-file-input'].accept, policy.extensions.join(','));
  assert.equal(ui.inputs['board-file'].accept, policy.extensions.join(','));
  const files = policy.extensions.map((ext) => ({ name: 'file' + ext.toUpperCase(), size: 10 }));
  assert.equal(ui.selectUploads([], files).files.length, 13);
  for (const name of ['old.md', 'image.webp', 'image.jpeg', 'file.txt.exe']) {
    const result = ui.selectUploads([], [{ name, size: 1 }]);
    assert.equal(result.files.length, 0);
    assert.equal(result.error, 'attachmentTypes');
  }
});

test('10 MiB and 30 files are accepted; larger files and extra selections are rejected', () => {
  const ui = frontend();
  assert.equal(ui.selectUploads([], [{ name: 'limit.txt', size: policy.max_file_bytes }]).files.length, 1);
  assert.equal(ui.selectUploads([], [{ name: 'large.txt', size: policy.max_file_bytes + 1 }]).error, 'attachmentSizeLimit');
  const files = Array.from({ length: 31 }, (_, index) => ({ name: `${index}.txt`, size: 1 }));
  const result = ui.selectUploads([], files);
  assert.equal(result.files.length, 30);
  assert.equal(result.error, 'attachmentLimit');
  assert.equal(ui.selectUploads(result.files, [{ name: 'more.txt', size: 1 }]).files.length, 30);
});

test('uploads run sequentially and send small reference lists to cleanup', async () => {
  let active = 0, peak = 0;
  const requests = [], progress = [];
  const ui = frontend(async (url, options) => {
    requests.push({ url, options });
    active++;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setImmediate(resolve));
    active--;
    return { ok: true, json: async () => ({ upload_id: String(requests.length) }) };
  });
  const files = ui.selectUploads([], ['one.txt', 'two.csv', 'three.pdf'].map((name) => ({ name, size: 1 }))).files;
  const references = await ui.uploadFiles(files, (index, total) => progress.push([index, total]));
  assert.equal(peak, 1);
  assert.equal(references.length, 3);
  assert.deepEqual(progress, [[1, 3], [2, 3], [3, 3]]);
  assert.equal(requests[0].options.body, files[0].file);
  assert.equal(requests[0].options.headers['Content-Type'], 'application/octet-stream');
  await ui.discardUploads(references);
  assert.equal(requests[3].url, '/api/uploads/discard');
  assert.deepEqual(JSON.parse(requests[3].options.body), { attachments: [{ upload_id: '1' }, { upload_id: '2' }, { upload_id: '3' }] });
});

test('a failed upload cleans earlier temporary files without hiding the error', async () => {
  const requests = [];
  const ui = frontend(async (url, options) => {
    requests.push({ url, options });
    return requests.length === 2
      ? { ok: false, json: async () => ({ error: 'Rejected by the server' }) }
      : { ok: true, json: async () => ({ upload_id: 'first' }) };
  });
  const files = ui.selectUploads([], [{ name: 'one.txt', size: 1 }, { name: 'two.txt', size: 1 }]).files;
  await assert.rejects(ui.uploadFiles(files), /Rejected by the server/);
  assert.equal(requests[2].url, '/api/uploads/discard');
  assert.deepEqual(JSON.parse(requests[2].options.body), { attachments: [{ upload_id: 'first' }] });
});
