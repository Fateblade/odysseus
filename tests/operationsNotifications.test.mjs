import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/js/operationsNotifications.js', import.meta.url), 'utf8').replace(/^export /gm, '');

function harness() {
  class Element {
    children = []; attributes = {}; events = {}; parent = null;
    setAttribute(key, value) { this.attributes[key] = value; }
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); this.parent = null; }
    contains(element) { return element === this || this.children.some(child => child.contains(element)); }
    addEventListener(name, callback) { this.events[name] = callback; }
  }
  const document = { body: new Element(), createElement: () => new Element(), activeElement: null };
  let now = 0, id = 0;
  const timers = new Map();
  const environment = { document, setTimeout(callback, delay) { timers.set(++id, { callback, at: now + delay }); return id; }, clearTimeout(key) { timers.delete(key); } };
  const context = vm.createContext({ environment });
  vm.runInContext(source, context);
  const notifier = vm.runInContext('createNotifier(environment)', context);
  return { ...notifier, document, timers,
    items: () => document.body.children[0]?.children || [],
    advance(ms) { now += ms; for (const [key, timer] of [...timers]) if (timer.at <= now) { timers.delete(key); timer.callback(); } },
  };
}

test('status expires after 5 seconds, warning after 30, errors never expire', () => {
  const h = harness();
  h.show('Started'); h.show('Partial readings', { severity: 'warning' }); h.show('Failed', { severity: 'error' });
  h.advance(4999); assert.equal(h.items().length, 3);
  h.advance(1); assert.equal(h.items().length, 2);
  h.advance(24999); assert.equal(h.items().length, 2);
  h.advance(1); assert.equal(h.items().length, 1);
  h.advance(1000000); assert.equal(h.items().length, 1);
  assert.equal(h.items()[0].attributes.role, 'alert');
});

test('every severity can be dismissed manually and cancels its timer', () => {
  for (const severity of ['status', 'warning', 'error']) {
    const h = harness(); h.show('Message', { severity });
    const close = h.items()[0].children[1];
    assert.equal(close.attributes['aria-label'], `Dismiss ${severity} notification`);
    close.events.click(); assert.equal(h.items().length, 0); assert.equal(h.timers.size, 0);
  }
});

test('polling cannot resurrect a dismissed unchanged error; a recovered source can fail again', () => {
  const h = harness();
  const dismiss = h.show('Offline', { severity: 'error', key: 'host' });
  h.show('Offline', { severity: 'error', key: 'host' }); assert.equal(h.items().length, 1);
  dismiss(); h.show('Offline', { severity: 'error', key: 'host' }); assert.equal(h.items().length, 0);
  h.reset('host'); h.show('Offline', { severity: 'error', key: 'host' }); assert.equal(h.items().length, 1);
});

test('new statuses and recovery never remove an existing error', () => {
  const h = harness();
  h.show('Failed', { severity: 'error', key: 'host' }); h.reset('host');
  h.show('Recovered'); h.advance(5000); assert.equal(h.items().length, 1);
  assert.equal(h.items()[0].children[2].textContent, 'Failed');
});

test('messages are rendered as text and status announcements stay polite', () => {
  const h = harness(); h.show('<script>unsafe()</script>');
  assert.equal(h.items()[0].children[2].textContent, '<script>unsafe()</script>');
  assert.equal(h.items()[0].attributes.role, 'status');
});
