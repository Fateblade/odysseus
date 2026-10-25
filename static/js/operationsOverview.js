import { notify, resetNotification } from './operationsNotifications.js?v=20261025notifications1';
import { updateResources, markResourcesStale } from './operationsResources.js?v=20261024resources1';
const el = id => document.getElementById(id);
let timer = null;
let active = false;
let loading = false;
let processState = null;
const gib = value => Number.isFinite(value) ? `${(value / 1024 ** 3).toFixed(1)} GiB` : 'Unavailable';
const memorySize = value => Number.isFinite(value) ? (value < 1024 ** 3 ? `${(value / 1024 ** 2).toFixed(1)} MiB` : gib(value)) : 'Unavailable';
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

const seconds = value => Number.isFinite(value) ? `${value.toFixed(1)} s` : 'Unavailable';
const timestamp = value => Number.isFinite(value) ? new Date(value * 1000).toLocaleTimeString() : 'Unavailable';

function processDetails(row) {
  const details = node('details');
  details.append(node('summary', row.name));
  details.append(node('p', `Executable: ${row.executable || 'Unavailable'}`, 'operations-muted'));
  details.append(node('p', `Parent PID: ${row.parent_pid ?? 'Unavailable'}`, 'operations-muted'));
  return details;
}

function processOwner(row) {
  const owner = node('div');
  if (row.container) {
    owner.append(node('strong', row.container.name || 'Unnamed container'));
    owner.append(node('p', row.container.image || 'Image unavailable', 'operations-muted'));
  } else if (row.container_id) {
    owner.append(node('strong', `Docker ${row.container_id.slice(0, 12)} � name unavailable`));
  } else {
    owner.append(node('span', row.unit || 'No service identified'));
  }
  if (row.container_id) {
    const details = node('details');
    details.append(node('summary', 'Container ID and scope'));
    details.append(node('p', row.container_id, 'operations-muted'));
    details.append(node('p', row.unit || 'Scope unavailable', 'operations-muted'));
    owner.append(details);
  }
  return owner;
}

function renderProcesses(processes) {
  processState = processes;
  const sort = el('operations-process-sort').value;
  const rows = [...(processes.items || [])].sort((a, b) => (b[sort] ?? -1) - (a[sort] ?? -1)).slice(0, 30);
  const states = { R: 'Running', S: 'Sleeping', D: 'I/O wait', T: 'Stopped', t: 'Tracing stop', I: 'Idle' };
  table('operations-processes', ['PID', 'Process', 'Container / service', 'State', 'CPU', 'RAM (RSS)', 'GPU engines', 'GPU memory', 'Threads'], rows.map(row => [
    row.pid, processDetails(row), processOwner(row), states[row.state] || row.state, percent(row.cpu_percent), memorySize(row.rss_bytes),
    Number.isFinite(row.gpu_engine_percent) ? percent(row.gpu_engine_percent) : 'Not reported',
    Number.isFinite(row.gpu_vram_bytes) ? gib(row.gpu_vram_bytes) : 'Not reported', row.threads,
  ]), processes.message || 'No process readings available.');
  el('operations-process-note').textContent = processes.status !== 'ok' ? processes.message || 'Process sampling unavailable.' :
    `${processes.visible_processes} readable processes; ${rows.length} shown. Sample: ${seconds(processes.sample_seconds)}. ${processes.skipped || 0} exited or unreadable. This is a selected list, not all processes. RSS totals do not reconcile with Linux RAM in use. ` +
    (processes.gpu_available ? 'GPU counters are reported only where the driver and process permissions allow access.' : 'Per-process GPU counters unavailable; check aggregate GPU utilization above.');
}

