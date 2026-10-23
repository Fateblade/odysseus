import { applyColors, applyFontDensity, getSaved } from './theme.js';
import { closeOverview, openOverview } from './operationsOverview.js?v=20261023overviewfix1';
import { openManagement, closeManagement } from './operationsManagement.js?v=20261023overviewfix1';
import { showSections, panelForSectionHash } from './operationsNavigation.js?v=20261022audit1';
import { openAudit } from './operationsAudit.js?v=20261022audit1';

const endpoint = '/api/operations/litellm';
let state = null;
let reviewed = null;
let busy = false;
let currentPanel = null;
const scrollPositions = new Map();

function selectPanel(name) {
  const workspace = el('operations-workspace');
  if (currentPanel) scrollPositions.set(currentPanel, workspace.scrollTop);
  currentPanel = name;
  const titles = {
    overview: ['Host overview', 'Compute, shared memory and inference services.'],
    litellm: ['Models & routing', 'Manage the deployments and aliases exposed by LiteLLM.'],
    management: ['Management', 'Control containers, inference servers and resident models.'],
    jobs: ['Scripts & TPS tests', 'Run benchmarks, inspect output and compare results.'],
    audit: ['Request journal', 'Trace callers, inference requests, host actions and model residency.'],
  };
  el('operations-page-title').textContent = titles[name][0];
  el('operations-page-description').textContent = titles[name][1];
  const overview = name === 'overview';
  for (const panel of ['overview', 'litellm', 'management', 'jobs', 'audit']) {
    el(`operations-${panel}-panel`).hidden = panel !== name;
    el(`operations-${panel}-tab`).setAttribute('aria-pressed', String(panel === name));
  }
  el('operations-refresh').hidden = name !== 'litellm';
  requestAnimationFrame(() => { if (currentPanel === name) workspace.scrollTop = scrollPositions.get(name) || 0; });
  closeManagement();
  if (overview) openOverview();
  else {
    closeOverview();
    if (name === 'litellm' && !state) task(load, 'Loading LiteLLM configuration…');
    if (name === 'management' || name === 'jobs') openManagement(name);
    if (name === 'audit') openAudit();
  }
  showSections(name);
}

const el = id => document.getElementById(id);
const names = () => [...new Set(state.draft.models.map(row => row.model_name).filter(Boolean))];

function message(text, error = false) {
  el('operations-status').textContent = text;
  el('operations-status').classList.toggle('operations-error', error);
}

function invalidate() {
  reviewed = null;
  el('operations-review').hidden = true;
  el('operations-apply').disabled = true;
}

