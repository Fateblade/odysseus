const el = id => document.getElementById(id);
let timer = null;
let active = false;
let loading = false;
const gib = value => Number.isFinite(value) ? `${(value / 1024 ** 3).toFixed(1)} GiB` : 'Unavailable';
const percent = value => Number.isFinite(value) ? `${value.toFixed(1)}%` : 'Unavailable';
const text = value => value ?? 'Unavailable';

function node(tag, content = '', className = '') {
  const result = document.createElement(tag);
  result.textContent = content;
  result.className = className;
  return result;
}

function metric(label, value, detail, usage = null, usageLabel = label) {
  const card = node('article', '', 'operations-card operations-metric');
  card.append(node('h3', label), node('p', value, 'operations-metric-value'), node('p', detail, 'operations-muted'));
  if (Number.isFinite(usage)) {
    const meter = document.createElement('progress');
    meter.max = 100;
    meter.value = Math.max(0, Math.min(100, usage));
    meter.setAttribute('aria-label', usageLabel);
    card.append(meter);
  }
  return card;
}

function table(target, headings, rows, emptyText) {
  const container = el(target);
  container.replaceChildren();
  if (!rows.length) {
    container.append(node('p', emptyText, 'operations-muted'));
    return;
  }
  const result = document.createElement('table');
  const head = document.createElement('thead');
  const header = document.createElement('tr');
  headings.forEach(label => header.append(node('th', label)));
  head.append(header);
  const body = document.createElement('tbody');
  rows.forEach(row => {
    const tr = document.createElement('tr');
    row.forEach(value => {
      const cell = node('td');
      if (value instanceof Node) cell.append(value);
      else cell.textContent = String(text(value));
      tr.append(cell);
    });
    body.append(tr);
  });
  result.append(head, body);
  container.append(result);
}

function badge(label, state) {
  const item = node('span', label, 'operations-badge');
  item.dataset.state = state;
  return item;
}

function memoryRow(label, used, total, detail, kind) {
  const row = node('div', '', 'operations-memory-row');
  row.dataset.kind = kind;
  const heading = node('div', '', 'operations-memory-heading');
  heading.append(node('h3', label), node('span', `${gib(used)} / ${gib(total)}`));
  row.append(heading);
  if (Number.isFinite(used) && Number.isFinite(total) && total > 0) {
    const meter = document.createElement('progress');
    meter.className = 'operations-memory-meter';
    meter.max = total;
    meter.value = Math.min(total, Math.max(0, used));
    meter.setAttribute('aria-label', `${label} used`);
    row.append(meter);
  }
  row.append(node('p', detail, 'operations-muted'));
  return row;
}

function service(name, label, state, description) {
  const card = node('article', '', 'operations-metric');
  const heading = node('div', '', 'operations-service-heading');
  heading.append(node('h3', name), badge(label, state));
  card.append(heading, node('p', description, 'operations-metric-value'));
  return card;
}

function facts(values) {
  const result = node('div', '', 'operations-service-facts');
  for (const [label, value] of values) {
    const entry = node('p', String(text(value)));
    entry.append(node('span', label));
    result.append(entry);
  }
  return result;
}

function action(label, target) {
  const result = node('button', label, 'cookbook-btn');
  result.type = 'button';
  result.addEventListener('click', () => el(`operations-${target}-tab`).click());
  return result;
}