function renderActivity(data) {
  const strata = data.strata || {};
  const live = strata.activity || {};
  const gateway = data.gateway_activity || {};
  const content = el('operations-request-activity');
  content.replaceChildren();
  const current = node('article');
  current.append(node('h3', 'Strata'), facts([
    ['State', live.state || strata.state], ['Running', live.running], ['Queued', live.queued],
    ['Elapsed', seconds(live.elapsed_s)], ['Prompt tokens', live.prompt_tokens],
    ['Prompt read / total', live.prompt_total != null ? `${text(live.prompt_read)} / ${live.prompt_total}` : 'Unavailable'],
    ['Generated tokens', live.generated], ['Generation', percentless(live.tok_s)],
  ]));
  if (!strata.metrics_available) current.append(node('p', 'Live request metrics unavailable from Strata.', 'operations-muted'));
  const proxy = node('article');
  proxy.append(node('h3', 'LiteLLM'), node('p', gateway.status === 'ok' ? `${gateway.in_flight_requests} HTTP requests in flight` : gateway.message || 'In-flight count unavailable.', 'operations-muted'),
    node('p', 'The gateway count is a worker snapshot of HTTP activity, not an Ollama-only request count. Direct backend calls are outside this count.', 'operations-muted'));
  const ollama = node('article');
  ollama.append(node('h3', 'Ollama'), node('p', 'Its API does not expose an active-request list. Loaded models may be idle. Use the Ollama / runner processes below to identify resource activity.', 'operations-muted'));
  content.append(current, proxy, ollama);
  if (data.dwarfstar?.configured) {
    const dwarfstar = node('article');
    dwarfstar.append(node('h3', 'DwarfStar'), node('p', 'Readiness and model context are available. This API does not report active requests or GPU allocations. Use host process readings and TPS jobs for activity.', 'operations-muted'));
    content.append(dwarfstar);
  }
  table('operations-strata-requests', ['Started', 'ID', 'Model', 'Endpoint', 'State', 'Elapsed', 'Queue', 'Load', 'First token'], (strata.requests || []).map(row => [
    timestamp(row.started_at), row.id, row.model, row.path, row.state,
    seconds(row.wallclock_s), seconds(row.queue_s), seconds(row.load_s), seconds(row.first_token_s),
  ]), strata.requests_available ? 'No retained Strata requests.' : 'Request list unavailable. This Strata version may not expose it, or its API monitor is disabled.');
  el('operations-request-note').textContent = 'Up to 30 live or retained requests reported by Strata. Completed entries are history. Prompts and response contents are omitted.';
  table('operations-strata-history', ['Started', 'Finish', 'Duration', 'Prompt tokens', 'Output tokens', 'Decode'], (strata.recent_requests || []).map(row => [
    timestamp(row.time), row.finish, seconds(row.duration_s), row.prompt_tokens, row.output_tokens, percentless(row.decode_tok_s),
  ]), strata.metrics_available ? 'No completed generations reported.' : 'Generation history unavailable.');
}

