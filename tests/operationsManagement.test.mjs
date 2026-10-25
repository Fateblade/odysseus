import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/js/operationsManagement.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '');

function harness() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', hidden: false, disabled: false, textContent: '', classList: { toggle() {} },
      click() { throw new Error(`Unexpected navigation to ${id}`); } });
    return elements.get(id);
  };
  let resolve;
  let reject;
  const pending = new Promise((yes, no) => { resolve = yes; reject = no; });
  const context = vm.createContext({ document: { getElementById: element }, pending });
  vm.runInContext(source, context);
  vm.runInContext(`active = 'management'; request = () => pending;`, context);
  element('operations-maya-context').value = '8192';
  element('operations-maya-drafting').value = 'Off';
  return { element, resolve, reject, run: code => vm.runInContext(code, context) };
}

const settings = { configured: true, active_context: 8192, minimum_context: 4096, maximum_context: 128000 };

test('Maya polling keeps controls visible while awaiting host settings', async () => {
  const h = harness();
  const loading = h.run('loadMaya(true)');
  assert.equal(h.element('operations-maya-config-fields').hidden, false);
  h.resolve(settings);
  await loading;
  assert.equal(h.element('operations-maya-config-fields').hidden, false);
});

for (const backend of ['maya', 'dwarfstar']) {
  test(`${backend} polling preserves edits made during an in-flight refresh`, async () => {
    const h = harness();
    const loading = h.run(backend === 'maya' ? 'loadMaya(true)' : 'loadDwarfstar(true)');
    h.element(`operations-${backend}-context`).value = '32768';
    h.resolve({ ...settings, configured_context: 8192 });
    await loading;
    assert.equal(h.element(`operations-${backend}-context`).value, '32768');
  });
}

test('transient Maya errors preserve the last controls and label stale settings', async () => {
  const h = harness();
  h.run('mayaState = { active_context: 8192 }');
  const loading = h.run('loadMaya(true)');
  h.reject(new Error('Temporary connection failure'));
  await loading;
  assert.equal(h.element('operations-maya-config-fields').hidden, false);
  assert.match(h.element('operations-maya-config-status').textContent, /stale/);
});

test('queued service actions remain on Management and retain their job selection', async () => {
  const h = harness();
  h.run('confirmOperation = async () => true; refreshResidency = async () => {};');
  const operation = h.run(`manage({ target: 'service', name: 'maya', operation: 'stop' })`);
  h.resolve({ job: { id: 'service-job' } });
  await operation;
  assert.equal(h.run('active'), 'management');
  assert.equal(h.run('selectedJob'), 'service-job');
  assert.match(h.element('operations-management-message').textContent, /Host job started/);
});
