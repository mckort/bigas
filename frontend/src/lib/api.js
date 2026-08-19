const TOKEN_KEY = 'bigas_chat_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

export async function apiFetch(path, options = {}) {
  const token = getToken()
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(path, { ...options, headers })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.error || `Request failed (${res.status})`)
  }
  return data
}

export async function fetchAuthConfig() {
  return apiFetch('/api/auth/config')
}

export async function verifyAuth() {
  return apiFetch('/api/auth/verify', { method: 'POST' })
}

export async function fetchAgents() {
  return apiFetch('/api/agents')
}

export async function updateAgent(agentId, payload) {
  return apiFetch(`/api/agents/${agentId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function createThread(agentId) {
  return apiFetch('/api/chat/threads', {
    method: 'POST',
    body: JSON.stringify({ agent_id: agentId }),
  })
}

export async function fetchThreads() {
  return apiFetch('/api/chat/threads')
}

export async function fetchMessages(threadId, since) {
  const qs = since ? `?since=${encodeURIComponent(since)}` : ''
  return apiFetch(`/api/chat/threads/${threadId}/messages${qs}`)
}

export async function sendMessage(threadId, content) {
  return apiFetch(`/api/chat/threads/${threadId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}

export async function fetchFeed(since) {
  const qs = since ? `?since=${encodeURIComponent(since)}` : ''
  return apiFetch(`/api/feed${qs}`)
}
