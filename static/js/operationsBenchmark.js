let controls;
let taskCatalog;
const labelOptions = (input, entries) => {
  const previous = input.value;
  input.replaceChildren(...entries.map(([value, label]) => { const item = document.createElement('option'); item.value = value; item.textContent = label; return item; }));
  if (entries.some(([value]) => value === previous)) input.value = previous;
};

export function setBenchmarkCatalog(catalog) {
  if (!catalog || !controls || taskCatalog?.version === catalog.version) return;
  taskCatalog = catalog;
  const { family } = controls;
  labelOptions(family, [['custom', 'Custom prompt'], ...catalog.families.map(item => [item.id, item.label]), ['mixed', 'Mixed task suite']]);
  controls.describe();
}

export function refreshBenchmarkLimits() {
  controls?.refreshLimits();
}

export function initializeBenchmark(parent, { section, field, button, request, task, confirmOperation, modelForBackend, suggestions, started }) {
  const card = section(parent, 'TPS benchmark', 'Compare reproducible workloads. Native decode TPS uses backend timings; end-to-end TPS includes loading, queueing and prefill. Caches are not cleared.', 'operations-benchmark');
  const form = document.createElement('form'); form.addEventListener('submit', event => event.preventDefault()); card.append(form);
  const grid = document.createElement('div'); grid.className = 'operations-grid'; form.append(grid);
  const backend = field(grid, 'operations-benchmark-backend', 'Backend', 'ollama', ['ollama', 'strata', 'dwarfstar', 'maya']);
  const model = field(grid, 'operations-benchmark-model', 'Model name (1–512 characters, no whitespace)');
  model.required = true; model.maxLength = 512; model.pattern = '\\S+'; model.setAttribute('list', suggestions);
  backend.addEventListener('change', () => { const selected = modelForBackend(backend.value); if (selected) model.value = selected; });
  const family = field(grid, 'operations-benchmark-task', 'Task', 'custom', ['custom']); family.options[0].textContent = 'Custom prompt';
  const size = field(grid, 'operations-benchmark-task-size', 'Source size (approximate characters)', 'small', ['small', 'medium', 'large']);
  labelOptions(size, [['small', 'Small · ~4,000 characters'], ['medium', 'Medium · ~16,000 characters'], ['large', 'Large · ~64,000 characters']]);
  const mode = field(grid, 'operations-benchmark-task-mode', 'Task repetition', 'repeat', ['repeat', 'different']);
  labelOptions(mode, [['repeat', 'Same task each run'], ['different', 'Different task each run']]);
  const seed = field(grid, 'operations-benchmark-task-seed', 'Task set seed (optional; 0–2,147,483,647; blank = fresh set)', '', null, 'number'); seed.min = 0; seed.max = 2147483647; seed.step = 1;
  const warmup = field(grid, 'operations-benchmark-warmup', 'Warmup (excluded from measurements)', 'yes', ['yes', 'no']);
  const runs = field(grid, 'operations-benchmark-runs', 'Measured runs (1–20)', 3, null, 'number'); runs.min = 1; runs.max = 20; runs.step = 1; runs.required = true;
  const tokens = field(grid, 'operations-benchmark-tokens', 'Maximum output tokens per run (1–262,144)', 256, null, 'number'); tokens.min = 1; tokens.max = 262144; tokens.step = 1; tokens.required = true;
  const timeout = field(grid, 'operations-benchmark-timeout', 'Job timeout seconds (60–604,800; default 7,200 = 2 hours)', 7200, null, 'number'); timeout.min = 60; timeout.max = 604800; timeout.step = 1; timeout.required = true;
  const artifacts = field(grid, 'operations-benchmark-size', 'Model artifacts GiB (optional; >0 to 8,388,607)', '', null, 'number'); artifacts.min = 1 / 1024 ** 3; artifacts.max = 8388607; artifacts.step = 'any';
  const quantization = field(grid, 'operations-benchmark-quant', 'Quantization (optional; up to 200 characters)'); quantization.maxLength = 200;
  const version = field(grid, 'operations-benchmark-version', 'Engine version / revision (optional; up to 200 characters)'); version.maxLength = 200;
  const notes = field(grid, 'operations-benchmark-notes', 'Notes / test label (optional; up to 2,000 characters)', '', null, 'textarea'); notes.maxLength = 2000;
  const prompt = field(grid, 'operations-benchmark-prompt', 'Custom prompt (1–32,768 characters)', 'Explain how shared memory works in an integrated GPU system.', null, 'textarea'); prompt.maxLength = 32768;
  prompt.closest('label').classList.add('operations-benchmark-prompt');
  const description = document.createElement('p'); description.className = 'operations-muted'; form.append(description);
  function describe() {
    const custom = family.value === 'custom'; const mixed = family.value === 'mixed';
    if (custom) mode.value = 'repeat'; if (mixed) mode.value = 'different';
    mode.disabled = custom || mixed; size.disabled = custom; seed.disabled = custom;
    prompt.disabled = !custom; prompt.required = custom; prompt.closest('label').hidden = !custom;
    const selected = taskCatalog?.families.find(item => item.id === family.value);
    description.textContent = custom ? 'Custom prompts repeat verbatim, including warmup. Backend prompt caches may be reused.' :
      `${mixed ? 'Cycles through research, summarization, code review, code generation and text generation. Choose at least 5 runs to cover all five.' : selected?.description || ''} Source sizes are characters, not tokens. A blank task-set seed creates fresh inputs for each benchmark; reuse a recorded seed for like-for-like model comparisons. ` +
      (mode.value === 'different' ? 'Every measured run has different source material; warmup uses a separate task. This reduces prompt reuse but does not clear backend caches.' : 'The same task repeats; warmup may populate its prompt cache.');
  }
  family.addEventListener('change', describe); mode.addEventListener('change', describe);
  const budgetText = document.createElement('p'); budgetText.className = 'operations-muted operations-benchmark-budget';
  budgetText.id = 'operations-benchmark-budget'; budgetText.setAttribute('role', 'status');
  grid.insertBefore(budgetText, family.closest('label'));
  let baseline = null;
  let checked = null;
  let revision = 0;
  let modelRevision = 0;
  let modelTimer;
  const inputValues = () => ({ kind: 'benchmark', backend: backend.value, model: model.value, prompt: prompt.value,
    task_family: family.value, task_size: size.value, task_mode: mode.value, task_seed: seed.value === '' ? null : Number(seed.value),
    model_size_bytes: artifacts.value ? Math.round(Number(artifacts.value) * 1024 ** 3) : null,
    quantization: quantization.value, engine_version: version.value, notes: notes.value,
    runs: Number(runs.value), max_tokens: Number(tokens.value), timeout: Number(timeout.value), warmup: warmup.value === 'yes' });
  const planKey = () => JSON.stringify([backend.value, model.value, family.value, size.value, mode.value, seed.value, runs.value, warmup.value, prompt.value]);
  function renderBudget() {
    const budget = checked?.key === planKey() ? checked.budget : baseline;
    const maximum = budget?.max_output_tokens ?? 262144;
    tokens.max = maximum;
    tokens.closest('label').firstChild.textContent = maximum > 0 ? `Maximum output tokens per run (1–${maximum.toLocaleString()})` : 'No output budget available for this task';
    tokens.setCustomValidity(maximum < 1 ? 'The selected prompt leaves no room for output. Choose a smaller task or increase runtime context.' : '');
    const exceeds = Number(tokens.value) > maximum;
    if (!budget) budgetText.textContent = 'Select a model to read its active runtime context. Application output cap: 262,144 tokens; model limits are not yet known.';
    else if (!budget.context_tokens) budgetText.textContent = budget.message;
    else budgetText.textContent = `Active runtime context: ${budget.context_tokens.toLocaleString()} tokens. ` +
      (budget.exact ? `Largest task prompt: ${budget.prompt_tokens.toLocaleString()} · engine reserve: ${budget.reserve_tokens} · available output: ${maximum.toLocaleString()} tokens. ` :
        `Output ceiling before prompt allowance: ${maximum.toLocaleString()} tokens. Check task limits before choosing a large cap. `) +
      (budget.exact && budget.task_seed != null ? `Task-set seed: ${budget.task_seed}. ` : '') +
      (exceeds ? `Current output cap ${Number(tokens.value).toLocaleString()} exceeds this limit.` : '') +
      (!budget.exact ? ' Exact remaining output space is not yet verified.' : '');
    budgetText.classList.toggle('operations-error', exceeds || maximum < 1);
    useBudget.disabled = !budget?.exact || maximum < 1;
  }
  async function refreshLimits() {
    ++revision; const version = ++modelRevision; const selection = `${backend.value}/${model.value}`; checked = null; baseline = null;
    renderBudget();
    if (!model.value || !model.checkValidity()) return;
    budgetText.textContent = 'Reading the selected model’s active runtime context…';
    try {
      const result = await request('benchmark/limits', { backend: backend.value, model: model.value });
      if (version !== modelRevision || selection !== `${backend.value}/${model.value}`) return;
      if (checked && checked.budget.context_tokens !== result.context_tokens) checked = null;
      baseline = result; renderBudget();
    } catch (error) {
      if (version !== modelRevision || selection !== `${backend.value}/${model.value}` || checked) return;
      renderBudget(); budgetText.textContent = `Runtime limits unavailable: ${error.message} The application cap is not a verified model limit.`;
    }
  }
  async function checkTasks() {
    clearTimeout(modelTimer);
    const key = planKey(); const version = ++revision;
    const body = inputValues();
    if (checked?.key === key && body.task_seed == null) body.task_seed = checked.budget.task_seed;
    budgetText.textContent = 'Checking task token budgets without running inference…';
    let result;
    try { result = await request('benchmark/limits', { ...body, validate_tasks: true }); }
    catch (error) { if (version === revision) { renderBudget(); budgetText.textContent += ` Task check failed: ${error.message}`; } throw error; }
    if (version !== revision || key !== planKey()) throw new Error('Model or task settings changed during the check. Check again.');
    checked = { key, budget: result }; baseline = { ...result, exact: false, prompt_tokens: null,
      max_output_tokens: result.context_tokens ? Math.min(262144, Math.max(0, result.context_tokens - result.reserve_tokens - 1)) : 262144 };
    renderBudget();
    return { ...inputValues(), task_seed: inputValues().task_seed ?? result.task_seed };
  }
  const useBudget = button('Use available output cap', () => {
    if (checked?.key !== planKey() || !checked.budget.exact || checked.budget.max_output_tokens < 1) return;
    tokens.value = checked.budget.max_output_tokens; renderBudget();
  });
  useBudget.disabled = true;
  const checkButton = button('Check task limits', () => task(async () => {
    if (!model.reportValidity() || !runs.reportValidity() || !seed.reportValidity() || !prompt.reportValidity()) return;
    await checkTasks();
  }));
  const budgetActions = document.createElement('div'); budgetActions.className = 'cookbook-actions';
  budgetActions.append(checkButton, useBudget); form.append(budgetActions);
  const modelChanged = () => { ++revision; ++modelRevision; baseline = null; checked = null; renderBudget(); clearTimeout(modelTimer); modelTimer = setTimeout(refreshLimits, 300); };
  backend.addEventListener('change', modelChanged); model.addEventListener('input', modelChanged); model.addEventListener('change', modelChanged);
  for (const input of [family, size, mode, seed, warmup, runs, prompt]) input.addEventListener('input', () => { ++revision; checked = null; renderBudget(); });
  family.addEventListener('change', () => { ++revision; checked = null; renderBudget(); });
  tokens.addEventListener('input', renderBudget);
  controls = { family, describe, refreshLimits }; describe(); renderBudget();
  const note = document.createElement('p'); note.className = 'operations-muted';
  note.textContent = 'The output cap does not force that many tokens: generation can stop at EOS. Input plus output must fit the backend context; a backend may reject a larger cap. Large outputs may need a longer job timeout. DwarfStar and Maya disable thinking; sampling uses temperature 0 and seed 42. Research presets use supplied synthetic notes, without web browsing.';
  form.append(note, button('Run benchmark', () => {
    if (!form.reportValidity()) return;
    task(async () => {
      const body = await checkTasks();
      if (!form.reportValidity()) throw new Error('The requested output cap does not fit the selected task. Review the displayed limit and use a smaller cap.');
      if (!await confirmOperation(`Run ${body.runs} measured ${body.task_family} tasks on ${body.backend}/${body.model}${body.warmup ? ' plus one warmup' : ''}? The output cap is ${body.max_tokens.toLocaleString()} tokens per task. This shares the running inference service with other requests.`)) return;
      await started(await request('jobs/start', body));
      checked = null; renderBudget();
    });
  }));
}
