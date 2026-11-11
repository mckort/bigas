// Optional areas switched by BIGAS_FEATURES on the server (see bigas/features.py).
// Every area is on until /api/auth/config says otherwise.
let features = { marketing: true, objectives: true, outreach: true }

export function setFeatures(next) {
  if (next && typeof next === 'object') features = { ...features, ...next }
}

export function featureEnabled(name) {
  return features[name] !== false
}
