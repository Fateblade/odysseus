const strip = document.getElementById('operations-resources');
const values = document.getElementById('operations-resource-values');
const status = document.getElementById('operations-resource-status');
const workspace = document.getElementById('operations-workspace');
let panel = 'overview';
let timer;
let controller;
let sampledAt = null;
let freshnessTimer;
let visibilityFrame;
const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const display = value => finite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 1 }) : '—';
const sum = (rows, key) => rows.length && rows.every(row => finite(row[key])) ? rows.reduce((total, row) => total + row[key], 0) : null;

function ratio(used, total) {
  const unit = finite(total) && total >= 1024 ** 4 ? 1024 ** 4 : 1024 ** 3;
  return `${display(finite(used) ? used / unit : null)} / ${display(finite(total) ? total / unit : null)} ${unit === 1024 ** 4 ? 'TiB' : 'GiB'}`;
}

function reading(label, value, description) {
  const group = document.createElement('div');
  const name = document.createElement('dt'); name.textContent = label;
  const number = document.createElement('dd'); number.textContent = value;
  group.title = description; group.append(name, number); return group;
}

export function updateResources(data) {
  const host = data.host || {}; const memory = host.memory || {}; const gpus = host.gpus || [];
  const ram = finite(memory.total_bytes) && finite(memory.available_bytes) ? memory.total_bytes - memory.available_bytes : null;
  const disks = data.storage?.totals || {};
  const readings = [reading('CPU', `${display(host.cpu?.utilization_percent)} / 100%`, 'Host CPU utilization across all logical cores.')];
  (gpus.length ? gpus : [{}]).forEach((gpu, index) => readings.push(reading(gpus.length > 1 ? `GPU ${index + 1}` : 'GPU',
    `${display(gpu.utilization_percent)} / 100%`, gpu.name || 'GPU busy percentage.')));
  readings.push(reading('RAM', ratio(ram, memory.total_bytes), 'Linux total minus estimated available memory. GPU memory overlaps RAM on unified-memory systems.'),
    reading('GPU memory', ratio(sum(gpus, 'vram_used_bytes'), sum(gpus, 'vram_total_bytes')), 'Driver-reported GPU memory across GPUs. GTT is not additional RAM.'),
    reading('Swap', ratio(memory.swap_used_bytes, memory.swap_total_bytes), 'Used / configured swap.'),
    reading('Disk', ratio(disks.used_bytes, disks.total_bytes), 'Used / capacity summed across local disk filesystems, counting each source once. Excludes RAM filesystems and reserved free-space availability.'));
  values.replaceChildren(...readings);
  sampledAt = finite(data.sampled_at) ? data.sampled_at : null;
  const incomplete = host.status !== 'ok' || data.storage?.status !== 'ok';
  status.textContent = incomplete ? 'Partial' : 'Live';
  strip.dataset.state = incomplete ? 'partial' : 'live';
  status.title = sampledAt ? `Sampled ${new Date(sampledAt * 1000).toLocaleTimeString()}. Refreshes every 5 seconds while this page is visible.` : 'Sample time unavailable';
  clearTimeout(freshnessTimer);
  if (sampledAt) freshnessTimer = setTimeout(markResourcesStale, Math.max(0, (sampledAt * 1000 + 15000) - Date.now()));
  else markResourcesStale();
  updateVisibility();
}

export function markResourcesStale() {
  status.textContent = sampledAt ? 'Stale' : 'Unavailable';
  strip.dataset.state = 'stale';
}

function updateVisibility() {
  if (panel !== 'overview') { strip.hidden = false; return; }
  const hardware = ['#operations-metrics', '.operations-memory-panel', '.operations-thermal-panel']
    .map(selector => document.querySelector(selector)).filter(item => item?.getClientRects().length);
  strip.hidden = !hardware.length || Math.max(...hardware.map(item => item.getBoundingClientRect().bottom)) > workspace.getBoundingClientRect().top;
}

function scheduleVisibility() {
  cancelAnimationFrame(visibilityFrame);
  visibilityFrame = requestAnimationFrame(updateVisibility);
}

async function refresh() {
  if (panel === 'overview' || document.hidden || controller) return;
  const pending = new AbortController(); controller = pending;
  try {
    const response = await fetch('/api/operations/resources', { credentials: 'same-origin', cache: 'no-store', signal: pending.signal });
    if (response.status === 401) { window.location.assign('/login'); return; }
    const data = await response.json();
    if (!pending.signal.aborted && panel !== 'overview') {
      if (!response.ok || !data.configured) markResourcesStale();
      else updateResources(data);
    }
  } catch (error) { if (error.name !== 'AbortError') markResourcesStale(); }
  finally {
    if (controller === pending) controller = null;
    if (!pending.signal.aborted && panel !== 'overview' && !document.hidden) timer = setTimeout(refresh, 5000);
  }
}

export function selectResourcePanel(name) {
  panel = name; clearTimeout(timer); controller?.abort(); controller = null;
  scheduleVisibility();
  if (name !== 'overview') refresh();
}

workspace.addEventListener('scroll', scheduleVisibility, { passive: true });
window.addEventListener('resize', scheduleVisibility);
new ResizeObserver(scheduleVisibility).observe(document.getElementById('operations-overview-content'));
document.addEventListener('visibilitychange', () => {
  clearTimeout(timer);
  if (document.hidden) { controller?.abort(); controller = null; }
  else { if (sampledAt && Date.now() - sampledAt * 1000 > 15000) markResourcesStale(); refresh(); }
});

updateResources({});
