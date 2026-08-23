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

export async function sendMessage(threadId, content, clientId) {
  const body = { content }
  if (clientId) body.client_id = clientId
  return apiFetch(`/api/chat/threads/${threadId}/messages`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function pollDeployPostcheck(threadId) {
  return apiFetch(`/api/chat/threads/${threadId}/deploy-poll`, { method: 'POST' })
}

export async function fetchFeed(since) {
  const qs = since ? `?since=${encodeURIComponent(since)}` : ''
  return apiFetch(`/api/feed${qs}`)
}

export async function approveProposal(proposalId, messageId, actionId, extra = {}) {
  return apiFetch(`/api/v1/chat/proposals/${encodeURIComponent(proposalId)}/approve`, {
    method: 'POST',
    body: JSON.stringify({ message_id: messageId, action_id: actionId, ...extra }),
  })
}

export async function rejectProposal(proposalId, messageId) {
  return apiFetch(`/api/v1/chat/proposals/${encodeURIComponent(proposalId)}/reject`, {
    method: 'POST',
    body: JSON.stringify({ message_id: messageId }),
  })
}

export async function transitionJiraIssue(issueKey) {
  return apiFetch('/api/jira/transition', {
    method: 'POST',
    body: JSON.stringify({ issue_key: issueKey }),
  })
}

export async function fetchBoards() {
  return apiFetch('/api/boards')
}

export async function createBoard(payload) {
  return apiFetch('/api/boards', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function deleteBoard(boardId) {
  return apiFetch(`/api/boards/${boardId}`, { method: 'DELETE' })
}

export async function fetchBoardTickets(boardId) {
  return apiFetch(`/api/boards/${boardId}/tickets`)
}

export async function createTicket(boardId, payload) {
  return apiFetch(`/api/boards/${boardId}/tickets`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchTicketByKey(key) {
  return apiFetch(`/api/tickets/by-key/${encodeURIComponent(key)}`)
}

export async function fetchTicket(ticketId) {
  return apiFetch(`/api/tickets/${ticketId}`)
}

export async function addTicketComment(ticketId, body) {
  return apiFetch(`/api/tickets/${ticketId}/comments`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  })
}

export async function updateTicket(ticketId, payload) {
  return apiFetch(`/api/tickets/${ticketId}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function deleteTicket(ticketId) {
  return apiFetch(`/api/tickets/${ticketId}`, { method: 'DELETE' })
}
