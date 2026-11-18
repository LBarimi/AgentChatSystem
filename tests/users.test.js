const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/relay/web/users.js'), 'utf8');
const { isSelfUser, userLabel, chatUserIdentity } = vm.runInNewContext(source + '\n({ isSelfUser, userLabel, chatUserIdentity });');
const translate = (key, values) => key === 'selfUser' ? `${values.name} (나)` : '사용자';
const alice = { ip: '192.168.1.20', name: 'ALICE-PC' };
const bob = { ip: '192.168.1.21', name: 'BOB-PC' };

test('each viewer marks only their IP as self, even when computer names match', () => {
  assert.equal(userLabel(alice.name, alice.ip, alice, translate), 'ALICE-PC (나)');
  assert.equal(userLabel(alice.name, alice.ip, bob, translate), 'ALICE-PC');
  assert.equal(isSelfUser(bob.ip, { ...alice, name: bob.name }), false);
  assert.equal(isSelfUser(null, null), false);
  assert.equal(isSelfUser('', { ip: '' }), false);
});

test('chat shows saved computer names and highlights only the sender viewing it', () => {
  const message = { sender: 'user', user_ip: alice.ip, user_name: alice.name };
  const mine = chatUserIdentity(message, alice, [alice, bob], translate);
  const theirs = chatUserIdentity(message, bob, [alice, bob], translate);
  assert.equal(mine.own, true);
  assert.equal(mine.label, 'ALICE-PC (나)');
  assert.equal(theirs.own, false);
  assert.equal(theirs.label, 'ALICE-PC');
  assert.equal(chatUserIdentity(message, bob, [], translate).label, 'ALICE-PC');
});

test('IP fallback can display a name resolved after the message was sent', () => {
  const message = { sender: 'user', user_ip: alice.ip, user_name: alice.ip };
  assert.equal(chatUserIdentity(message, bob, [], translate).label, alice.ip);
  assert.equal(chatUserIdentity(message, bob, [alice], translate).label, alice.name);
  assert.equal(chatUserIdentity(message, alice, [], translate).label, 'ALICE-PC (나)');
});

test('legacy messages and agent replies are never claimed as mine', () => {
  for (const message of [{ sender: 'user' }, { sender: 'user', user_ip: null, user_name: null }]) {
    const result = chatUserIdentity(message, alice, [alice], translate);
    assert.equal(result.own, false);
    assert.equal(result.label, '사용자');
  }
  assert.equal(chatUserIdentity({ sender: 'codex', user_ip: alice.ip }, alice, [alice], translate).own, false);
});

test('self labels use the current language and unknown names fall back to IP', () => {
  assert.equal(userLabel('', alice.ip, alice, translate), `${alice.ip} (나)`);
  assert.equal(userLabel(alice.name, alice.ip, alice, (_, values) => `${values.name} (You)`), 'ALICE-PC (You)');
});
