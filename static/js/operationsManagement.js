const el = id => document.getElementById(id);
let active = null;
let timer = null;
let busy = false;
let polling = false;
let selectedJob = null;
let initialized = false;
let pendingPanel = null;
let strataState = null;
let strataReview = null;

function node(tag, text = '', className = '') {
  const item = document.createElement(tag);
  item.textContent = text;
  item.className = className;
  return item;
}

function button(text, action) {
  const item = node('button', text, 'cookbook-btn');
  item.type = 'button';
  item.addEventListener('click', action);
  return item;
}

function field(parent, id, title, value = '', options = null, type = 'text') {
  const label = node('label', title, 'operations-field');
  const input = node(options ? 'select' : type === 'textarea' ? 'textarea' : 'input', '', 'settings-input');
  input.id = id;
  if (options) options.forEach(option => {
    const entry = node('option', option);
    entry.value = option;
    input.append(entry);
  });
  else if (type !== 'textarea') input.type = type;
  input.value = value;
  label.append(input);
  parent.append(label);
  return input;
}

function section(parent, title, description, className = '') {
  const item = node('section', '', `operations-card ${className}`);
  item.append(node('h2', title), node('p', description, 'operations-muted'));
  parent.append(item);
  return item;
}

function status(message, error = false) {
  const item = el(`operations-${active}-message`);
  if (!item) return;
  item.textContent = message;
  item.classList.toggle('operations-error', error);
}

