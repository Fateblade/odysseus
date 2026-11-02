import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
class Element {
  constructor() { this.children = []; this.attributes = {}; this.listeners = {}; this.classList = { add() {} }; }
  append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } }
  before() {} after() {} closest() { return null; }
  setAttribute(name, value) { this.attributes[name] = value; } removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  replaceChildren() { this.children = []; }
  querySelectorAll() { return this.children.filter(item => item.tag === 'button'); }
  contains(target) { return this === target || this.children.some(item => item.contains(target)); }
  focus() {} scrollIntoView() {}
  click() { this.listeners.click?.(); }
  dispatchEvent(event) { this.listeners[event.type]?.(event); }
}
function fixture() {
  const doc = { createElement(tag) { const item = new Element(); item.tag = tag; return item; }, addEventListener() {} };
  const input = new Element(); input.value = ''; input.id = 'model';
  const context = vm.createContext({ document: doc, input, Event: class { constructor(type) { this.type = type; } } });
  vm.runInContext(readFileSync(new URL('../static/js/operationsModelPicker.js', import.meta.url), 'utf8').replace(/^export /gm, ''), context);
  const picker = vm.runInContext('createModelPicker(input)', context);
  const wrapper = input.parent, toggle = wrapper.children[1], list = wrapper.children[2];
  return { input, picker, toggle, list };
}
test('picker wraps complete model names, prioritizes loaded models and supports keyboard selection', () => {
  const { input, picker, toggle, list } = fixture();
  const long = 'hf.co/owner/very-long-model-name:Q4_K_M';
  picker.update([{ name: long }, { name: 'z-loaded' }], [{ name: 'z-loaded' }]);
  toggle.click();
  assert.equal(list.children[0].textContent, 'z-loaded');
  assert.equal(list.children[1].textContent, long);
  input.listeners.keydown({ key: 'ArrowUp', preventDefault() {} });
  input.listeners.keydown({ key: 'Enter', preventDefault() {} });
  assert.equal(input.value, long);
  assert.equal(list.hidden, true);
  input.value = 'loaded'; input.listeners.input();
  assert.equal(list.children.length, 1);
  assert.equal(list.children[0].textContent, 'z-loaded');
  input.listeners.keydown({ key: 'Escape' });
  assert.equal(list.hidden, true);
});
