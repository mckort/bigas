const CONFIRM_RE = /reply\s+\*{0,2}yes\*{0,2}/i

/** owner/repo (lowercase) → project key — keep in sync with portfolio DEFAULT_PROJECT_REPOS. */
const REPO_TO_PROJECT = {
  'mckort/vcfieldassistant': 'VFA',
  'mckort/roadpal': 'WAYW',
  'mckort/bigas': 'BIG',
  'mckort/remotebrief': 'REM',
  'green-promo-wear-global/greenpromowear-website': 'GPWW',
  'green-promo-wear-global/gpw': 'GPW-PROD',
  'mckort/multlyourdreamadventure': 'FYDA',
  'mckort/mylifesdeed': 'MYL',
  'mckort/frimaninvestments': 'FRI',
  'mckort/auralearn': 'AURA',
  'mckort/pass-or-fund': 'POF',
}

const DEPLOY_HEADER_RE =
  /\*\*Deploy\s+([A-Z][A-Z0-9-]+?)(?:\s+[\d.]+)?\*\*|^🚀\s*\*\*Deploy\s+([A-Z][A-Z0-9-]+?)(?:\s+[\d.]+)?:/im
/** ``Prepare AuraLearn 0.7.0`` / ``Prepare VFA 0.1.0:`` inside bold markers. */
const PREPARE_HEADER_RE = /\*\*Prepare\s+(.+?)\s+[\d.]+:?\*\*/i
const USER_COMMAND_RE =
  /^(?:prepare\s+deploy|deploy)\s+([A-Z][A-Z0-9-]+)\b|^(?:yes|no)\s+([A-Z][A-Z0-9-]+)\s*$/i