async function request(path, body = null) {
  const response = await fetch('/api/operations/' + path, {
    credentials: 'same-origin',
    ...(body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  });
  if (response.status === 401) window.location.assign('/login');
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Host operation failed.');
  if (result.configured === false) throw new Error(result.message);
  return result;
}

function confirmOperation(text) {
  const dialog = el('operations-confirm');
  el('operations-confirm-text').textContent = text;
  return new Promise(resolve => {
    const cancel = () => dialog.close('cancel');
    const apply = () => dialog.close('apply');
    el('operations-confirm-cancel').addEventListener('click', cancel);
    el('operations-confirm-apply').addEventListener('click', apply);
    dialog.addEventListener('close', () => {
      el('operations-confirm-cancel').removeEventListener('click', cancel);
      el('operations-confirm-apply').removeEventListener('click', apply);
      resolve(dialog.returnValue === 'apply');
    }, { once: true });
    dialog.returnValue = 'cancel';
    dialog.showModal();
  });
}

async function task(action) {
  if (busy) return;
  busy = true;
  status('Working…');
  try { await action(); }
  catch (error) { status(error.message, true); }
  finally {
    busy = false;
    const pending = pendingPanel;
    pendingPanel = null;
    if (pending && pending === active) task(pending === 'management' ? refreshManagement : refreshJobs);
  }
}

async function manage(payload) {
  if (!['logs', 'status', 'show'].includes(payload.operation)) {
    const target = payload.name || 'Strata model';
    const detail = payload.operation === 'delete' ? 'This deletes the local model files.' : 'This may interrupt requests or change shared GPU memory use.';
    if (!await confirmOperation(`${payload.operation} ${target}${payload.destination ? ` → ${payload.destination}` : ''}? ${detail}`)) {
      status('Operation cancelled.');
      return;
    }
  }
  const result = await request('manage', payload);
  status(result.job ? 'Host job started. Follow its progress in Scripts & TPS tests.' : 'Operation completed.');
  if (result.log !== undefined) el('operations-management-output').textContent = result.log || 'No log output.';
  if (result.details) el('operations-management-output').textContent = JSON.stringify(result.details, null, 2);
  if (result.job) {
    selectedJob = result.job.id;
    el('operations-jobs-tab').click();
  }
}

function controls(parent, title, target, options, operations) {
  const card = section(parent, title, target === 'docker' ? 'Start, stop or inspect the containers allowed by your host configuration. The dashboard container is protected.' : 'Control the inference services configured on your host.', `operations-control-${target}`);
  const input = field(card, `operations-${target}-name`, 'Target', options[0] || '', options);
  const row = node('div', '', 'cookbook-actions');
  operations.forEach(operation => {
    const control = button(operation, () => task(() => manage({ target, name: input.value, operation })));
    control.dataset.hostControl = 'true';
    row.append(control);
  });
  card.append(row);
}

function initialize() {
  if (initialized) return;
  initialized = true;
  const management = el('operations-management-panel');
  management.append(node('p', '', 'operations-status'));
  management.firstChild.id = 'operations-management-message';
  management.firstChild.setAttribute('role', 'status');
  const content = node('div');
  content.id = 'operations-management-content';
  content.hidden = true;
  management.append(content);
  controls(content, 'Docker containers', 'docker', [], ['start', 'stop', 'restart', 'logs']);
  controls(content, 'Inference servers', 'service', [], ['status', 'start', 'stop', 'restart', 'logs']);
  const strata = section(content, 'Strata residency', 'Load the configured model into memory, or release it while keeping the server available. Active requests are not forcibly stopped.', 'operations-strata-residency');
  const strataActions = node('div', '', 'cookbook-actions');
  ['load', 'unload'].forEach(operation => strataActions.append(button(operation, () => task(() => manage({ target: 'strata', operation })))));
  strata.append(strataActions);
  const configuration = section(content, 'Strata configuration', 'Choose quantization and context. Applying prepares the model, restarts Strata and verifies the result. New quantizations may need a download.', 'operations-strata-configuration');
  const contextStatus = node('p', 'Loading Strata configuration…', 'operations-status');
  contextStatus.id = 'operations-strata-config-status';
  configuration.append(contextStatus);
  const configFields = node('div');
  configFields.id = 'operations-strata-config-fields';
  configFields.hidden = true;
  configuration.append(configFields);
  const quantization = field(configFields, 'operations-strata-quantization', 'Model quantization', '', []);
  const availability = node('p', '', 'operations-muted');
  availability.id = 'operations-strata-model-availability';
  configFields.append(availability);
  const context = field(configFields, 'operations-strata-context', 'Context size (tokens)', 131072, null, 'number');
  context.min = '1024'; context.step = '1024';
  const command = node('pre', '', 'operations-diff');
  command.id = 'operations-strata-command';
  command.hidden = true;
  const configActions = node('div', '', 'cookbook-actions');
  configFields.append(configActions);
  configActions.append(button('Reload Strata configuration', () => task(loadStrata)), button('Review setup command', () => task(async () => {
    const requestedContext = Number(context.value);
    const requestedModel = quantization.value;
    const reviewed = await request('strata/preview', { revision: strataState.revision, context: requestedContext, model: requestedModel });
    if (Number(context.value) !== requestedContext || quantization.value !== requestedModel) throw new Error('Model or context changed during review. Review the new values.');
    strataReview = reviewed;
    command.textContent = `Working directory: ${strataReview.directory}\n${strataReview.display_command}`;
    command.hidden = false;
    const needsLauncherLink = strataReview.model !== strataState.model && !strataState.switch_available;
    el('operations-strata-apply').disabled = !strataState.restart_available || needsLauncherLink;
    status(needsLauncherLink ? 'Configure the stable service launcher link before switching quantizations.' : 'Review the setup command. Applying stops Strata, runs setup, then verifies the model and context.');
  })));
  configFields.append(command);
  const apply = button('Apply setup and restart Strata', () => task(async () => {
    if (!strataReview) throw new Error('Review the setup command first.');
    if (!await confirmOperation(`Configure Strata ${strataReview.model} with ${strataReview.context} tokens? The managed server will stop while setup runs. Configuration and launcher backups are saved; setup downloads/builds cannot be rolled back.`)) return;
    const result = await request('strata/apply', { revision: strataReview.revision, context: strataReview.context, model: strataReview.model, review_digest: strataReview.review_digest });
    strataReview = null; strataState = null; apply.disabled = true;
    selectedJob = result.job.id;
    el('operations-jobs-tab').click();
  }));
  apply.id = 'operations-strata-apply';
  apply.disabled = true;
  configFields.append(apply);
  const invalidateSetup = () => {
    strataReview = null; command.hidden = true; apply.disabled = true;
    const variant = strataState?.models?.find(model => model.name === quantization.value);
    availability.textContent = variant?.installed ? 'Configuration exists on the host; setup verifies its artifacts.' : 'Not configured yet. Setup may download and prepare this quantization.';
  };
  context.addEventListener('input', invalidateSetup);
  quantization.addEventListener('change', invalidateSetup);
  const models = section(content, 'Ollama models', 'Download, inspect or copy a model. Loading keeps it in memory for 10 minutes; downloads run as jobs.', 'operations-ollama-control');
  field(models, 'operations-model-name', 'Model name', '');
  const suggestions = node('datalist');
  suggestions.id = 'operations-model-suggestions';
  models.append(suggestions);
  el('operations-model-name').setAttribute('list', suggestions.id);
  field(models, 'operations-model-destination', 'Destination name (for copy)', '');
  const modelActions = node('div', '', 'cookbook-actions');
  ['show', 'pull', 'copy', 'load', 'unload', 'delete'].forEach(operation => modelActions.append(button(operation, () => task(() => manage({
    target: 'ollama', operation, name: el('operations-model-name').value,
    destination: el('operations-model-destination').value,
  })))));
  models.append(modelActions);
  const outputSection = section(content, 'Logs & model details', 'Select logs, service status or model details to inspect the response.', 'operations-command-output');
  const output = node('pre', 'Select logs, status or model details.', 'operations-diff');
  output.id = 'operations-management-output';
  outputSection.append(output);

  const jobs = el('operations-jobs-panel');
  const message = node('p', '', 'operations-status');
  message.id = 'operations-jobs-message';
  message.setAttribute('role', 'status');
  jobs.append(message);
  const jobsContent = node('div');
  jobsContent.id = 'operations-jobs-content';
  jobsContent.hidden = true;
  jobs.append(jobsContent);
  const benchmark = section(jobsContent, 'TPS benchmark', 'Compare repeated generations with an optional warmup. Decode TPS uses backend timings; end-to-end TPS includes loading, queueing and prefill. Caches are not cleared.', 'operations-benchmark');
  const grid = node('div', '', 'operations-grid');
  benchmark.append(grid);
  field(grid, 'operations-benchmark-backend', 'Backend', 'ollama', ['ollama', 'strata']);
  field(grid, 'operations-benchmark-model', 'Model name');
  el('operations-benchmark-model').setAttribute('list', suggestions.id);
  const runs = field(grid, 'operations-benchmark-runs', 'Measured runs', 3, null, 'number');
  runs.min = '1'; runs.max = '20';
  const tokens = field(grid, 'operations-benchmark-tokens', 'Maximum output tokens', 256, null, 'number');
  tokens.min = '1'; tokens.max = '8192';
  field(grid, 'operations-benchmark-warmup', 'Warmup', 'yes', ['yes', 'no']);
  field(grid, 'operations-benchmark-prompt', 'Prompt', 'Explain how shared memory works in an integrated GPU system.', null, 'textarea');
  benchmark.append(button('Run benchmark', () => task(async () => {
    const body = { kind: 'benchmark', backend: el('operations-benchmark-backend').value,
      model: el('operations-benchmark-model').value, prompt: el('operations-benchmark-prompt').value,
      runs: Number(runs.value), max_tokens: Number(tokens.value), warmup: el('operations-benchmark-warmup').value === 'yes' };
    if (!await confirmOperation(`Run ${body.runs} measured generations on ${body.backend}/${body.model}${body.warmup ? ' plus one warmup' : ''}? This shares the running inference service with other requests.`)) return;
    const result = await request('jobs/start', body);
    selectedJob = result.job.id;
    await refreshJobs();
    status('Benchmark started.');
  })));
  const scripts = section(jobsContent, 'Python scripts', 'Save a definition for a script in your host scripts folder, then run it with its saved arguments and timeout.', 'operations-scripts');
  scripts.id = 'operations-scripts-card';
  const scriptGrid = node('div', '', 'operations-grid');
  scripts.append(scriptGrid);
  field(scriptGrid, 'operations-script-name', 'Saved name');
  field(scriptGrid, 'operations-script-path', 'Relative path in scripts folder');
  field(scriptGrid, 'operations-script-timeout', 'Timeout (seconds)', 600, null, 'number');
  field(scriptGrid, 'operations-script-args', 'Arguments (JSON array)', '[]');
  scripts.append(button('Save script', () => task(async () => {
    let argumentsValue;
    try { argumentsValue = JSON.parse(el('operations-script-args').value); }
    catch { throw new Error('Arguments must be a JSON array of strings.'); }
    await request('scripts/save', { name: el('operations-script-name').value, path: el('operations-script-path').value,
      timeout: Number(el('operations-script-timeout').value), arguments: argumentsValue });
    await refreshJobs();
    status('Script definition saved.');
  })));
  const scriptList = node('div');
  scriptList.id = 'operations-script-list';
  scripts.append(scriptList);
  const history = section(jobsContent, 'Jobs & benchmark comparison', 'One host job runs at a time. History survives dashboard restarts. Benchmark rates below are medians.', 'operations-history');
  history.append(button('Refresh jobs', () => task(refreshJobs)));
  const list = node('div', '', 'operations-table-wrap');
  list.id = 'operations-job-list';
  history.append(list);
  const detail = section(jobsContent, 'Job output', 'Choose a job above to inspect its logs and full result.', 'operations-job-detail');
  const detailActions = node('div', '', 'cookbook-actions');
  detail.append(detailActions);
  const cancel = button('Cancel selected job', () => task(async () => {
    if (!selectedJob) throw new Error('Select an active job first.');
    if (await confirmOperation('Cancel the selected host job? The worker will terminate its child process. Backend downloads or generations may take time to stop.')) {
      await request('jobs/cancel', { id: selectedJob });
      status('Cancellation requested.');
      await refreshJobs();
    }
  }));
  cancel.id = 'operations-job-cancel';
  detailActions.append(cancel, button('Export result JSON', () => task(async () => {
    if (!selectedJob) throw new Error('Select a job first.');
    const result = await request('jobs/detail', { id: selectedJob });
    const url = URL.createObjectURL(new Blob([JSON.stringify(result.job, null, 2)], { type: 'application/json' }));
    const link = node('a'); link.href = url; link.download = `operations-${selectedJob}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  })));
  const log = node('pre', 'No job selected.', 'operations-diff');
  log.id = 'operations-job-output';
  const result = node('pre', '', 'operations-diff');
  result.id = 'operations-job-result';
  const logStream = node('div', '', 'operations-job-stream');
  const resultStream = node('div', '', 'operations-job-stream');
  logStream.append(node('h3', 'Output log'), log);
  resultStream.append(node('h3', 'Result JSON'), result);
  detail.append(logStream, resultStream);
}

function options(input, values) {
  const previous = input.value;
  input.replaceChildren(...values.map(value => {
    const entry = node('option', value); entry.value = value; return entry;
  }));
  if (values.includes(previous)) input.value = previous;
}

async function refreshManagement() {
  const result = await request('management');
  el('operations-management-content').hidden = false;
  options(el('operations-docker-name'), result.containers || []);
  options(el('operations-service-name'), Object.keys(result.services || {}));
  for (const target of ['docker', 'service']) {
    const select = el(`operations-${target}-name`);
    select.closest('section').querySelectorAll('[data-host-control]').forEach(control => { control.disabled = !select.options.length; });
  }
  status(result.demo ? 'Preview data — controls are simulated.' : 'Host controls ready.');
  const draft = strataState && (el('operations-strata-quantization').value !== strataState.model || Number(el('operations-strata-context').value) !== strataState.configured_context);
  if (!strataState || (!draft && !strataReview)) await loadStrata();
  try {
    const overview = await request('overview');
    const models = overview.ollama?.installed || [];
    el('operations-model-suggestions').replaceChildren(...models.map(model => {
      const option = node('option'); option.value = model.name; return option;
    }));
    el('operations-benchmark-backend').onchange = () => {
      if (el('operations-benchmark-backend').value === 'strata') el('operations-benchmark-model').value = overview.strata?.model || '';
    };
  } catch {}
}

async function loadStrata() {
  strataReview = null;
  el('operations-strata-apply').disabled = true;
  el('operations-strata-command').hidden = true;
  try {
    strataState = await request('strata/config');
    el('operations-strata-config-fields').hidden = false;
    el('operations-strata-context').value = strataState.configured_context;
    options(el('operations-strata-quantization'), (strataState.models || [{ name: strataState.model }]).map(model => model.name));
    el('operations-strata-quantization').value = strataState.model;
    el('operations-strata-quantization').dispatchEvent(new Event('change'));
    el('operations-strata-config-status').textContent = `Configured: ${strataState.model} · ${strataState.configured_context} tokens\nActive: ${strataState.active_model ?? 'unavailable'} · ${strataState.active_context ?? 'unavailable'} tokens\n${strataState.family} · ${strataState.backend} · port ${strataState.port}\n` +
      (strataState.restart_available ? `Generated launcher: ${strataState.launcher}` : 'Configure a Strata service that executes the generated launcher before applying.');
  } catch (error) {
    strataState = null;
    el('operations-strata-config-fields').hidden = true;
    el('operations-strata-config-status').textContent = error.message;
  }
}

const number = value => Number.isFinite(value) ? value.toFixed(2) : '—';
async function refreshDetail() {
  if (!selectedJob) return;
  const result = await request('jobs/detail', { id: selectedJob });
  el('operations-job-output').textContent = result.log || result.job.error || 'No output yet.';
  el('operations-job-result').textContent = JSON.stringify(result.job.result ?? { state: result.job.state, result_error: result.job.result_error }, null, 2);
  el('operations-job-cancel').disabled = !['queued', 'running'].includes(result.job.state);
}

async function refreshJobs() {
  const result = await request('jobs');
  el('operations-jobs-content').hidden = false;
  el('operations-scripts-card').querySelectorAll('input, button').forEach(input => { input.disabled = !result.scripts_enabled; });
  const scripts = el('operations-script-list');
  scripts.replaceChildren();
  for (const script of result.scripts || []) {
    const row = node('div', '', 'operations-card');
    row.append(node('p', `${script.name} · ${script.path}`));
    const actions = node('div', '', 'cookbook-actions');
    actions.append(button('Edit', () => {
      el('operations-script-name').value = script.name;
      el('operations-script-path').value = script.path;
      el('operations-script-args').value = JSON.stringify(script.arguments);
      el('operations-script-timeout').value = script.timeout;
    }), button('Run', () => task(async () => {
      if (!await confirmOperation(`Run ${script.name} with its saved arguments and a ${script.timeout}s timeout?`)) return;
      const started = await request('jobs/start', { kind: 'script', name: script.name });
      selectedJob = started.job.id;
      await refreshJobs();
      status('Script started.');
    })), button('Remove definition', () => task(async () => {
      if (!await confirmOperation(`Remove the saved definition for ${script.name}? The Python file remains on the host.`)) return;
      await request('scripts/save', { remove: true, name: script.name });
      await refreshJobs();
      status('Definition removed.');
    })));
    row.append(actions); scripts.append(row);
  }
  const table = node('table');
  const head = node('thead'); const headings = node('tr');
  ['Started', 'Job', 'State', 'TTFT s', 'Decode TPS', 'Prefill TPS', 'End-to-end TPS', ''].forEach(title => headings.append(node('th', title)));
  head.append(headings); table.append(head);
  const body = node('tbody');
  for (const job of result.jobs || []) {
    const summary = job.result?.summary || {};
    const row = node('tr');
    const spec = job.specification;
    const description = spec.name || (spec.context ? `${spec.model || ''} · ${spec.context} tokens` : (spec.backend ? spec.backend + '/' : '') + (spec.model || spec.operation));
    [new Date(job.created_at * 1000).toLocaleString(), `${spec.kind}: ${description}`, job.state,
      number(summary.ttft_s), number(summary.decode_tps), number(summary.prefill_tps), number(summary.end_to_end_tps)].forEach(value => row.append(node('td', value)));
    const action = node('td'); action.append(button('Inspect', () => task(async () => { selectedJob = job.id; await refreshDetail(); })));
    row.append(action); body.append(row);
  }
  table.append(body); el('operations-job-list').replaceChildren(table);
  await refreshDetail();
  status(result.demo ? 'Preview data — jobs and results are simulated.' : `${result.jobs?.length || 0} recent jobs. Logs refresh while this tab is visible.`);
}

async function poll() {
  if (active !== 'jobs' || document.hidden || polling || busy) return;
  polling = true;
  try { await refreshJobs(); }
  catch (error) { status(error.message, true); }
  finally { polling = false; }
}

export function openManagement(panel) {
  initialize();
  active = panel;
  if (busy) pendingPanel = panel;
  else task(panel === 'management' ? refreshManagement : refreshJobs);
  if (panel === 'jobs') timer = setInterval(poll, 5000);
}

export function closeManagement() {
  clearInterval(timer);
  timer = null;
  active = null;
  pendingPanel = null;
}
