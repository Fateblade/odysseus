let page = 0;
let lastResult = null;
let elements;
let api;
let inspectJob;
let runTask;
const make = (tag, text = '') => { const item = document.createElement(tag); item.textContent = text; return item; };
const rate = value => Number.isFinite(value) ? value.toFixed(2) : '—';
const count = value => Number.isFinite(value) ? value.toLocaleString() : '—';
const gib = value => Number.isFinite(value) ? (value / 1024 ** 3).toFixed(2) : '—';
const workloadText = item => {
  if (item.historical) return 'Historical · original settings';
  const labels = { custom: 'Custom prompt', research: 'Research synthesis', summarization: 'Summarization', code_review: 'Code review',
    code_generation: 'Code generation', text_generation: 'Text generation', mixed: 'Mixed task suite' };
  const workload = item.workload || {};
  return [labels[workload.task_family] || 'Custom prompt', workload.task_size,
    workload.task_mode === 'different' ? 'Different tasks' : 'Repeated task'].filter(Boolean).join(' · ');
};


export function initializeScoreboard(parent, helpers) {
  const { section, field, button, request, task, inspect } = helpers;
  api = request; runTask = task; inspectJob = inspect;
  const card = section(parent, 'Model measurement scoreboard', 'Saved on the inference host. Medians exclude warmup. Compare matching prompts, token counts, context, quantization and hardware. End-to-end TPS includes prefill and queueing; it is separate from native decode TPS.', 'operations-scoreboard');
  const grid = make('div'); grid.className = 'operations-grid'; card.append(grid);
  const backend = field(grid, 'scoreboard-backend', 'Backend filter', '', ['', 'ollama', 'strata', 'dwarfstar', 'maya']);
  backend.options[0].textContent = 'All backends';
  const model = field(grid, 'scoreboard-model', 'Model filter');
  const sort = field(grid, 'scoreboard-sort', 'Order', 'created_at', ['created_at', 'end_to_end_tps', 'decode_tps', 'ttft_s']);
  ['Newest first', 'End-to-end TPS: highest first', 'Decode TPS: highest first', 'TTFT: lowest first'].forEach((text, i) => { sort.options[i].textContent = text; });
  const workload = field(grid, 'scoreboard-workload', 'Workload filter', '', ['', 'historical', 'custom', 'research', 'summarization', 'code_review', 'code_generation', 'text_generation', 'mixed']);
  ['All workloads', 'Historical entries', 'Custom prompt', 'Research synthesis', 'Summarization', 'Code review', 'Code generation', 'Text generation', 'Mixed task suite'].forEach((text, index) => { workload.options[index].textContent = text; });
  card.append(button('Apply filters / refresh', () => task(async () => { page = 0; await refreshScoreboard(); })));
  const info = make('p'); info.className = 'operations-muted'; info.setAttribute('role', 'status'); card.append(info);
  const list = make('div'); list.className = 'operations-table-wrap'; card.append(list);
  const previous = button('Previous page', () => task(async () => { page--; await refreshScoreboard(); }));
  const next = button('Next page', () => task(async () => { page++; await refreshScoreboard(); }));
  const actions = make('div'); actions.className = 'cookbook-actions'; actions.append(previous, next, button('Export this page JSON', () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(lastResult, null, 2)], { type: 'application/json' }));
    const link = make('a'); link.href = url; link.download = 'model-measurements.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  })); card.append(actions);
  elements = { backend, model, sort, workload, info, list, previous, next };
  const importer = make('details'); importer.append(make('summary', 'Import earlier TPS script JSON lines'));
  importer.append(make('p', 'Paste one measured run per line, excluding warmup. elapsed_s and output_tokens are required. Leave settings unknown unless recorded at the time. Imported context and artifact size are user supplied.'));
  const inputs = make('div'); inputs.className = 'operations-grid'; importer.append(inputs);
  const source = field(inputs, 'scoreboard-import-backend', 'Backend', 'dwarfstar', ['dwarfstar', 'ollama', 'strata', 'maya']);
  const name = field(inputs, 'scoreboard-import-model', 'Model name', 'deepseek-v4-flash');
  const context = field(inputs, 'scoreboard-import-context', 'Runtime context tokens (optional)', '', null, 'number');
  const size = field(inputs, 'scoreboard-import-size', 'Model artifacts GiB (optional; excludes runtime buffers)', '', null, 'number'); size.step = 'any';
  const quantization = field(inputs, 'scoreboard-import-quant', 'Quantization (optional)');
  const version = field(inputs, 'scoreboard-import-version', 'Engine version / revision (optional)');
  const prompt = field(inputs, 'scoreboard-import-prompt', 'Original prompt (optional; used for fingerprint)', '', null, 'textarea');
  const notes = field(inputs, 'scoreboard-import-notes', 'Notes / hardware / date of original run', '', null, 'textarea');
  const jsonl = field(inputs, 'scoreboard-import-jsonl', 'Measured sample JSON lines', '', null, 'textarea');
  const file = field(inputs, 'scoreboard-import-file', 'Load JSONL file (optional)', '', null, 'file');
  file.accept = '.jsonl,.json,.txt';
  file.addEventListener('change', () => task(async () => {
    const selected = file.files[0];
    if (!selected) return;
    if (selected.size > 262144) throw new Error('Use a JSONL file of at most 256 KiB.');
    jsonl.value = await selected.text();
  }));
  importer.append(button('Import measurements', () => task(async () => {
    const result = await request('measurements/import', { backend: source.value, model: name.value, jsonl: jsonl.value,
      context_tokens: context.value ? Number(context.value) : null,
      model_size_bytes: size.value ? Math.round(Number(size.value) * 1024 ** 3) : null,
      quantization: quantization.value, engine_version: version.value, prompt: prompt.value || null, notes: notes.value });
    page = 0; await refreshScoreboard(); elements.info.textContent += ` · ${result.status}`;
  }))); card.append(importer);
}

