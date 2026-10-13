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
    row.forEach(value => tr.append(node('td', String(text(value)))));
    body.append(tr);
  });
  result.append(head, body);
  container.append(result);
}

function render(data) {
  const host = data.host || {};
  const memory = host.memory || {};
  const cpu = host.cpu || {};
  const used = Number.isFinite(memory.total_bytes) && Number.isFinite(memory.available_bytes)
    ? memory.total_bytes - memory.available_bytes : null;
  const metrics = el('operations-metrics');
  metrics.replaceChildren(
    metric('CPU', percent(cpu.utilization_percent), cpu.name || host.message || '', cpu.utilization_percent),
    metric('Linux RAM available', gib(memory.available_bytes), `${gib(memory.total_bytes)} visible · ${gib(used)} used`,
      memory.total_bytes > 0 && Number.isFinite(used) ? 100 * used / memory.total_bytes : null, 'Linux RAM used'),
    metric('Installed memory', gib(host.physical_memory_bytes), host.physical_memory_bytes ? 'Configured physical capacity' : 'Physical capacity has not been configured'),
    metric('Swap used', gib(memory.swap_used_bytes), `${gib(memory.swap_total_bytes)} configured`),
  );
  for (const gpu of host.gpus || []) {
    metrics.append(
      metric(`${gpu.name} utilization`, percent(gpu.utilization_percent), gpu.id, gpu.utilization_percent),
      metric('GPU allocation', gib(gpu.vram_used_bytes), `${gib(gpu.vram_total_bytes)} driver-reported capacity`,
        gpu.vram_total_bytes > 0 && Number.isFinite(gpu.vram_used_bytes) ? 100 * gpu.vram_used_bytes / gpu.vram_total_bytes : null),
      metric('GTT allocation', gib(gpu.gtt_used_bytes), `${gib(gpu.gtt_total_bytes)} shared-access limit`),
    );
  }
  el('operations-host-name').textContent = host.hostname ? `${host.hostname} · ${cpu.threads} threads · ${host.kernel}` : host.message || 'Host unavailable';
  const services = el('operations-services');
  services.replaceChildren();
  const strata = data.strata || {};
  const strataCard = metric('Strata', strata.loaded === true ? 'Model loaded' : strata.loaded === false ? 'Model unloaded' : strata.status,
    strata.model || strata.message || '');
  strataCard.append(node('p', `Engine ${text(strata.version)} · context ${text(strata.max_context)}`, 'operations-muted'));
  strataCard.append(node('p', `${percentless(strata.generation_tps)} generation · ${percentless(strata.prefill_tps)} prefill`, 'operations-muted'));
  strataCard.append(node('p', `State: ${text(strata.state)} · queued: ${text(strata.queued)}`, 'operations-muted'));
  services.append(strataCard);
  const ollama = data.ollama || {};
  services.append(metric('Ollama', ollama.status === 'ok' ? 'Available' : text(ollama.status),
    ollama.loaded_available ? `${ollama.loaded.length} loaded · ${ollama.installed_available ? ollama.installed.length : 'Unknown'} installed` : ollama.message || 'Inventory unavailable'));
  const lite = data.litellm || {};
  const gateway = metric('LiteLLM', lite.status === 'ok' ? 'Available' : text(lite.status), lite.message || 'Inference gateway');
  const edit = node('button', 'Edit models and routing', 'cookbook-btn');
  edit.type = 'button';
  edit.addEventListener('click', () => el('operations-litellm-tab').click());
  gateway.append(edit);
  services.append(gateway);
  table('operations-thermals', ['Sensor', 'Reading'], (host.sensors || []).map(sensor => [
    `${sensor.chip} / ${sensor.label}`, `${sensor.celsius.toFixed(1)} °C`,
  ]), 'No host temperature sensors available.');
  table('operations-containers', ['Container', 'State', 'Image', 'Status'], (data.containers?.items || []).map(container => [
    container.name, container.state, container.image, container.status,
  ]), data.containers?.message || 'No containers found.');
  const loadedNames = new Set((ollama.loaded || []).map(model => model.name));
  table('operations-ollama-models', ['Model', 'Disk size', 'State'], (ollama.installed || []).map(model => [
    model.name, gib(model.size_bytes), ollama.loaded_available ? loadedNames.has(model.name) ? 'Loaded' : 'On disk' : 'Unknown',
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
      return;
    }
    render(data);
    el('operations-overview-content').hidden = false;
    const unavailable = ['host', 'containers', 'strata', 'ollama', 'litellm'].filter(key => data[key]?.status !== 'ok');
    const time = new Date(data.sampled_at * 1000).toLocaleTimeString();
    status.textContent = `${data.demo ? 'Preview data · ' : ''}Updated ${time}${unavailable.length ? ` · unavailable or incomplete: ${unavailable.join(', ')}` : ''}`;
    status.classList.toggle('operations-error', unavailable.length > 0);
  } catch (error) {
    status.textContent = `${error.message} Previous readings, if shown, are stale.`;
    status.classList.add('operations-error');
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
document.addEventListener('visibilitychange', () => document.hidden ? clearTimeout(timer) : active && refresh());
