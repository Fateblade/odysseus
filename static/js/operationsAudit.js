const el = id => document.getElementById(id);
let entries = [];
let initialized = false;
let loading = false;
let sequence = 0;

function node(tag, text = '', className = '') {
  const value = document.createElement(tag);
  value.textContent = text;
  value.className = className;
  return value;
}

async function request(path, body = null) {
  const response = await fetch('/api/operations/' + path, { credentials: 'same-origin',
    ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
  if (response.status === 401) { window.location.assign('/login'); throw new Error('Sign in to view the journal.'); }
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Journal unavailable.');
  return result;
}

function render() {
  const query = el('operations-audit-filter').value.toLowerCase();
  const table = node('table');
  const heading = node('tr');
  for (const title of ['Time', 'Event', 'Caller / client', 'Model / backend', 'Request / job', '']) heading.append(node('th', title));
  const head = node('thead'); head.append(heading); table.append(head);
  const body = node('tbody');
  const filtered = entries.filter(entry => JSON.stringify(entry).toLowerCase().includes(query));
  for (const entry of filtered) {
    const row = node('tr');
    const caller = [entry.actor, entry.key_alias, entry.client_label, entry.client_ip, entry.user_agent].filter(Boolean).join(' · ') || 'Caller not established';
    const model = [entry.requested_model, entry.model, entry.backend, entry.endpoint].filter(Boolean).join(' · ');
    for (const text of [new Date(entry.time * 1000).toLocaleString(), `${entry.event} · ${entry.source || ''}`, caller, model,
      [entry.request_id, entry.job_id].filter(Boolean).join(' / ')]) row.append(node('td', text));
    const action = node('td');
    const inspect = node('button', 'Inspect', 'cookbook-btn'); inspect.type = 'button';
    inspect.addEventListener('click', async () => {
      const version = ++sequence;
      el('operations-audit-detail').textContent = 'Loading entry…';
      try {
        const result = await request('audit/detail', { id: entry.id });
        if (version === sequence) el('operations-audit-detail').textContent = result.event ? JSON.stringify(result.event, null, 2) : result.message;
      } catch (error) { if (version === sequence) el('operations-audit-detail').textContent = error.message; }
    });
    action.append(inspect); row.append(action); body.append(row);
  }
  table.append(body); el('operations-audit-entries').replaceChildren(table);
  el('operations-audit-count').textContent = `${filtered.length} of ${entries.length} recent entries. Filter by model, caller, event or correlation ID.`;
}

async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const result = await request('audit');
    entries = result.events || [];
    el('operations-audit-status').textContent = result.message || 'Journal loaded.';
    if (result.configured === false) el('operations-audit-status').textContent = result.message || 'Connect the Linux host helper.';
    render();
  } catch (error) { el('operations-audit-status').textContent = error.message; }
  finally { loading = false; }
}

export function openAudit() {
  if (!initialized) {
    initialized = true;
    el('operations-audit-refresh').addEventListener('click', refresh);
    el('operations-audit-filter').addEventListener('input', render);
  }
  refresh();
}
