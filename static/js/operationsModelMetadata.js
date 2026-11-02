const limits = ['context_window', 'max_input_tokens', 'max_output_tokens'];
const flags = ['supports_function_calling', 'supports_vision', 'supports_reasoning'];
const fields = [...limits, ...flags, 'mode'];
const labels = { context_window: 'Total context', max_input_tokens: 'Maximum input', max_output_tokens: 'Maximum output', supports_function_calling: 'Tools', supports_vision: 'Vision', supports_reasoning: 'Reasoning', mode: 'Task' };

export function metadataPanel(row, sources, changed, rerender, refresh) {
  row.metadata ||= { overrides: {}, source_id: null };
  const metadata = row.metadata;
  const panel = document.createElement('details');
  panel.open = row.index == null || Boolean(metadata.source_id);
  const summary = document.createElement('summary');
  summary.textContent = 'Capabilities and context guidance';
  panel.append(summary);
  const text = value => {
    const node = document.createElement('p');
    node.className = 'operations-muted';
    node.textContent = value;
    panel.append(node);
    return node;
  };
  text('Guidance for clients. These settings do not enforce request limits or change runtime allocation.');
  const sourceLabel = document.createElement('label');
  sourceLabel.className = 'operations-field';
  sourceLabel.textContent = 'Inference provider';
  const provider = document.createElement('select');
  provider.className = 'settings-input';
  const selected = sources.find(source => source.id === metadata.source_id);
  for (const id of ['', ...new Set(sources.map(source => source.provider))]) {
    provider.add(new Option(id || 'Manual / remote', id));
  }
  provider.value = selected?.provider || '';
  sourceLabel.append(provider);
  panel.append(sourceLabel);
  const modelLabel = document.createElement('label');
  modelLabel.className = 'operations-field';
  modelLabel.textContent = 'Installed model / configured route';
  const model = document.createElement('select');
  model.className = 'settings-input';
  modelLabel.append(model);
  panel.append(modelLabel);
  function populate() {
    model.replaceChildren(new Option('Choose a model', ''));
    for (const source of sources.filter(item => item.provider === provider.value && item.status === 'fresh')) {
      model.add(new Option(`${source.name} (${source.model})`, source.id));
    }
    model.value = metadata.source_id || '';
    modelLabel.hidden = !provider.value;
  }
  populate();
  provider.addEventListener('change', () => {
    metadata.source_id = null;
    metadata.overrides = {};
    metadata.detected = {};
    metadata.reset_identity = true;
    populate();
    changed();
    if (!provider.value) rerender();
  });
  model.addEventListener('change', () => {
    const source = sources.find(item => item.id === model.value);
    if (!source) return;
    row.model = source.model;
    row.api_base = source.api_base;
    metadata.source_id = source.id;
    metadata.detected = source.detected;
    metadata.source = source;
    metadata.overrides = {};
    metadata.reset_identity = true;
    changed();
    rerender();
  });
  const refreshButton = document.createElement('button');
  refreshButton.type = 'button';
  refreshButton.className = 'cookbook-btn';
  refreshButton.textContent = 'Refresh local models and detected values';
  refreshButton.addEventListener('click', refresh);
  panel.append(refreshButton);
  if (metadata.source?.last_success_at) {
    text(`Source: ${metadata.source.provider}; ${metadata.source.status}; last success ${new Date(metadata.source.last_success_at * 1000).toLocaleString()}`);
    text(`Runtime context: ${metadata.source.details?.active_context ?? 'unknown'}; published model context: ${metadata.source.details?.published_context ?? 'unknown'}`);
  }
  if (metadata.identity_changed) text('Identity changed. Reselect a local model or reset metadata before applying.');
  if (metadata.conflicts?.length) text(`Detected limits conflict with saved assertions: ${metadata.conflicts.join(', ')}. Reduce or reset these fields before publishing.`);
  text(`Last published by Odysseus: ${metadata.published ? JSON.stringify(metadata.published) : 'not yet published'}. Edits remain pending until Review and Apply.`);
  const grid = document.createElement('div');
  grid.className = 'operations-grid';
  for (const field of fields) {
    const wrap = document.createElement('label');
    wrap.className = 'operations-field';
    const title = document.createElement('span');
    const detected = metadata.detected?.[field];
    title.textContent = `${labels[field]} — detected: ${detected ?? 'unknown'}`;
    const action = document.createElement('select');
    action.className = 'settings-input';
    const boolean = flags.includes(field);
    for (const [value, label] of [['inherit', 'Inherit detected'], ...(boolean ? [['true', 'Yes'], ['false', 'No']] : [['set', 'Set value']]), ['suppress', 'Unknown']]) action.add(new Option(label, value));
    const operation = metadata.overrides[field] || { operation: 'inherit' };
    action.value = operation.operation === 'set' && boolean ? String(operation.value) : operation.operation;
    const value = document.createElement(field === 'mode' ? 'select' : 'input');
    value.className = 'settings-input';
    if (field === 'mode') for (const mode of ['chat', 'embedding', 'completion']) value.add(new Option(mode, mode));
    else { value.type = 'number'; value.min = '1'; value.step = '1'; if (detected != null) value.max = String(detected); }
    value.value = operation.value ?? detected ?? (field === 'mode' ? 'chat' : '');
    const effective = document.createElement('span');
    effective.className = 'operations-muted';
    function update(save) {
      value.hidden = boolean || action.value !== 'set';
      if (save) {
        metadata.overrides[field] = ['true', 'false'].includes(action.value)
          ? { operation: 'set', value: action.value === 'true' }
          : action.value === 'set' ? { operation: 'set', value: field === 'mode' ? value.value : Number(value.value) } : { operation: action.value };
        changed();
      }
      const current = metadata.overrides[field] || { operation: 'inherit' };
      effective.textContent = `Effective: ${current.operation === 'suppress' ? 'unknown' : current.operation === 'set' ? current.value : detected ?? 'unknown'}; ${current.operation === 'set' ? 'operator assertion / restriction' : 'inherited'}`;
    }
    action.addEventListener('change', () => update(true));
    value.addEventListener('input', () => update(true));
    update(false);
    wrap.append(title, action, value, effective);
    grid.append(wrap);
  }
  panel.append(grid);
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.className = 'cookbook-btn';
  reset.textContent = 'Reset all to detected';
  reset.addEventListener('click', () => { metadata.overrides = {}; metadata.reset_identity = true; changed(); rerender(); });
  panel.append(reset);
  return panel;
}
