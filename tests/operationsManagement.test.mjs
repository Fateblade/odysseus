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
  const notifications = [];
  const context = vm.createContext({ document: { getElementById: element }, pending, notify(message, options) { notifications.push({ message, options }); }, resetNotification() {} });
  vm.runInContext(source, context);
  vm.runInContext(`active = 'management'; request = () => pending; updateResourceControls = () => {};`, context);
  element('operations-maya-context').value = '8192';
  element('operations-maya-drafting').value = 'Off';
  return { element, resolve, reject, notifications, run: code => vm.runInContext(code, context) };
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

test('Maya polling preserves a speculative-decoding switch edit in flight', async () => {
  const h = harness();
  h.element('operations-maya-drafting').checked = false;
  const loading = h.run('loadMaya(true)');
  h.element('operations-maya-drafting').checked = true;
  h.resolve({ ...settings, drafting: false });
  await loading;
  assert.equal(h.element('operations-maya-drafting').checked, true);
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

test('background updates stay quiet even while a user action overlaps polling', () => {
  const h = harness();
  h.run("busy = true; polling = true; status('Recent jobs', false, true); status('Benchmark started.');");
  assert.deepEqual(h.notifications.map(item => item.message), ['Benchmark started.']);
});

test('start is allowed only when stopped; stop and restart only when running', () => {
  const h = harness();
  for (const state of ['running', 'stopped', 'transitioning', 'unknown', 'pending']) {
    assert.equal(h.run(`canControl('start', '${state}')`), state === 'stopped');
    assert.equal(h.run(`canControl('stop', '${state}')`), state === 'running');
    assert.equal(h.run(`canControl('restart', '${state}')`), state === 'running');
    assert.equal(h.run(`canControl('logs', '${state}')`), true);
  }
});

test('stale readings become unknown and pending jobs override sampled state', () => {
  const h = harness();
  h.run("serviceStates = {maya: {state: 'running'}}; controlSampledAt = Date.now();");
  assert.equal(h.run("controlState('service', 'maya')"), 'running');
  h.run('controlSampledAt = Date.now() - 15001;');
  assert.equal(h.run("controlState('service', 'maya')"), 'unknown');
  h.run("pendingControls.set('service:maya', {id:'job'});");
  assert.equal(h.run("controlState('service', 'maya')"), 'pending');
});

test('terminal jobs release controls, malformed job state keeps them locked', async () => {
  for (const state of ['completed', 'failed', 'timed_out', 'cancelled', 'unexpected']) {
    const h = harness();
    h.run("pendingControls.set('service:maya', {id:'job', operation:'stop'});");
    const refresh = h.run('refreshPendingControls().then(ready => { globalThis.finished = ready; })');
    h.resolve({ job: { state } });
    await refresh;
    assert.equal(h.run("controlState('service', 'maya')"), 'pending', 'Terminal jobs stay locked until a fresh sample is ready');
    h.run('settlePendingControls(finished)');
    assert.equal(h.run("pendingControls.has('service:maya')"), state === 'unexpected');
  }
});

test('Strata load and unload require confirmed opposite residency', () => {
  const h = harness();
  for (const state of ['loaded', 'unloaded', 'unknown', 'pending']) {
    assert.equal(h.run(`canControl('load', '${state}')`), state === 'unloaded');
    assert.equal(h.run(`canControl('unload', '${state}')`), state === 'loaded');
  }
  assert.equal(h.run("strataModelState({status:'unavailable',loaded:false})"), 'unknown');
  assert.equal(h.run("strataModelState({status:'ok',loaded:false})"), 'unloaded');
  assert.equal(h.run("strataModelState({status:'ok',loaded:true})"), 'loaded');
  assert.equal(h.run("strataModelState({status:'ok',loaded:null})"), 'unknown');
});

test('pending Strata residency blocks service mutations too', () => {
  const h = harness();
  h.run("serviceStates = {strata:{state:'running'}}; controlSampledAt = Date.now(); pendingControls.set('strata:model',{id:'load-job'});");
  assert.equal(h.run("controlState('service','strata')"), 'pending');
});

test('active routed inference disables service mutations', () => {
  const h = harness();
  h.run("inferenceBusy = true; serviceStates = {maya:{state:'running'}}; controlSampledAt=Date.now();");
  assert.equal(h.run("controlState('service','maya')"), 'pending');
  assert.equal(h.run("canControl('stop', controlState('service','maya'))"), false);
});
