import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; }
  append(...items) { this.children.push(...items); }
  add(item) { this.append(item); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
}

function fixture(row, sources = []) {
  let changes = 0;
  let renders = 0;
  const context = vm.createContext({
    document: { createElement: tag => new Element(tag) },
    Option: class extends Element { constructor(text, value) { super('option'); this.textContent = text; this.value = value; } },
    row, sources, changed: () => changes++, render: () => renders++,
  });
  vm.runInContext(readFileSync(new URL('../static/js/operationsModelMetadata.js', import.meta.url), 'utf8').replace(/^export /gm, ''), context);
  const panel = vm.runInContext('metadataPanel(row, sources, changed, render, () => {})', context);
  return { panel, changes: () => changes, renders: () => renders };
}

test('local model selection preserves router mapping and clears assertions from the old identity', () => {
  const row = { index: null, metadata: { overrides: { mode: { operation: 'set', value: 'chat' } } } };
  const source = { id: 'embedding', provider: 'ollama', name: 'embed', model: 'openai/local-embed', api_base: 'http://127.0.0.1:8094/v1', status: 'fresh', detected: { mode: 'embedding' } };
  const h = fixture(row, [source]);
  const provider = h.panel.children[2].children[0];
  provider.value = 'ollama'; provider.listeners.change();
  const model = h.panel.children[3].children[0];
  model.value = 'embedding'; model.listeners.change();
  assert.equal(row.model, 'openai/local-embed');
  assert.equal(row.api_base, source.api_base);
  assert.equal(row.metadata.detected.mode, 'embedding');
  assert.equal(Object.keys(row.metadata.overrides).length, 0);
  assert.equal(row.metadata.reset_identity, true);
  assert.equal(h.renders(), 1);
});

test('false, unknown and reset remain separate operations', () => {
  const row = { index: 0, metadata: { overrides: {}, detected: { supports_vision: true } } };
  const h = fixture(row);
  const grid = h.panel.children.find(item => item.className === 'operations-grid');
  const vision = grid.children[4];
  const action = vision.children[1];
  action.value = 'false'; action.listeners.change();
  assert.equal(row.metadata.overrides.supports_vision.value, false);
  action.value = 'suppress'; action.listeners.change();
  assert.equal(row.metadata.overrides.supports_vision.operation, 'suppress');
  const reset = h.panel.children.at(-1);
  reset.listeners.click();
  assert.equal(Object.keys(row.metadata.overrides).length, 0);
  assert.equal(h.renders(), 1);
});
