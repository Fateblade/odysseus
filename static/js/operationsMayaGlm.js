let ui;
let state;
let review;
let aliases = [];
let sequence = 0;
export const selectedGlmModel = () => aliases[0] || 'glm-5.3-flash';

export function initializeMayaGlm(parent, helpers) {
  const { section, field, toggle, engineControls, button, node, task, request, confirmOperation, started } = helpers;
  const card = section(parent, 'Maya GLM', 'GLM-5.3-Flash · Maya-S-v2', 'operations-maya-glm-residency');
  const live = node('div', 'Reading live status…'); live.id = 'operations-maya-glm-live';
  const sampled = node('p', '', 'operations-muted'); sampled.id = 'operations-maya-glm-sampled';
  const { start, stop } = engineControls(card, 'maya_glm');
  const status = node('p', 'Reading GLM settings…', 'operations-status'); status.id = 'operations-maya-glm-config-status';
  const fields = node('div'); fields.hidden = true;
  const context = field(fields, 'operations-maya-glm-context', 'Maximum context (tokens)', 8192, null, 'number'); context.step = 1;
  const drafting = toggle(fields, 'operations-maya-glm-drafting', 'Native MTP speculative decoding');
  const draftingNote = node('p', '', 'operations-muted'); fields.append(draftingNote);
  const diff = node('pre', '', 'operations-diff'); diff.hidden = true;
  const apply = button('Apply changes', () => task(async () => {
    if (!review) throw new Error('Review the GLM settings change first.');
    const effect = review.managed_settings ? `Native MTP ${review.drafting ? 'on' : 'off'}. A running service restarts and interrupts requests; a stopped service stays stopped. Previous settings are backed up for recovery.` : 'Native reload may interrupt requests.';
    if (!await confirmOperation(`Set GLM context to ${review.context.toLocaleString()} tokens? ${effect} More context consumes more memory.`)) return;
    const result = await request('maya-glm/apply', { revision: review.revision, context: review.context, ...(review.managed_settings ? { drafting: review.drafting } : {}), review_digest: review.review_digest });
    review = null; state = null; apply.disabled = true; diff.hidden = true;
    await started(result);
  })); apply.disabled = true;
  const invalidate = () => { review = null; apply.disabled = true; diff.hidden = true; };
  context.addEventListener('input', invalidate);
  drafting.addEventListener('change', invalidate);
  const settingActions = node('div', '', 'cookbook-actions');
  settingActions.append(button('Review changes', () => task(async () => {
    invalidate(); const value = Number(context.value); const revision = state?.revision; const mtp = drafting.checked;
    const result = await request('maya-glm/preview', { revision, context: value, ...(state?.managed_settings ? { drafting: mtp } : {}) });
    if (Number(context.value) !== value || drafting.checked !== mtp || state?.revision !== revision) throw new Error('GLM settings changed during review. Review again.');
    review = result; diff.textContent = result.display_change; diff.hidden = false; apply.disabled = false;
  })), apply);
  fields.append(settingActions, diff);
  card.append(live, sampled, status, button('Reload settings', () => task(() => refreshMayaGlm(true))), fields);
  ui = { ...helpers, live, status, fields, context, drafting, draftingNote, diff, apply, start, stop, invalidate };
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
  if (!ui || (!force && (review || (state && (Number(ui.context.value) !== (state.configured_context ?? state.active_context) || ui.drafting.checked !== Boolean(state.drafting)))))) return;
  const version = ++sequence;
  const context = ui.context.value;
  const drafting = ui.drafting.checked;
  try {
    const result = await ui.request('maya-glm/config');
    if (version !== sequence || (!force && (review || ui.context.value !== context || ui.drafting.checked !== drafting))) return;
    if (force) ui.invalidate();
    ui.status.textContent = result.configured ? `Configured: ${(result.configured_context ?? result.active_context).toLocaleString()} tokens · Active: ${result.active_context?.toLocaleString() || 'Stopped'}. Accepted range: ${result.minimum_context.toLocaleString()}–${result.maximum_context.toLocaleString()}.` : result.message;
    ui.fields.hidden = !result.configured;
    if (result.configured) {
      state = result; ui.context.value = result.configured_context ?? result.active_context; ui.context.min = result.minimum_context; ui.context.max = result.maximum_context;
      ui.drafting.checked = Boolean(result.drafting); ui.drafting.disabled = !result.managed_settings;
      ui.draftingNote.textContent = result.managed_settings ? `Native MTP ${result.drafting ? 'on' : 'off'}. ${result.running ? 'Applying restarts this service and verifies the engine switch.' : 'Applying saves settings and keeps the service stopped.'}` : 'Configure the managed GLM profile to enable the MTP switch. Native context reload remains available.';
    }
  } catch (error) {
    if (version !== sequence || (!force && (review || ui.context.value !== context || ui.drafting.checked !== drafting))) return;
    ui.status.textContent = `${error.message}${state ? ' Previous settings are stale; reload before reviewing changes.' : ''}`;
    if (error.unconfigured) { state = null; ui.fields.hidden = true; }
  }
}
