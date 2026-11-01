import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
const source = readFileSync(new URL('../static/js/operationsInferenceRouting.js', import.meta.url), 'utf8').replace(/^export /gm, '');
function setup() {
  const elements = new Map(), buttons = new Map(), calls = [];
  const node = () => ({ append() {}, replaceChildren() {}, addEventListener(name, callback) { this[name] = callback; } });
  const configuration = { backends: { maya: 'http://127.0.0.1:8092' }, routes: { ds: { backend: 'maya', model: 'ds', keep_alive_seconds: 600 } } };
  const result = { installed: true, revision: 'one', configuration, routes: [{ name: 'ds', ...configuration.routes.ds }] };
  const tools = { node, section: node, status() {}, task: action => action(),
    field(parent, id, title, value) { const item = { ...node(), value: String(value), reportValidity: () => true }; elements.set(id, item); return item; },
    button(title, action) { const item = { ...node(), action }; buttons.set(title, item); return item; },
    async request(path, payload) { calls.push(payload); return result; } };
  const context = vm.createContext({ tools });
  vm.runInContext(source, context);
  vm.runInContext('initializeInferenceRouting({}, globalThis.tools)', context);
  return { elements, buttons, calls, run: code => vm.runInContext(code, context) };
}
test('JSON editor preserves drafts, validates JSON and submits its revision', async () => {
  const h = setup();
  await h.run('refreshInferenceRouting()');
  const editor = h.elements.get('operations-routing-configuration');
  editor.value = '{invalid'; editor.input();
  assert.equal(h.buttons.get('Save keep-alive').disabled, true);
  await h.run('refreshInferenceRouting()');
  assert.equal(editor.value, '{invalid');
  await assert.rejects(h.buttons.get('Save router configuration').action(), /valid JSON/);
  assert.equal(h.calls.length, 1);
  editor.value = JSON.stringify({ backends: {}, routes: { replacement: { backend: 'maya', model: 'new' } } });
  await h.buttons.get('Save router configuration').action();
  assert.equal(h.calls[1].revision, 'one');
  assert.equal(h.calls[1].configuration.routes.replacement.model, 'new');
});
