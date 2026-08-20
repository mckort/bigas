import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import AgentSidebar from './AgentSidebar'
import ActivityFeed from './ActivityFeed'
import AgentSettings from './AgentSettings'
import {
  createThread,
  fetchAgents,
  fetchFeed,
  fetchMessages,
  fetchThreads,
  pollDeployPostcheck,
  sendMessage,
} from '../lib/api'
import { logout } from '../lib/auth'

function humanizeChatContent(content) {
  const text = (content || '').trim()
  if (!text) return content || ''
  let blob = text
  if (!text.startsWith('{') && !text.startsWith('[')) {
    const idx = text.indexOf('{')
    if (idx === -1) return content
    blob = text.slice(idx).trim()
  }
  try {
    const parsed = JSON.parse(blob)
    if (parsed && typeof parsed === 'object') {
      for (const key of ['answer', 'text', 'message', 'summary', 'content', 'report']) {
        if (typeof parsed[key] === 'string' && parsed[key].trim()) return parsed[key].trim()
      }
      if (typeof parsed.error === 'string' && parsed.error.trim()) return parsed.error.trim()
    }
  } catch {
    /* keep original */
  }
  return content
}

function MessageBubble({ message, agentIcon }) {
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      {!isUser && (
        <div className="flex-shrink-0 w-9 h-9 rounded-full bg-surface border border-border flex items-center justify-center text-lg">
          {isSystem ? '⏳' : (
            <span className={(agentIcon || '').includes('<') ? 'font-mono text-[11px] font-semibold tracking-tight' : ''}>
              {agentIcon}
            </span>
          )}
        </div>
      )}
      <div
        className={`max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-3 ${
          isUser
            ? 'bg-accent text-white'
            : isSystem
              ? 'bg-bg border border-border text-muted italic'
              : 'bg-surface border border-border'
        }`}
      >
        <div className="markdown-body text-sm sm:text-base break-words">
          <ReactMarkdown>{humanizeChatContent(message.content)}</ReactMarkdown>
        </div>
      </div>
    </div>
  )
}

function TypingIndicator({ agentName, agentIcon }) {
  return (
    <div className="flex gap-3 items-end" aria-live="polite" aria-label={`${agentName} is typing`}>
      <div className="flex-shrink-0 w-9 h-9 rounded-full bg-surface border border-border flex items-center justify-center text-lg">
        <span className={(agentIcon || '').includes('<') ? 'font-mono text-[11px] font-semibold tracking-tight' : ''}>
          {agentIcon}
        </span>
      </div>
      <div className="bg-surface border border-border rounded-2xl px-4 py-3">
        <div className="flex items-center gap-1.5 h-5">
          <span className="typing-dot" />
          <span className="typing-dot" />
          <span className="typing-dot" />
        </div>
      </div>
    </div>
  )
}

function lastMessageIsInProgress(messages) {
  const last = messages[messages.length - 1]
  if (!last) return false
  if (last.role === 'system') return true
  return last.metadata?.status === 'in_progress'
}

const DEPLOY_POLL_INTERVAL_MS = 20000