export async function refreshScoreboard() {
  if (!elements) return;
  const { backend, model, sort, workload, info, list, previous, next } = elements;
  const result = await api('measurements', { page, backend: backend.value, model: model.value, sort: sort.value, workload: workload.value });
  lastResult = result;
  info.textContent = `${result.total} measurements · page ${page + 1}. Missing metrics remain unknown. Context is allocation, not filled prompt length. Size is model artifacts, not total RAM. Mixed workloads combine different tasks; compare like-for-like settings. Inspect for per-task summaries, individual runs and memory snapshots.`;
  previous.disabled = page === 0; next.disabled = (page + 1) * 100 >= result.total;
  const table = make('table'); const head = make('thead'); const titles = make('tr');
  ['Recorded', 'Backend / model', 'Workload', 'E2E TPS', 'Decode TPS', 'Prefill TPS', 'TTFT s', 'Prompt / output tokens', 'Context', 'Artifacts GiB', 'Runs / output cap', 'Settings', ''].forEach(title => titles.append(make('th', title)));
  head.append(titles); table.append(head); const body = make('tbody');
  for (const item of result.measurements || []) {
    const row = make('tr'); const env = item.environment || {}; const declared = env.declared || {}; const stats = item.summary || {};
    const settings = [`Warmup: ${item.warmup ?? 'unknown'}`, `T: ${item.temperature ?? 'unknown'}, seed: ${item.seed ?? 'unknown'}`,
      `Thinking: ${item.thinking ?? 'unknown'}`, `Task set seed: ${item.workload?.task_seed ?? 'unknown'}`, `Cached prompt tokens: ${count(stats.prompt_cached_tokens)}`, `Quant: ${declared.quantization || 'unknown'} (declared)`,
      `Engine: ${env.engine_version || declared.engine_version || 'unknown'}`, `Workload fingerprint: ${(item.workload_sha256 || item.prompt_sha256)?.slice(0, 12) || 'unknown'}`, item.source, item.notes].filter(Boolean).join(' · ');
    [new Date(item.created_at * 1000).toLocaleString(), `${item.backend} / ${item.model}`, workloadText(item), rate(stats.end_to_end_tps), rate(stats.decode_tps),
      rate(stats.prefill_tps), rate(stats.ttft_s), `${count(stats.prompt_tokens)} / ${count(stats.output_tokens)}`,
      count(env.context_tokens), `${gib(env.model_size_bytes)}${env.model_size_source?.startsWith('user') || item.source?.startsWith('imported') ? ' (declared)' : ''}`,
      `${item.runs} / ${count(item.max_tokens)}`, settings].forEach(text => row.append(make('td', text)));
    const action = make('td'); const inspect = make('button', 'Inspect'); inspect.type = 'button'; inspect.className = 'cookbook-btn';
    inspect.addEventListener('click', () => runTask(() => inspectJob(item.id))); action.append(inspect); row.append(action); body.append(row);
  }
  if (!body.children.length) { const row = make('tr'); const cell = make('td', 'No saved measurements. Run a benchmark or import script output.'); cell.colSpan = 13; row.append(cell); body.append(row); }
  table.append(body); list.replaceChildren(table);
}
