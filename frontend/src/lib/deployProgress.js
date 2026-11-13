const CONFIRM_RE = /reply\s+\*{0,2}yes\*{0,2}/i

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
    .map((message) => ({
      id: message.message_id || `${message.created_at}:${message.role}`,
      text: String(message.content).trim(),
      status: message.metadata?.status || '',
    }))
}

export function deployRunPhase({ pendingSend = false, error = '', messages = [], deployPoll = false } = {}) {
  if (error) return 'error'
  const last = [...(messages || [])].reverse().find((message) => {
    if (!message || (message.role !== 'assistant' && message.role !== 'system')) return false
    return Boolean(String(message.content || '').trim())
  })
  if (!pendingSend && last && CONFIRM_RE.test(String(last.content || ''))) return 'confirm'
  if (pendingSend || deployPoll || !last || last.metadata?.status === 'in_progress') return 'working'
  return 'done'
}

export function applyDeployUpdate(run, incoming, { pendingSend = false, error = '', deployPoll = false } = {}) {
  const messages = mergeDeployMessages(
    run?.messages,
    messagesForDeploy(incoming, { since: run?.since, prompt: run?.prompt }),
  )
  return {
    phase: deployRunPhase({ pendingSend, error, messages, deployPoll }),
    pendingSend: Boolean(pendingSend),
    threadId: run?.threadId || '',
    since: run?.since || '',
    prompt: run?.prompt || '',
    messages,
    error: error || '',
    deployPoll: Boolean(deployPoll) && !error,
    token: run?.token,
  }
}