async function request(path = '', body = null) {
  const response = await fetch(endpoint + path, {
    credentials: 'same-origin',
    ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  if (response.status === 401) {
    window.location.assign('/login');
    throw new Error('Sign in to manage LiteLLM.');
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'The operation failed.');
  return result;
}

async function task(work, progress) {
  if (busy) return;
  busy = true;
  message(progress);
  el('operations-content').setAttribute('aria-busy', 'true');
  document.querySelectorAll('#operations-content input, #operations-content select, #operations-content button').forEach(node => { node.disabled = true; });
  el('operations-refresh').disabled = true;
  try { await work(); }
  catch (error) { message(error.message, true); }
  finally {
    busy = false;
    el('operations-content').setAttribute('aria-busy', 'false');
    document.querySelectorAll('#operations-content input, #operations-content select, #operations-content button').forEach(node => { node.disabled = false; });
    el('operations-refresh').disabled = false;
    el('operations-apply').disabled = !reviewed?.changed;
  }
}

function field(label, value, change, options = null) {
  const wrap = document.createElement('label');
  wrap.className = 'operations-field';
  const title = document.createElement('span');
  title.textContent = label;
  const input = document.createElement(options ? 'select' : 'input');
  input.className = 'settings-input';
  if (options) {
    for (const option of new Set([value, ...options])) {
      const node = document.createElement('option');
      node.value = option;
      node.textContent = option || 'Choose a model group';
      input.append(node);
    }
  } else {
    input.type = 'text';
    input.maxLength = 2048;
    input.autocomplete = 'off';
    input.spellcheck = false;
  }
  input.value = value || '';
  input.addEventListener(options ? 'change' : 'input', () => {
    if (change(input.value) === false) {
      input.value = value || '';
      return;
    }
    value = input.value;
    invalidate();
  });
  wrap.append(title, input);
  return wrap;
}

function button(label, action) {
  const node = document.createElement('button');
  node.type = 'button';
  node.className = 'cookbook-btn';
  node.textContent = label;
  node.addEventListener('click', action);
  return node;
}

function renderModels() {
  el('operations-models').replaceChildren();
  state.draft.models.forEach((row, index) => {
    const card = document.createElement('fieldset');
    card.className = 'operations-card';
    const legend = document.createElement('legend');
    legend.textContent = `Deployment ${index + 1}`;
    const fields = document.createElement('div');
    fields.className = 'operations-grid';
    fields.append(
      field('Public model name', row.model_name, value => { row.model_name = value; }),
      field('Backend model', row.model, value => { row.model = value; }),
      field('Endpoint URL', row.api_base, value => { row.api_base = value; }),
    );
    const dropped = field('Parameters to drop (comma-separated)', (row.additional_drop_params || []).join(', '), value => {
      row.additional_drop_params = value.split(',').map(name => name.trim()).filter(Boolean);
    });
    const hint = document.createElement('span');
    hint.className = 'operations-muted';
    hint.textContent = 'Removed before forwarding this deployment’s requests. For non-thinking Qwen3-Coder: reasoning_effort, thinking. Leave empty to keep parameters.';
    dropped.append(hint);
    fields.append(dropped);
    const actions = document.createElement('div');
    actions.className = 'cookbook-actions';
    actions.append(
      button('Test endpoint', () => task(async () => {
        const result = await request('/probe', { revision: state.revision, draft: state.draft, index });
        message(result.message);
      }, 'Checking backend model discovery…')),
      button('Remove', () => {
        state.draft.models.splice(index, 1);
        invalidate();
        renderModels();
      }),
    );
    if (row.credentials_configured) {
      const note = document.createElement('span');
      note.className = 'operations-muted';
      note.textContent = 'Existing credentials retained';
      actions.append(note);
    }
    card.append(legend, fields, actions);
    el('operations-models').append(card);
  });
  el('operations-count').textContent = `${state.draft.models.length} deployments`;
}

function renderRouting() {
  const aliases = el('operations-aliases');
  aliases.replaceChildren();
  Object.entries(state.draft.aliases).forEach(([name, target]) => {
    let currentName = name;
    const row = document.createElement('div');
    row.className = 'operations-routing-row';
    row.append(
      field('Alias', name, value => {
        if (currentName !== value && Object.hasOwn(state.draft.aliases, value)) {
          message('Alias names must be unique.', true);
          return false;
        }
        const currentTarget = state.draft.aliases[currentName];
        delete state.draft.aliases[currentName];
        state.draft.aliases[value] = currentTarget;
        currentName = value;
      }),
      field('Target model group', target, value => { state.draft.aliases[currentName] = value; }, names()),
      button('Remove alias', () => { delete state.draft.aliases[currentName]; invalidate(); renderRouting(); }),
    );
    aliases.append(row);
  });
  const fallbacks = el('operations-fallbacks');
  fallbacks.replaceChildren();
  state.draft.fallbacks.forEach((item, index) => {
    let source = Object.keys(item)[0];
    const row = document.createElement('div');
    row.className = 'operations-routing-row';
    row.append(
      field('Model group', source, value => {
        const targets = item[source];
        delete item[source];
        item[value] = targets;
        source = value;
      }, names()),
      field('Fallback groups, in order (comma-separated)', (item[source] || []).join(', '), value => {
        item[source] = value.split(',').map(part => part.trim()).filter(Boolean);
      }),
      button('Remove fallback', () => { state.draft.fallbacks.splice(index, 1); invalidate(); renderRouting(); }),
    );
    fallbacks.append(row);
  });
}

function render() {
  el('operations-content').hidden = false;
  el('operations-setup').hidden = true;
  renderModels();
  renderRouting();
  const strategy = el('operations-strategy');
  strategy.replaceChildren();
  for (const value of new Set([state.draft.routing_strategy, ...state.strategies])) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    strategy.append(option);
  }
  strategy.value = state.draft.routing_strategy;
}

async function load() {
  const result = await request();
  invalidate();
  if (!result.configured) {
    state = null;
    el('operations-content').hidden = true;
    el('operations-setup').hidden = false;
    message(result.message);
    return;
  }
  state = result;
  render();
  message('Configuration loaded. Changes stay in this page until you review and apply them.');
}

el('operations-refresh').addEventListener('click', () => {
  if (state && !window.confirm('Reload configuration and discard edits on this page?')) return;
  task(load, 'Loading LiteLLM configuration…');
});
el('operations-add-model').addEventListener('click', () => {
  state.draft.models.push({ index: null, model_name: '', model: '', api_base: '', additional_drop_params: [] });
  invalidate();
  renderModels();
  el('operations-models').lastElementChild.querySelector('input').focus();
});
el('operations-add-alias').addEventListener('click', () => {
  if (Object.hasOwn(state.draft.aliases, '')) return;
  state.draft.aliases[''] = names()[0] || '';
  invalidate();
  renderRouting();
});
el('operations-add-fallback').addEventListener('click', () => {
  state.draft.fallbacks.push({ [names()[0] || '']: [] });
  invalidate();
  renderRouting();
});
el('operations-strategy').addEventListener('change', event => {
  state.draft.routing_strategy = event.target.value;
  invalidate();
});
el('operations-preview').addEventListener('click', () => task(async () => {
  reviewed = await request('/preview', { revision: state.revision, draft: state.draft });
  el('operations-diff').textContent = reviewed.diff || 'No configuration changes.';
  el('operations-review').hidden = false;
  message(reviewed.changed ? 'Changes validated. Review the diff before restarting LiteLLM.' : 'No configuration changes.');
}, 'Validating models and routing…'));
el('operations-apply').addEventListener('click', () => task(async () => {
  const result = await request('/apply', {
    revision: state.revision, draft: state.draft, review_digest: reviewed.review_digest,
  });
  state = result;
  invalidate();
  render();
  message('Configuration applied. LiteLLM health and model catalog verified; previous configuration saved on the host.');
}, 'Applying changes and restarting LiteLLM. This can take several minutes; keep this page open…'));

const theme = getSaved();
if (theme?.colors) applyColors(theme.colors);
if (theme?.font || theme?.density) applyFontDensity(theme.font, theme.density);
el('operations-overview-tab').addEventListener('click', () => selectPanel('overview'));
el('operations-litellm-tab').addEventListener('click', () => selectPanel('litellm'));
el('operations-management-tab').addEventListener('click', () => selectPanel('management'));
el('operations-jobs-tab').addEventListener('click', () => selectPanel('jobs'));
el('operations-audit-tab').addEventListener('click', () => selectPanel('audit'));
window.addEventListener('hashchange', () => {
  const panel = panelForSectionHash();
  if (panel) selectPanel(panel);
});
selectPanel(panelForSectionHash() || 'overview');