function render(data) {
  const host = data.host || {};
  const memory = host.memory || {};
  const cpu = host.cpu || {};
  const used = Number.isFinite(memory.total_bytes) && Number.isFinite(memory.available_bytes)
    ? memory.total_bytes - memory.available_bytes : null;
  const metrics = el('operations-metrics');
  metrics.replaceChildren(
    metric('CPU load', percent(cpu.utilization_percent), cpu.threads ? `${cpu.threads} threads` : host.message || 'Thread count unavailable', cpu.utilization_percent),
  );
  for (const gpu of host.gpus || []) {
    metrics.append(metric('GPU utilization', percent(gpu.utilization_percent), gpu.name || gpu.id, gpu.utilization_percent));
  }
  metrics.append(
    metric('Installed memory', gib(host.physical_memory_bytes), host.physical_memory_bytes ? 'Configured physical capacity' : 'Set installed capacity in host configuration'),
    metric('Swap used', gib(memory.swap_used_bytes), `Of ${gib(memory.swap_total_bytes)} configured`),
  );
  const allocations = el('operations-memory');
  allocations.replaceChildren(memoryRow('Linux RAM', used, memory.total_bytes, `${gib(memory.available_bytes)} available to applications`, 'ram'));
  for (const gpu of host.gpus || []) {
    allocations.append(memoryRow('GPU memory', gpu.vram_used_bytes, gpu.vram_total_bytes,
      `GTT: ${gib(gpu.gtt_used_bytes)} allocated of ${gib(gpu.gtt_total_bytes)} shared-access limit`, 'gpu'));
  }
  el('operations-host-name').textContent = host.hostname ? `${cpu.name || host.hostname} / ${host.kernel || 'Kernel unavailable'}` : host.message || 'Host unavailable';
  el('operations-sidebar-host').textContent = host.hostname || 'Host unavailable';
  const services = el('operations-services');
  services.replaceChildren();
  const strata = data.strata || {};
  const strataCard = service('Strata', strata.loaded === true ? 'Loaded' : strata.loaded === false ? 'Unloaded' : text(strata.status),
    strata.loaded === false ? 'unloaded' : strata.status, strata.model || strata.message || 'Model unavailable');
  strataCard.append(facts([['Context tokens', strata.max_context], ['Generation', percentless(strata.generation_tps)], ['Prefill', percentless(strata.prefill_tps)]]));
  strataCard.append(node('p', `Engine ${text(strata.version)}. ${text(strata.state)}; ${text(strata.queued)} queued.`, 'operations-muted'), action('Manage Strata', 'management'));
  services.append(strataCard);
  const ollama = data.ollama || {};
  const ollamaCard = service('Ollama', ollama.status === 'ok' ? 'Ready' : text(ollama.status), ollama.status,
    ollama.loaded_available ? `${ollama.loaded.length} models loaded` : 'Inventory unavailable');
  ollamaCard.append(node('p', ollama.installed_available ? `${ollama.installed.length} installed models` : ollama.message || 'Installed models unavailable', 'operations-muted'), action('Manage models', 'management'));
  services.append(ollamaCard);
  const lite = data.litellm || {};
  const gateway = service('LiteLLM', lite.status === 'ok' ? 'Ready' : text(lite.status), lite.status, 'Inference gateway');
  gateway.append(node('p', lite.message || 'Model aliases, routing and fallbacks.', 'operations-muted'), action('Edit models and routing', 'litellm'));
  services.append(gateway);
  table('operations-thermals', ['Sensor', 'Reading'], (host.sensors || []).map(sensor => [
    `${sensor.chip} / ${sensor.label}`, Number.isFinite(sensor.celsius) ? `${sensor.celsius.toFixed(1)} °C` : 'Unavailable',
  ]), 'No host temperature sensors available.');
  table('operations-containers', ['Container', 'State', 'Image', 'Status'], (data.containers?.items || []).map(container => [
    container.name, badge(text(container.state), container.state), container.image, container.status,
  ]), data.containers?.message || 'No containers found.');
  const loadedNames = new Set((ollama.loaded || []).map(model => model.name));
  table('operations-ollama-models', ['Model', 'Disk size', 'State'], (ollama.installed || []).map(model => [
    model.name, gib(model.size_bytes), badge(ollama.loaded_available ? loadedNames.has(model.name) ? 'Loaded' : 'On disk' : 'Unknown', loadedNames.has(model.name) && ollama.loaded_available ? 'Loaded' : 'on-disk'),
  ]), ollama.message || 'No Ollama models installed.');
  el('operations-loaded-models').textContent = ollama.loaded_available
    ? ollama.loaded.length ? ollama.loaded.map(model => `${model.name} · ${gib(model.size_bytes)} runtime · context ${text(model.context_length)}`).join('\n') : 'No Ollama models are loaded.'
    : 'Loaded-model inventory unavailable.';
}

function percentless(value) {
  return Number.isFinite(value) ? `${value.toFixed(1)} tok/s` : 'Unavailable';
}

async function refresh() {
  if (loading || !active || document.hidden) return;
  clearTimeout(timer);
  loading = true;
  const status = el('operations-overview-status');
  el('operations-refresh-overview').disabled = true;
  try {
    const response = await fetch('/api/operations/overview', { credentials: 'same-origin' });
    if (response.status === 401) {
      window.location.assign('/login');
      return;
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Host overview could not be read.');
    if (!data.configured) {
      status.textContent = data.message;
      status.classList.remove('operations-error');
      el('operations-overview-content').hidden = true;
      el('operations-connection').textContent = 'Host not connected';
      el('operations-connection').dataset.state = 'offline';
      return;
    }
    render(data);
    el('operations-overview-content').hidden = false;
    const unavailable = ['host', 'containers', 'strata', 'ollama', 'litellm'].filter(key => data[key]?.status !== 'ok');
    const time = new Date(data.sampled_at * 1000).toLocaleTimeString();
    status.textContent = `${data.demo ? 'Preview data · ' : ''}Updated ${time}${unavailable.length ? ` · unavailable or incomplete: ${unavailable.join(', ')}` : ''}`;
    status.classList.toggle('operations-error', unavailable.length > 0);
    const connection = el('operations-connection');
    connection.textContent = data.demo ? 'Preview data' : unavailable.length ? 'Partial readings' : 'Host connected';
    connection.dataset.state = data.demo ? 'demo' : unavailable.length ? 'partial' : 'online';
  } catch (error) {
    status.textContent = `${error.message} Previous readings, if shown, are stale.`;
    status.classList.add('operations-error');
    el('operations-connection').textContent = 'Readings stale';
    el('operations-connection').dataset.state = 'offline';
  } finally {
    loading = false;
    el('operations-refresh-overview').disabled = false;
    if (active && !document.hidden) timer = setTimeout(refresh, 10000);
  }
}

export function openOverview() {
  active = true;
  refresh();
}

export function closeOverview() {
  active = false;
  clearTimeout(timer);
}

el('operations-refresh-overview').addEventListener('click', refresh);
el('operations-open-containers').addEventListener('click', () => el('operations-management-tab').click());
el('operations-open-models').addEventListener('click', () => el('operations-management-tab').click());
document.addEventListener('visibilitychange', () => document.hidden ? clearTimeout(timer) : active && refresh());