function render(data) {
  renderProcesses(data.processes || {});
  renderActivity(data);
  const storage = data.storage || {};
  table('operations-storage', ['Mount', 'Device', 'Filesystem', 'Capacity', 'Used', 'Available', 'Usage'], (storage.items || []).map(disk => [
    disk.mount, disk.source, disk.filesystem, gib(disk.total_bytes), gib(disk.used_bytes), gib(disk.available_bytes),
    badge(percent(disk.used_percent), disk.used_percent >= 90 ? 'unavailable' : 'ok'),
  ]), storage.message || 'Disk space unavailable.');
  el('operations-storage-note').textContent = storage.message || '';
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
  const breakdownOpen = allocations.querySelector('.operations-memory-breakdown')?.open || false;
  allocations.replaceChildren(memoryRow('Linux RAM in use', used, memory.total_bytes, `${gib(memory.available_bytes)} available. In use = total minus estimated available; this is not a sum of process RSS.`, 'ram'));
  for (const gpu of host.gpus || []) {
    allocations.append(memoryRow('GPU memory', gpu.vram_used_bytes, gpu.vram_total_bytes,
      `GTT: ${gib(gpu.gtt_used_bytes)} allocated of ${gib(gpu.gtt_total_bytes)} shared-access limit`, 'gpu'));
  }
  const breakdown = node('details', '', 'operations-memory-breakdown');
  breakdown.open = breakdownOpen;
  breakdown.append(node('summary', 'Linux memory breakdown'));
  const memoryFacts = node('dl');
  for (const [label, key] of [
    ['Free', 'free_bytes'], ['Available (estimate)', 'available_bytes'], ['Anonymous pages', 'anonymous_bytes'],
    ['Page cache (includes shared memory)', 'cached_bytes'], ['Buffers', 'buffers_bytes'],
    ['Shared memory / tmpfs', 'shared_bytes'], ['Kernel slab', 'slab_bytes'],
    ['Reclaimable slab (part of slab)', 'slab_reclaimable_bytes'], ['Kernel stacks', 'kernel_stack_bytes'],
    ['Page tables', 'page_tables_bytes'],
  ]) memoryFacts.append(node('dt', label), node('dd', memorySize(memory[key])));
  breakdown.append(memoryFacts, node('p', 'These are overlapping Linux counters, not slices to add together. Available includes memory the kernel estimates it can reclaim. Process RSS can count shared pages more than once; kernel allocations and unmapped cache are outside process RSS.', 'operations-muted'));
  allocations.append(breakdown);
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
  const dwarfstar = data.dwarfstar || {};
  const dwarfstarCard = service('DwarfStar', dwarfstar.status === 'ok' ? 'Ready' : dwarfstar.status === 'stopped' ? 'Stopped' : dwarfstar.configured ? 'Unavailable' : 'Not configured',
    dwarfstar.status, dwarfstar.model || dwarfstar.message || 'Optional DeepSeek V4 Flash backend');
  if (dwarfstar.status === 'ok') dwarfstarCard.append(facts([['Context tokens', dwarfstar.max_context]]));
  dwarfstarCard.append(node('p', 'The server holds one startup-selected model. Unload competing large models before starting it.', 'operations-muted'), action('Manage DwarfStar', 'management'));
  services.append(dwarfstarCard);
  if (data.maya?.configured) {
    const maya = data.maya;
    const card = service('Maya', maya.status === 'ok' ? 'Ready' : maya.status === 'unloaded' ? 'Unloaded' : 'Unavailable', maya.status,
      maya.model || maya.message);
    card.append(facts([['Context tokens', maya.max_context], ['Version', maya.version]]), action('Manage Maya', 'management'));
    services.append(card);
  }
  if (data.maya_glm?.configured) {
    const glm = data.maya_glm;
    const card = service('Maya GLM', glm.status === 'ok' ? 'Ready' : glm.status === 'unloaded' ? 'Unloaded' : 'Unavailable', glm.status, glm.model || glm.message);
    card.append(facts([['Context tokens', glm.max_context], ['Version', glm.version]]), action('Manage GLM', 'management'));
    services.append(card);
  }
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

async function refresh(manual = false) {
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
      markResourcesStale();
      resetNotification('overview:request');
      notify(data.message || 'Host not connected.', { severity: 'warning', key: 'overview:availability' });
      status.textContent = data.message;
      status.classList.remove('operations-error');
      el('operations-overview-content').hidden = true;
      el('operations-connection').textContent = 'Host not connected';
      el('operations-connection').dataset.state = 'offline';
      return;
    }
    render(data);
    updateResources(data);
    el('operations-overview-content').hidden = false;
    const unavailable = ['host', 'containers', 'strata', 'ollama', 'litellm', 'processes', 'storage'].filter(key => data[key]?.status !== 'ok');
    if (data.dwarfstar?.configured && !['ok', 'stopped'].includes(data.dwarfstar.status)) unavailable.push('dwarfstar');
    const time = new Date(data.sampled_at * 1000).toLocaleTimeString();
    status.textContent = `${data.demo ? 'Preview data · ' : ''}Updated ${time}${unavailable.length ? ` · unavailable or incomplete: ${unavailable.join(', ')}` : ''}`;
    status.classList.toggle('operations-error', unavailable.length > 0);
    resetNotification('overview:request');
    if (manual) resetNotification('overview:availability');
    if (unavailable.length) notify(`Host readings unavailable or incomplete: ${unavailable.join(', ')}`, { severity: 'warning', key: 'overview:availability' });
    else {
      resetNotification('overview:availability');
      if (manual) notify('Host readings refreshed.');
    }
    const connection = el('operations-connection');
    connection.textContent = data.demo ? 'Preview data' : unavailable.length ? 'Partial readings' : 'Host connected';
    connection.dataset.state = data.demo ? 'demo' : unavailable.length ? 'partial' : 'online';
  } catch (error) {
    markResourcesStale();
    status.textContent = `${error.message} Previous readings, if shown, are stale.`;
    status.classList.add('operations-error');
    notify(status.textContent, { severity: 'error', key: 'overview:request' });
    el('operations-connection').textContent = 'Readings stale';
    el('operations-connection').dataset.state = 'offline';
  } finally {
    loading = false;
    el('operations-refresh-overview').disabled = false;
    if (active && !document.hidden) timer = setTimeout(refresh, 5000);
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

el('operations-refresh-overview').addEventListener('click', () => refresh(true));
el('operations-open-containers').addEventListener('click', () => el('operations-management-tab').click());
el('operations-open-models').addEventListener('click', () => el('operations-management-tab').click());
el('operations-process-sort').addEventListener('change', () => processState && renderProcesses(processState));
document.addEventListener('visibilitychange', () => document.hidden ? clearTimeout(timer) : active && refresh());