function applyMessagesResponse(setMessages, setDeployPollActive, setWaitingForReply, res) {
  const next = res.messages || []
  setMessages(next)
  if (res.deploy_poll_active) {
    setDeployPollActive(true)
    setWaitingForReply(true)
  }
  return next
}
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return `client-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function messageSortKey(createdAt) {
  if (createdAt == null) return ''
  return typeof createdAt === 'string' ? createdAt : String(createdAt)
}

function mergePolledMessages(prev, incoming) {
  const serverClientIds = new Set(
    incoming
      .filter((m) => m.role === 'user' && m.metadata?.client_id)
      .map((m) => m.metadata.client_id),
  )
  const base = prev.filter(
    (m) =>
      !(
        m.role === 'user' &&
        String(m.message_id).startsWith('local-') &&
        m.metadata?.client_id &&
        serverClientIds.has(m.metadata.client_id)
      ),
  )
  const byId = new Map(base.map((m) => [m.message_id, m]))
  for (const m of incoming) {
    byId.set(m.message_id, m)
  }
  return [...byId.values()].sort((a, b) =>
    messageSortKey(a.created_at).localeCompare(messageSortKey(b.created_at)),
  )
}

export default function ChatLayout({ user, onLogout }) {
  const [agents, setAgents] = useState([])
  const [activeAgentId, setActiveAgentId] = useState('chief')
  const [threadId, setThreadId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [waitingForReply, setWaitingForReply] = useState(false)
  const [deployPollActive, setDeployPollActive] = useState(false)
  const [events, setEvents] = useState([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [activityOpen, setActivityOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const bottomRef = useRef(null)
  const lastMsgTs = useRef('')
  const lastFeedTs = useRef('')

  const activeAgent = agents.find((a) => a.agent_id === activeAgentId) || { icon: '🤖', name: 'Agent' }

  const loadAgents = useCallback(async () => {
    const res = await fetchAgents()
    setAgents(res.agents || [])
  }, [])

  const openAgentThread = useCallback(async (agentId, { cancelled } = {}) => {
    setThreadId(null)
    setMessages([])
    setWaitingForReply(false)
    setDeployPollActive(false)
    lastMsgTs.current = ''

    let thread = null
    let loaded = []
    let resumeDeployPoll = false
    const probedMessages = new Map()
    try {
      const listed = await fetchThreads()
      const candidates = (listed.threads || []).filter((t) => t.agent_id === agentId)
      // Skip empty threads left over from previous visits that always created a new one.
      // Missing message_count (older docs) is treated as maybe-resumable and probed.
      const resumable = candidates.filter((t) => (t.message_count ?? 1) > 0)
      const toProbe = resumable.slice(0, 15)
      const probeResults = await Promise.all(
        toProbe.map(async (candidate) => {
          try {
            const msgRes = await fetchMessages(candidate.thread_id)
            return {
              candidate,
              msgs: msgRes.messages || [],
              deployPollActive: Boolean(msgRes.deploy_poll_active),
              ok: true,
            }
          } catch {
            return { candidate, msgs: null, deployPollActive: false, ok: false }
          }
        })
      )
      if (cancelled?.()) return
      for (const { candidate, msgs, deployPollActive, ok } of probeResults) {
        if (ok) probedMessages.set(candidate.thread_id, { msgs, deployPollActive })
      }
      for (const candidate of toProbe) {
        const probed = probedMessages.get(candidate.thread_id)
        if (probed === undefined) continue
        if (probed.msgs.length) {
          thread = candidate
          loaded = probed.msgs
          resumeDeployPoll = probed.deployPollActive
          break
        }
      }
      if (!thread) thread = candidates[0] || null
    } catch {
      thread = null
    }
    if (cancelled?.()) return

    if (!thread) {
      try {
        const created = await createThread(agentId)
        if (cancelled?.()) return
        thread = created.thread
      } catch {
        return
      }
    }

    setThreadId(thread.thread_id)

    if (loaded.length) {
      setMessages(loaded)
      lastMsgTs.current = loaded[loaded.length - 1].created_at
      if (resumeDeployPoll) {
        setDeployPollActive(true)
        setWaitingForReply(true)
      } else {
        setWaitingForReply(lastMessageIsInProgress(loaded))
      }
      return
    }

    if (probedMessages.has(thread.thread_id)) {
      const cached = probedMessages.get(thread.thread_id)
      setMessages(cached.msgs)
      if (cached.msgs.length) lastMsgTs.current = cached.msgs[cached.msgs.length - 1].created_at
      if (cached.deployPollActive) {
        setDeployPollActive(true)
        setWaitingForReply(true)
      } else {
        setWaitingForReply(lastMessageIsInProgress(cached.msgs))
      }
      return
    }

    try {
      const msgRes = await fetchMessages(thread.thread_id)
      if (cancelled?.()) return
      const next = applyMessagesResponse(setMessages, setDeployPollActive, setWaitingForReply, msgRes)
      if (next.length) lastMsgTs.current = next[next.length - 1].created_at
      if (!msgRes.deploy_poll_active) {
        setWaitingForReply(lastMessageIsInProgress(next))
      }
    } catch {
      if (!cancelled?.()) setMessages([])
    }
  }, [])

  useEffect(() => {
    loadAgents()
  }, [loadAgents])

  useEffect(() => {
    let cancelled = false
    openAgentThread(activeAgentId, { cancelled: () => cancelled })
    return () => {
      cancelled = true
    }
  }, [activeAgentId, openAgentThread])

  const showTyping = waitingForReply || lastMessageIsInProgress(messages)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, showTyping])

  // Poll messages
  useEffect(() => {
    if (!threadId) return
    let cancelled = false

    async function poll() {
      try {
        const res = await fetchMessages(threadId, lastMsgTs.current || undefined)
        if (cancelled || !res.messages?.length) {
          if (!cancelled && res.deploy_poll_active) setDeployPollActive(true)
          return
        }
        setMessages((prev) => mergePolledMessages(prev, res.messages))
        if (res.deploy_poll_active) setDeployPollActive(true)
        const latest = res.messages[res.messages.length - 1]
        if (latest?.created_at) lastMsgTs.current = latest.created_at
        if (latest?.role === 'assistant' && latest.metadata?.status !== 'in_progress') {
          if (!res.deploy_poll_active) setWaitingForReply(false)
        }
      } catch {
        /* ignore poll errors */
      }
    }

    poll()
    const id = setInterval(poll, showTyping ? 1000 : 3000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [threadId, showTyping])

  // Client-driven DevOps deploy post-check (GitHub Actions + health)
  useEffect(() => {
    if (!threadId || !deployPollActive) return
    let cancelled = false

    async function pollDeploy() {
      try {
        const result = await pollDeployPostcheck(threadId)
        if (cancelled) return
        if (result.messages?.length) {
          setMessages((prev) => mergePolledMessages(prev, result.messages))
          const latest = result.messages[result.messages.length - 1]
          if (latest?.created_at) lastMsgTs.current = latest.created_at
        }
        if (result.status === 'complete' || !result.active) {
          setDeployPollActive(false)
          setWaitingForReply(false)
        }
      } catch {
        /* ignore deploy poll errors */
      }
    }

    pollDeploy()
    const id = setInterval(pollDeploy, DEPLOY_POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [threadId, deployPollActive])

  // Poll activity feed
  useEffect(() => {
    let cancelled = false

    async function pollFeed() {
      try {
        const res = await fetchFeed(lastFeedTs.current || undefined)
        if (cancelled || !res.events?.length) return
        setEvents((prev) => {
          const ids = new Set(prev.map((e) => e.id))
          const incoming = res.events.filter((e) => {
            if (!e?.id || ids.has(e.id)) return false
            ids.add(e.id)
            return true
          })
          const merged = [...incoming, ...prev]
          merged.sort((a, b) => {
            const timeA = a?.created_at || ''
            const timeB = b?.created_at || ''
            return timeA < timeB ? 1 : timeA > timeB ? -1 : 0
          })
          return merged.slice(0, 100)
        })
        const newestTs = res.events.reduce(
          (max, e) => ((e?.created_at || '') > max ? e.created_at : max),
          lastFeedTs.current || '',
        )
        if (newestTs) lastFeedTs.current = newestTs
      } catch {
        /* ignore */
      }
    }

    pollFeed()
    const id = setInterval(pollFeed, 5000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  async function handleSend(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || !threadId || sending) return
    setInput('')
    setSending(true)
    setWaitingForReply(true)
    const clientId = createClientMessageId()
    const optimistic = {
      message_id: `local-${clientId}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
      metadata: { client_id: clientId },
    }
    setMessages((prev) => [...prev, optimistic])
    try {
      const result = await sendMessage(threadId, text, clientId)
      const res = await fetchMessages(threadId)
      const next = applyMessagesResponse(setMessages, setDeployPollActive, setWaitingForReply, res)
      if (next.length) {
        lastMsgTs.current = next[next.length - 1].created_at
      }
      if (result.deploy_poll_active) {
        setDeployPollActive(true)
        setWaitingForReply(true)
      } else {
        const done =
          result.status !== 'in_progress' && !lastMessageIsInProgress(next) && !res.deploy_poll_active
        if (done) setWaitingForReply(false)
      }
    } catch (err) {
      setWaitingForReply(false)
      setMessages((prev) => [
        ...prev.filter((m) => m.message_id !== optimistic.message_id),
        optimistic,
        {
          message_id: `err-${Date.now()}`,
          role: 'assistant',
          content: `Error: ${err.message}`,
          created_at: new Date().toISOString(),
        },
      ])
    } finally {
      setSending(false)
    }
  }

  function handleLogout() {
    logout()
    onLogout()
  }

  return (
    <div className="h-screen flex flex-col lg:flex-row overflow-hidden">
      <AgentSidebar
        agents={agents}
        activeAgentId={activeAgentId}
        onSelectAgent={setActiveAgentId}
        onOpenSettings={() => setSettingsOpen(true)}
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <header className="flex items-center gap-3 px-4 py-3 border-b border-border bg-surface/80 backdrop-blur">
          <button
            className="lg:hidden text-xl"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open agents"
          >
            ☰
          </button>
          <span className="text-2xl">{activeAgent.icon}</span>
          <div className="flex-1 min-w-0">
            <h1 className="font-semibold truncate">{activeAgent.name}</h1>
            <p className="text-xs text-muted truncate">
              {showTyping ? `${activeAgent.name} is typing…` : user?.email}
            </p>
          </div>
          <button
            className="lg:hidden text-sm text-muted px-2"
            onClick={() => setActivityOpen(true)}
          >
            Activity
          </button>
          <button onClick={handleLogout} className="text-sm text-muted hover:text-text px-2">
            Log out
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {messages.length === 0 && !showTyping && (
            <div className="text-center text-muted py-16 px-4">
              <p className="text-4xl mb-4">{activeAgent.icon}</p>
              <p className="text-lg font-medium text-text mb-2">Chat with {activeAgent.name}</p>
              <p className="text-sm max-w-md mx-auto">
                Ask questions, request reports, or delegate tasks across your AI team.
              </p>
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.message_id} message={m} agentIcon={activeAgent.icon} />
          ))}
          {showTyping && (
            <TypingIndicator agentName={activeAgent.name} agentIcon={activeAgent.icon} />
          )}
          <div ref={bottomRef} />
        </div>

        <form onSubmit={handleSend} className="p-3 sm:p-4 border-t border-border bg-surface">
          {showTyping && (
            <p className="text-xs text-muted max-w-4xl mx-auto mb-2 px-1">
              <span className="text-accent">{activeAgent.name}</span> is typing…
            </p>
          )}
          <div className="flex gap-2 max-w-4xl mx-auto">
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={`Message ${activeAgent.name}…`}
              className="flex-1 bg-bg border border-border rounded-full px-4 py-3 text-sm sm:text-base min-w-0"
            />
            <button
              type="submit"
              disabled={sending || !input.trim()}
              className="bg-accent text-white font-semibold rounded-full px-5 py-3 disabled:opacity-40 flex-shrink-0"
            >
              {sending ? '…' : '↑'}
            </button>
          </div>
        </form>
      </main>

      <ActivityFeed events={events} open={activityOpen} onClose={() => setActivityOpen(false)} />

      <AgentSettings open={settingsOpen} onClose={() => { setSettingsOpen(false); loadAgents() }} />
    </div>
  )
}
