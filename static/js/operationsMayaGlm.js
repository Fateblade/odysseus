let ui;
let state;
let review;
let aliases = [];
let sequence = 0;
export const selectedGlmModel = () => aliases[0] || 'glm-5.3-flash';

export function initializeMayaGlm(parent, helpers) {
  const { section, field, button, node, task, request, manage, refreshResidency, confirmOperation, started } = helpers;
  const card = section(parent, 'Maya GLM residency', 'Isolated GLM-5.3-Flash Maya-S-v2 profile. Stop Maya DS41F and other large models before starting. Existing services are never stopped automatically.', 'operations-maya-glm-residency');
  const live = node('div', 'Reading live status…'); live.id = 'operations-maya-glm-live';
  const sampled = node('p', '', 'operations-muted'); sampled.id = 'operations-maya-glm-sampled';
  const actions = node('div', '', 'cookbook-actions');
  const start = button('Start GLM Maya', () => task(() => manage({ target: 'service', name: 'maya_glm', operation: 'start' })));
  const stop = button('Stop GLM Maya', () => task(() => manage({ target: 'service', name: 'maya_glm', operation: 'stop' })));
  start.disabled = true; stop.disabled = true;
  actions.append(start, stop, button('Refresh status', () => task(refreshResidency)));
  const status = node('p', 'Read context limits after GLM is ready.', 'operations-status'); status.id = 'operations-maya-glm-config-status';
  const fields = node('div'); fields.hidden = true;
  const context = field(fields, 'operations-maya-glm-context', 'Maximum context (tokens)', 8192, null, 'number'); context.step = 1;
  const diff = node('pre', '', 'operations-diff'); diff.hidden = true;
  const apply = button('Apply GLM context', () => task(async () => {
    if (!review) throw new Error('Review the GLM context change first.');
    if (!await confirmOperation(`Set GLM Maya context to ${review.context.toLocaleString()} tokens? Native reload saves its isolated config and may interrupt requests. More context consumes more memory.`)) return;
    const result = await request('maya-glm/apply', { revision: review.revision, context: review.context, review_digest: review.review_digest });
    review = null; state = null; apply.disabled = true; diff.hidden = true;
    await started(result);
  })); apply.disabled = true;
  const invalidate = () => { review = null; apply.disabled = true; diff.hidden = true; };
  context.addEventListener('input', invalidate);
  const settingActions = node('div', '', 'cookbook-actions');
  settingActions.append(button('Review GLM context', () => task(async () => {
    invalidate(); const value = Number(context.value); const revision = state?.revision;
    const result = await request('maya-glm/preview', { revision, context: value });
    if (Number(context.value) !== value || state?.revision !== revision) throw new Error('GLM settings changed during review. Review again.');
    review = result; diff.textContent = result.display_change; diff.hidden = false; apply.disabled = false;
  })), apply);
  fields.append(settingActions, diff);
  card.append(live, sampled, actions, status, button('Reload GLM context limits', () => task(() => refreshMayaGlm(true))), fields,
    node('p', 'GLM uses its embedded native MTP, not DeepSeek DSpark. The setup guide selects the initial MTP mode explicitly. Context edits require this GLM server to be loaded and ready.', 'operations-muted'));
  ui = { ...helpers, live, status, fields, context, diff, apply, start, stop, invalidate };
}

export function renderMayaGlm(data) {
  if (!ui) return;
  aliases = data.aliases || [];
  const { node } = ui;
  ui.live.replaceChildren(node('p', data.status === 'ok' ? 'GLM model loaded; server ready' : data.status === 'unloaded' ? 'Unloaded' : data.status === 'disabled' ? 'Not configured' : 'Stopped, starting or unavailable', 'operations-residency-state'));
  ui.live.append(node('p', `Model: ${data.model || 'Not reported'} · Context: ${data.max_context?.toLocaleString() || 'Not reported'} · Maya: ${data.version || 'Not reported'}`, 'operations-muted'));
  ui.live.append(node('p', data.message || '', 'operations-muted'));
}

export function configureMayaGlm(services) {
  if (!ui) return;
  ui.start.disabled = ui.stop.disabled = !Object.hasOwn(services, 'maya_glm');
}

export async function refreshMayaGlm(force = false) {
  if (!ui || (!force && (review || (state && Number(ui.context.value) !== state.active_context)))) return;
  const version = ++sequence;
  const context = ui.context.value;
  try {
    const result = await ui.request('maya-glm/config');
    if (version !== sequence || (!force && (review || ui.context.value !== context))) return;
    if (force) ui.invalidate();
    ui.status.textContent = result.configured ? `Active: ${result.active_context.toLocaleString()} tokens. Accepted range: ${result.minimum_context.toLocaleString()}–${result.maximum_context.toLocaleString()}.` : result.message;
    ui.fields.hidden = !result.configured;
    if (result.configured) { state = result; ui.context.value = result.active_context; ui.context.min = result.minimum_context; ui.context.max = result.maximum_context; }
  } catch (error) {
    if (version !== sequence || (!force && (review || ui.context.value !== context))) return;
    ui.status.textContent = `${error.message}${state ? ' Previous settings are stale; reload before reviewing changes.' : ''}`;
    if (error.unconfigured) { state = null; ui.fields.hidden = true; }
  }
}
