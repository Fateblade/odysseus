export function createModelPicker(input) {
  const wrapper = document.createElement('div'); wrapper.className = 'operations-model-picker';
  const label = input.closest('label');
  if (label) { label.htmlFor = input.id; label.after(wrapper); } else input.before(wrapper);
  wrapper.classList.add('operations-field'); wrapper.append(input); input.removeAttribute('list');
  input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list'); input.autocomplete = 'off';
  const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'cookbook-btn'; toggle.textContent = 'Browse installed models';
  const list = document.createElement('div'); list.id = 'operations-installed-model-picker'; list.className = 'operations-model-options';
  list.setAttribute('role', 'listbox'); list.setAttribute('aria-label', 'Installed Ollama models'); input.setAttribute('aria-controls', list.id);
  wrapper.append(toggle, list);
  let models = [], selected = -1, opened = false, browseAll = false;
  const close = () => { opened = false; list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); };
  const render = (all = false) => {
    browseAll = all; opened = true; selected = -1; input.removeAttribute('aria-activedescendant');
    list.hidden = false; input.setAttribute('aria-expanded', 'true'); list.replaceChildren();
    const query = all ? '' : input.value.trim().toLocaleLowerCase();
    for (const model of models.filter(model => model.name.toLocaleLowerCase().includes(query))) {
      const item = document.createElement('button'); item.type = 'button'; item.setAttribute('role', 'option');
      item.id = `${list.id}-${list.children.length}`; item.setAttribute('aria-selected', 'false'); item.textContent = model.name; item.title = model.name;
      if (model.loaded) { const tag = document.createElement('span'); tag.textContent = 'Loaded'; item.append(tag); }
      item.addEventListener('click', () => { input.value = model.name; input.dispatchEvent(new Event('change', { bubbles: true })); close(); input.focus(); });
      list.append(item);
    }
    if (!list.children.length) { const empty = document.createElement('p'); empty.textContent = 'No matching installed models. You can still enter a model name to pull.'; list.append(empty); }
  };
  input.addEventListener('input', () => render());
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { close(); return; }
    if (event.key === 'Tab') { close(); return; }
    if (event.key === 'Enter' && opened && selected >= 0) { event.preventDefault(); list.querySelectorAll('button')[selected]?.click(); return; }
    if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault(); if (!opened) render(true);
    const items = [...list.querySelectorAll('button')]; if (!items.length) return;
    selected = selected < 0 ? event.key === 'ArrowDown' ? 0 : items.length - 1 : (selected + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items.forEach((item, index) => item.setAttribute('aria-selected', String(index === selected)));
    input.setAttribute('aria-activedescendant', items[selected].id); items[selected].scrollIntoView({ block: 'nearest' });
  });
  toggle.addEventListener('click', () => { if (opened) close(); else { render(true); input.focus(); } });
  wrapper.addEventListener('focusout', event => { if (!wrapper.contains(event.relatedTarget)) close(); });
  document.addEventListener('click', event => { if (!wrapper.contains(event.target)) close(); });
  close();
  return { update(installed, loaded) { models = installed.map(model => ({ ...model, loaded: loaded.some(row => row.name === model.name) })).sort((a, b) => Number(b.loaded) - Number(a.loaded) || a.name.localeCompare(b.name)); if (opened) render(browseAll); } };
}
