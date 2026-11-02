export function measurementSuffix(value) {
  const sample = value?.last_measurement;
  if (sample && Number.isFinite(sample.value)) return `(${sample.value.toFixed(2)} ${sample.metric === 'decode_tps' ? 'decode' : 'end-to-end'} TPS)`;
  return value?.measurements_available === false ? '(measurement unavailable)' : '(unmeasured)';
}

export function measurementTitle(value) {
  const sample = value?.last_measurement;
  return sample ? `Latest saved benchmark: ${new Date(sample.time * 1000).toLocaleString()}. Context: ${sample.context_tokens ?? 'unknown'}. Historical result; settings may differ from the current deployment.` : 'No matching saved benchmark for this backend and exact model identity.';
}