const REPO_MENTION_RE = /\b([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)\b/g

const BRAND_TO_PROJECT = {
  auralearn: 'AURA',
  'aura learn': 'AURA',
  bigas: 'BIG',
  'vc field assistant': 'VFA',
  vcfieldassistant: 'VFA',
  roadpal: 'WAYW',
  remotebrief: 'REM',
  greenpromowear: 'GPWW',
  'green promo wear': 'GPWW',
  'green promo wear store': 'GPW-PROD',
  'pass or fund': 'POF',
  'pass-or-fund': 'POF',
  'friman investments': 'FRI',
  "my life's deed": 'MYL',
  'fulfill your dream adventure': 'FYDA',
}

const KNOWN_PROJECT_KEYS = new Set([
  ...Object.values(REPO_TO_PROJECT),
  ...Object.values(BRAND_TO_PROJECT),
])

function normalizeProjectLabel(label) {
  return String(label || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

/** One lookup for project keys and multi-word brand names. */
const LABEL_TO_PROJECT = new Map()
for (const key of KNOWN_PROJECT_KEYS) {
  LABEL_TO_PROJECT.set(normalizeProjectLabel(key), key)
}
for (const [label, key] of Object.entries(BRAND_TO_PROJECT)) {
  LABEL_TO_PROJECT.set(normalizeProjectLabel(label), key)
}

function projectKeyFromLabel(label) {
  return LABEL_TO_PROJECT.get(normalizeProjectLabel(label)) || ''
}

function at(value) {
  const time = new Date(value || 0).getTime()
  return Number.isNaN(time) ? 0 : time
}

export function messagesSince(messages, since) {
  const cutoff = since ? at(since) : 0
  return (messages || []).filter((message) => {
    if (!message) return false
    if (!cutoff) return true
    const created = at(message.created_at)
    return created > 0 && created >= cutoff
  })
}

/** Keep this click's thread, starting at the command the user just sent. */
export function messagesForDeploy(incoming, { since, prompt } = {}) {
  const list = incoming || []
  const wanted = String(prompt || '').trim()
  if (wanted) {
    const mine = [...list].reverse().find(
      (message) => message?.role === 'user' && String(message.content || '').trim() === wanted,
    )
    if (mine?.created_at) {
      const cutoff = at(mine.created_at)
      return list.filter((message) => message && at(message.created_at) >= cutoff)
    }
  }
  return messagesSince(list, since)
}

function metaProjectKey(message) {
  return String(message?.metadata?.project_key || '')
    .trim()
    .toUpperCase()
}

/** Infer a project key from message text when metadata is missing (legacy posts). */
export function inferProjectKeyFromMessage(message) {
  const meta = metaProjectKey(message)
  if (meta) return meta

  const text = String(message?.content || '')
  if (!text.trim()) return ''

  if (message?.role === 'user') {
    const userMatch = text.trim().match(USER_COMMAND_RE)
    if (userMatch) return String(userMatch[1] || userMatch[2] || '').toUpperCase()
  }

  const deploy = text.match(DEPLOY_HEADER_RE)
  if (deploy) {
    const key = String(deploy[1] || deploy[2] || '').toUpperCase()
    if (KNOWN_PROJECT_KEYS.has(key)) return key
  }

  const prepare = text.match(PREPARE_HEADER_RE)
  if (prepare) {
    const key = projectKeyFromLabel(prepare[1])
    if (key) return key
  }

  let found = ''
  for (const match of text.matchAll(REPO_MENTION_RE)) {
    const repo = String(match[1] || '').toLowerCase()
    const key = REPO_TO_PROJECT[repo]
    if (key) {
      // Prefer product repos over shared infra (gcp-single-vm-webstack is not mapped).
      found = key
      break
    }
  }
  return found
}

/**
 * Shared DevOps thread can interleave projects. Keep only this ship's run.
 * Prefers metadata.project_key; falls back to content cues and active-segment tracking.
 */
export function filterMessagesForProject(messages, projectKey) {
  const key = String(projectKey || '')
    .trim()
    .toUpperCase()
  if (!key) return messages || []

  let active = ''
  const out = []
  for (const message of messages || []) {
    if (!message) continue
    const inferred = inferProjectKeyFromMessage(message)
    if (inferred) {
      active = inferred
      if (inferred === key) out.push(message)
      continue
    }
    if (active === key) out.push(message)
  }
  return out
}

export function mergeDeployMessages(previous, incoming) {
  const byId = new Map()
  for (const message of [...(previous || []), ...(incoming || [])]) {
    if (!message) continue
    const id = message.message_id || `${message.created_at}:${message.role}:${message.content}`
    byId.set(id, message)
  }
  return [...byId.values()].sort((a, b) => at(a.created_at) - at(b.created_at))
}

export function deployProgressLines(messages) {
  return (messages || [])
    .filter((message) => {
      if (!message) return false
      if (message.role !== 'assistant' && message.role !== 'system') return false
      return Boolean(String(message.content || '').trim())
    })
    .map((message, index) => ({
      id: message.message_id || `${message.created_at}:${message.role}:${index}`,
      text: String(message.content).trim(),
      status: message.metadata?.status || '',
    }))
}

/** A user message after the confirmation question, by order or by timestamp. */
function userRepliedAfter(messages, last) {
  const list = messages || []
  const lastIndex = list.indexOf(last)
  const lastAt = at(last?.created_at)
  return list.some((message, index) => {
    if (!message || message.role !== 'user') return false
    if (lastIndex >= 0 && index > lastIndex) return true
    const created = at(message.created_at)
    return created > 0 && lastAt > 0 && created >= lastAt
  })
}

export function deployRunPhase({ pendingSend = false, error = '', messages = [], deployPoll = false } = {}) {
  if (error) return 'error'
  const list = messages || []
  const last = [...list].reverse().find((message) => {
    if (!message || (message.role !== 'assistant' && message.role !== 'system')) return false
    return Boolean(String(message.content || '').trim())
  })
  const asking = Boolean(last && CONFIRM_RE.test(String(last.content || '')))
  const replied = Boolean(asking && userRepliedAfter(list, last))
  if (!pendingSend && asking && !replied) return 'confirm'
  if (pendingSend || deployPoll || replied || !last || last.metadata?.status === 'in_progress') return 'working'
  return 'done'
}

export function applyDeployUpdate(run, incoming, { pendingSend = false, error = '', deployPoll = false } = {}) {
  const projectKey = String(run?.projectKey || '').trim().toUpperCase()
  const windowed = messagesForDeploy(incoming, { since: run?.since, prompt: run?.prompt })
  const messages = filterMessagesForProject(
    mergeDeployMessages(run?.messages, windowed),
    projectKey,
  )
  return {
    phase: deployRunPhase({ pendingSend, error, messages, deployPoll }),
    pendingSend: Boolean(pendingSend),
    threadId: run?.threadId || '',
    since: run?.since || '',
    prompt: run?.prompt || '',
    projectKey,
    messages,
    error: error || '',
    deployPoll: Boolean(deployPoll) && !error,
    token: run?.token,
  }
}

/** Prefer primary_prompt when the ship card sets one (staging Update vs Prepare). */
export function commandForShipRow(row) {
  const commands = Array.isArray(row?.commands) ? row.commands : []
  if (!commands.length) return null
  const wanted = String(row?.primary_prompt || '').trim()
  if (wanted) {
    const match = commands.find((c) => String(c?.prompt || '').trim() === wanted)
    if (match) return match
  }
  return commands[0]
}

export function pipelineForProject(working, projectKey) {
  const key = String(projectKey || '').trim().toUpperCase()
  if (!key) return null
  return (
    (Array.isArray(working) ? working : []).find(
      (item) =>
        item?.type === 'pipeline' && String(item.project_key || '').trim().toUpperCase() === key,
    ) || null
  )
}

export function emptyDeployRun({
  threadId = '',
  since = '',
  prompt = '',
  projectKey = '',
  token,
  deployPoll = true,
} = {}) {
  return {
    phase: 'working',
    pendingSend: false,
    threadId: threadId || '',
    // Blank since means the caller has no start time. A synthetic window would
    // hide earlier steps of a deploy that is already running.
    since: since || '',
    prompt: prompt || '',
    projectKey: String(projectKey || '').trim().toUpperCase(),
    messages: [],
    error: '',
    deployPoll: Boolean(deployPoll),
    token,
  }
}

/** True while the cockpit still tracks an in-flight ship command for this project. */
export function isTrackedShipRun(run, projectKey) {
  const key = String(projectKey || '').trim().toUpperCase()
  if (!run || !key) return false
  if (String(run.projectKey || '').trim().toUpperCase() !== key) return false
  return run.phase === 'working' || run.phase === 'confirm'
}

/**
 * Show progress may reopen the in-memory run only for the same ship on the
 * same DevOps thread. A shared thread alone is not enough — otherwise another
 * card would show VFA (etc.) steps under its own title.
 */
export function canReuseShipProgressRun(run, projectKey, threadId) {
  if (!threadId || !isTrackedShipRun(run, projectKey)) return false
  return String(run?.threadId || '') === String(threadId)
}

/** Confirm/cancel from a ship card always names the project (shared DevOps thread). */
export function shipConfirmPrompt(projectKey, yes) {
  const key = String(projectKey || '')
    .trim()
    .toUpperCase()
  const verb = yes ? 'yes' : 'no'
  return key ? `${verb} ${key}` : verb
}
