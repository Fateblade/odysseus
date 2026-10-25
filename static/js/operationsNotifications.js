export const notificationDurations = { status: 5000, warning: 30000, error: null };

export function createNotifier({ document, setTimeout, clearTimeout } = globalThis) {
  const seen = new Map();
  const active = new Set();
  let region;

  function show(message, { severity = 'status', key } = {}) {
    if (!message) return null;
    if (!Object.hasOwn(notificationDurations, severity)) throw new Error('Unknown notification severity.');
    const signature = `${severity}:${message}`;
    if (key && seen.get(key) === signature) return null;
    if (key) seen.set(key, signature);
    for (const item of active) if (key && item.key === key && item.signature === signature) return item.dismiss;
    if (!region) {
      region = document.createElement('div'); region.id = 'operations-notifications';
      region.className = 'operations-notifications'; region.setAttribute('role', 'region');
      region.setAttribute('aria-label', 'Notifications'); document.body.append(region);
    }
    const toast = document.createElement('div'); toast.className = `operations-notification operations-notification-${severity}`;
    toast.setAttribute('role', severity === 'error' ? 'alert' : 'status'); toast.setAttribute('aria-atomic', 'true');
    const heading = document.createElement('strong'); heading.textContent = { status: 'Status', warning: 'Warning', error: 'Error' }[severity];
    const text = document.createElement('p'); text.textContent = message;
    const close = document.createElement('button'); close.type = 'button'; close.textContent = '×';
    close.className = 'operations-notification-close'; close.setAttribute('aria-label', `Dismiss ${severity} notification`);
    const origin = document.activeElement;
    let timer = null;
    const record = { key, signature, dismiss: () => {
      if (!active.delete(record)) return;
      if (timer !== null) clearTimeout(timer);
      const focused = toast.contains(document.activeElement);
      toast.remove();
      if (focused && origin?.isConnected && !origin.disabled) origin.focus({ preventScroll: true });
    } };
    close.addEventListener('click', record.dismiss);
    toast.append(heading, close, text); region.append(toast); active.add(record);
    const duration = notificationDurations[severity];
    if (duration !== null) timer = setTimeout(record.dismiss, duration);
    return record.dismiss;
  }

  return { show, reset: key => seen.delete(key) };
}

let notifier;
export function notify(message, options) { return (notifier ??= createNotifier()).show(message, options); }
export function resetNotification(key) { notifier?.reset(key); }
