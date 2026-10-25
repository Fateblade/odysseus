const sections = {
  audit: [['entries', 'Recent entries', '.operations-audit-entries'], ['detail', 'Entry details', '.operations-audit-detail']],
  overview: [
    ['hardware', 'Hardware', '#operations-metrics'], ['storage', 'Disk space', '.operations-storage-panel'],
    ['services', 'Inference services', '.operations-service-panel'], ['requests', 'Requests', '.operations-activity-panel'],
    ['processes', 'Processes', '.operations-process-panel'], ['inventory', 'Containers & models', '.operations-container-panel'],
  ],
  litellm: [['deployments', 'Deployments', '.operations-deployments'], ['routing', 'Routing', '.operations-routing'], ['review', 'Review changes', '#operations-preview']],
  management: [
    ['containers', 'Containers', '.operations-control-docker'],
    ['exclusive', 'Exclusive engines', '#operations-exclusive-engines'],
    ['maya', 'Maya DS41F', '.operations-maya-residency'], ['maya-glm', 'Maya GLM', '.operations-maya-glm-residency'],
    ['dwarfstar', 'DwarfStar residency', '.operations-dwarfstar-residency'],
    ['shared', 'Shared engines', '#operations-shared-engines'],
    ['strata', 'Strata residency', '.operations-strata-residency'], ['ollama', 'Ollama residency', '.operations-ollama-residency'],
    ['configuration', 'Strata configuration', '.operations-strata-configuration'], ['models', 'Ollama models', '.operations-ollama-control'],
    ['output', 'Logs & details', '.operations-command-output'],
  ],
  jobs: [['benchmark', 'TPS benchmark', '.operations-benchmark'], ['scoreboard', 'Model scoreboard', '.operations-scoreboard'], ['scripts', 'Python scripts', '.operations-scripts'],
    ['history', 'Jobs & comparison', '.operations-history'], ['detail', 'Job details', '.operations-job-detail']],
};

const workspace = document.getElementById('operations-workspace');
const navigation = document.createElement('nav');
navigation.className = 'operations-subsections';
const mobile = window.matchMedia('(max-width: 700px)');
let panel = null;
let entries = [];
let pending = null;
let frame = null;

const anchor = (name, key) => `operations-section-${name}-${key}`;
const sectionId = (name, key, selector) => document.querySelector(selector)?.id || anchor(name, key);
const visible = target => target && target.getClientRects().length > 0;

function place() {
  if (!panel) return;
  if (mobile.matches) document.querySelector('.operations-sidebar-footer').before(navigation);
  else document.getElementById(`operations-${panel}-tab`).after(navigation);
}

function mark(id) {
  for (const entry of entries) {
    if (entry.id === id) {
      const changed = !entry.link.hasAttribute('aria-current');
      entry.link.setAttribute('aria-current', 'location');
      if (changed && mobile.matches) entry.link.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
    } else entry.link.removeAttribute('aria-current');
  }
}

function jump(entry, focus = false) {
  const target = document.querySelector(entry.selector);
  if (!visible(target)) return false;
  mark(entry.id);
  const top = target.getBoundingClientRect().top - workspace.getBoundingClientRect().top + workspace.scrollTop - 16;
  workspace.scrollTo({ top: Math.max(0, top), behavior: 'instant' });
  if (focus) {
    if (!target.matches('button, input, select, textarea, a[href]')) target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }
  return true;
}

function update() {
  frame = null;
  if (!panel) return;
  const candidates = [];
  const threshold = workspace.getBoundingClientRect().top + Math.min(160, workspace.clientHeight * 0.25);
  for (const entry of entries) {
    const target = document.querySelector(entry.selector);
    const available = visible(target);
    entry.link.setAttribute('aria-disabled', String(!available));
    if (target && !target.id) target.id = entry.id;
    if (available) candidates.push({ ...entry, top: target.getBoundingClientRect().top });
  }
  if (pending) {
    const entry = entries.find(item => item.id === pending);
    if (entry && jump(entry)) { pending = null; return; }
  }
  if (!candidates.length) { mark(null); return; }
  const atBottom = workspace.scrollHeight > workspace.clientHeight && workspace.scrollTop + workspace.clientHeight >= workspace.scrollHeight - 2;
  const above = candidates.filter(entry => entry.top <= threshold || atBottom);
  const nearest = above.length ? Math.max(...above.map(entry => entry.top)) : Math.min(...candidates.map(entry => entry.top));
  const row = candidates.filter(entry => Math.abs(entry.top - nearest) < 8);
  const selected = row.find(entry => entry.link.hasAttribute('aria-current')) || row[0];
  mark(selected.id);
}

function schedule() {
  if (frame === null) frame = requestAnimationFrame(update);
}

export function showSections(name) {
  const hashPanel = panelForSectionHash();
  if (hashPanel && hashPanel !== name) history.replaceState(null, '', location.pathname + location.search);
  panel = name;
  navigation.setAttribute('aria-label', `${document.getElementById('operations-page-title').textContent} subsections`);
  entries = sections[name].map(([key, label, selector]) => {
    const link = document.createElement('a');
    const id = sectionId(name, key, selector);
    link.href = `#${id}`;
    link.textContent = label;
    const entry = { id, selector, link };
    link.addEventListener('click', event => {
      event.preventDefault();
      if (!jump(entry, true)) return;
      pending = null;
      history.replaceState(null, '', `#${id}`);
    });
    return entry;
  });
  navigation.replaceChildren(...entries.map(entry => entry.link));
  pending = entries.some(entry => `#${entry.id}` === location.hash) ? location.hash.slice(1) : null;
  place();
  schedule();
}

export function panelForSectionHash() {
  return Object.keys(sections).find(name => sections[name].some(([key, , selector]) => location.hash === `#${sectionId(name, key, selector)}`));
}

workspace.addEventListener('scroll', schedule, { passive: true });
window.addEventListener('resize', schedule);
mobile.addEventListener('change', place);
new MutationObserver(schedule).observe(workspace, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
