let tools;
let snapshot = null;
let dirty = false;
let fields;
let note;
let save;
let editor;
let saveConfiguration;
const durations = new Map();

function render(result) {
  snapshot = result;
  dirty = false;
  editor.value = JSON.stringify(result.configuration || { backends: {}, routes: {} }, null, 2);
  editor.disabled = !result.installed;
  saveConfiguration.disabled = !result.installed;
  durations.clear();
  fields.replaceChildren();
  note.textContent = result.installed ? 'Durations are seconds after the last request finishes. Zero unloads immediately. Changes take effect within a few seconds.' : result.message;
  for (const route of result.routes || []) {
    const row = tools.node('article');
    row.append(tools.node('h3', route.name), tools.node('p', `${route.backend} / ${route.model}`, 'operations-muted'));
    const input = tools.field(row, `operations-retention-${durations.size}`, 'Keep-alive (seconds, 0 or greater)', route.keep_alive_seconds, null, 'number');
    input.min = '0'; input.step = '1';
    input.addEventListener('input', () => { dirty = true; saveConfiguration.disabled = true; });
    durations.set(route.name, input);
    fields.append(row);
  }
  save.disabled = !result.installed || !durations.size;
}

export async function refreshInferenceRouting(force = false) {
  if (dirty && !force) return;
  try {
    const result = await tools.request('inference-router');
    if (!dirty || force) render(result);
  } catch (error) {
    note.textContent = error.message;
    if (force) throw error;
  }
}

export function initializeInferenceRouting(parent, dependencies) {
  tools = dependencies;
  const card = tools.section(parent, 'On-demand inference', 'Load the requested model, release competing engines and retain the model between requests. Use the router endpoint for these routes.', 'operations-routing-retention');
  note = tools.node('p', 'Reading router settings…', 'operations-muted');
  fields = tools.node('div', '', 'operations-retention-grid');
  save = tools.button('Save keep-alive', () => tools.task(async () => {
    const values = {};
    for (const [name, input] of durations) {
      if (!input.reportValidity() || !input.value.trim()) throw new Error('Enter a nonnegative duration in seconds for each route.');
      values[name] = Number(input.value);
    }
    render(await tools.request('inference-router', { revision: snapshot.revision, keep_alive_seconds: values }));
    tools.status('Model keep-alive settings saved.');
  }));
  save.disabled = true;
  const actions = tools.node('div', '', 'cookbook-actions');
  actions.append(save, tools.button('Reload settings', () => tools.task(() => refreshInferenceRouting(true))));
  const advanced = tools.node('details');
  advanced.append(tools.node('summary', 'Edit routes and backend endpoints'));
  advanced.append(tools.node('p', 'Add or remove routes and change native model names or local endpoints. Identity changes apply when the request queue is idle and release models at the old endpoints first. Keep competing installed engines in backends.', 'operations-muted'));
  editor = tools.field(advanced, 'operations-routing-configuration', 'Router configuration (JSON)', '', null, 'textarea');
  editor.rows = 20; editor.spellcheck = false;
  editor.addEventListener('input', () => { dirty = true; save.disabled = true; });
  saveConfiguration = tools.button('Save router configuration', () => tools.task(async () => {
    let configuration;
    try { configuration = JSON.parse(editor.value); }
    catch { throw new Error('Router configuration must be valid JSON.'); }
    render(await tools.request('inference-router', { revision: snapshot.revision, configuration }));
    tools.status('Router configuration saved. Route changes apply once the request queue is idle; inspect router status if they remain pending.');
  }));
  saveConfiguration.disabled = true;
  advanced.append(saveConfiguration);
  card.append(note, fields, actions, advanced);
  return card;
}
