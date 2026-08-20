import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import AgentSidebar from './AgentSidebar'
import ActivityFeed from './ActivityFeed'
import AgentSettings from './AgentSettings'
import {
  approveProposal,
  createThread,
  fetchAgents,
  fetchFeed,
  fetchMessages,
  fetchThreads,
  pollDeployPostcheck,
  rejectProposal,
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

function ActionProposalCard({ message, onResolved }) {
  const [resolving, setResolving] = useState(false)
  const meta = message.metadata || {}
  if (meta.type !== 'action_proposal' || meta.status !== 'pending') return null

  const actions = meta.actions || []
  const proposalId = meta.proposal_id

  async function handleApprove(actionId) {
    if (resolving || !proposalId) return
    setResolving(true)
    try {
      await approveProposal(proposalId, message.message_id, actionId)
      onResolved()
    } catch (err) {
      alert(err.message || 'Failed to approve action')
    } finally {
      setResolving(false)
    }
  }

  async function handleReject() {
    if (resolving || !proposalId) return
    setResolving(true)
    try {
      await rejectProposal(proposalId, message.message_id)
      onResolved()
    } catch (err) {
      alert(err.message || 'Failed to reject proposal')
    } finally {
      setResolving(false)
    }
  }

  return (
    <div className="mt-3 pt-3 border-t border-black/10">
      <p className="text-xs text-muted mb-2 font-medium">Suggested actions</p>
      <div className="flex flex-col sm:flex-row sm:flex-wrap gap-2">
        {actions.map((action) => (
          <button
            key={action.id}
            type="button"
            disabled={resolving}
            onClick={() => handleApprove(action.id)}
            className="bg-bigas-black text-white rounded-full px-4 py-2 text-sm font-medium disabled:opacity-50 w-full sm:w-auto text-center min-h-[44px] hover:opacity-90 transition-opacity"
          >
            {action.label}
          </button>
        ))}
        <button
          type="button"
          disabled={resolving}
          onClick={handleReject}
          className="border border-border rounded-full px-4 py-2 text-sm text-muted hover:text-text disabled:opacity-50 w-full sm:w-auto text-center min-h-[44px] bg-white transition-colors"
        >
          Reject all
        </button>
      </div>
    </div>
  )
}

function MessageBubble({ message, agentIcon, onProposalResolved }) {
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'
  const meta = message.metadata || {}
  const showResolved =
    meta.type === 'action_proposal' && meta.status && meta.status !== 'pending'

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      {!isUser && (
        <div className="flex-shrink-0 w-9 h-9 rounded-xl bg-white border border-border flex items-center justify-center shadow-soft">
          {isSystem ? (
            <span className="text-sm">⏳</span>
          ) : (
            <span className={(agentIcon || '').includes('<') ? 'font-mono text-[11px] font-semibold tracking-tight' : 'text-lg'}>
              {agentIcon}
            </span>
          )}
        </div>
      )}
      <div
        className={`max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-3 ${
          isUser
            ? 'bg-bigas-blue text-bigas-black shadow-soft'
            : isSystem
              ? 'bg-surface border border-border text-muted italic'
              : 'bg-white border border-border text-text shadow-soft'
        }`}
      >
        <div className="markdown-body text-sm sm:text-base break-words">
          <ReactMarkdown>{humanizeChatContent(message.content)}</ReactMarkdown>
        </div>
        <ActionProposalCard message={message} onResolved={onProposalResolved} />
        {showResolved && (
          <p className="mt-2 text-xs text-muted capitalize">{meta.status}</p>
        )}
      </div>
    </div>
  )
}

function TypingIndicator({ agentName, agentIcon }) {
  return (
    <div className="flex gap-3 items-end" aria-live="polite" aria-label={`${agentName} is typing`}>
      <div className="flex-shrink-0 w-9 h-9 rounded-xl bg-white border border-border flex items-center justify-center shadow-soft">
        <span className={(agentIcon || '').includes('<') ? 'font-mono text-[11px] font-semibold tracking-tight' : 'text-lg'}>
          {agentIcon}
        </span>
      </div>
      <div className="bg-white border border-border rounded-2xl px-4 py-3 shadow-soft">
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

function createClientMessageId() {
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

function MobileAgentTabs({ agents, activeAgentId, onSelectAgent }) {
  if (!agents.length) return null
  return (
    <div className="lg:hidden border-b border-border bg-white">
      <div className="flex gap-2 overflow-x-auto scrollbar-hide px-3 py-2">
        {agents.map((agent) => {
          const isActive = activeAgentId === agent.agent_id
          return (
            <button
              key={agent.agent_id}
              type="button"
              onClick={() => onSelectAgent(agent.agent_id)}
              className={`flex-shrink-0 flex items-center gap-2 px-3 py-2 rounded-full text-sm font-medium min-h-[44px] transition-colors ${
                isActive
                  ? 'bg-bigas-blue text-bigas-black border border-black/10'
                  : 'bg-surface text-text border border-transparent'
              }`}
            >
              <span className={(agent.icon || '').includes('<') ? 'font-mono text-[10px] font-semibold' : ''}>
                {agent.icon || '🤖'}
              </span>
              <span className="whitespace-nowrap">{agent.name}</span>
            </button>
          )
        })}
      </div>
    </div>
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

  async function refreshMessages() {
    if (!threadId) return
    try {
      const res = await fetchMessages(threadId)
      const next = res.messages || []
      setMessages(next)
      if (next.length) lastMsgTs.current = next[next.length - 1].created_at
    } catch {
      /* ignore */
    }
  }

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
    <div className="h-screen flex flex-col lg:flex-row overflow-hidden bg-bg">
      <AgentSidebar
        agents={agents}
        activeAgentId={activeAgentId}
        onSelectAgent={setActiveAgentId}
        onOpenSettings={() => setSettingsOpen(true)}
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="flex-1 flex flex-col min-w-0 bg-bg">
        <header className="flex items-center gap-3 px-4 py-3 border-b border-border bg-white/90 backdrop-blur-sm sticky top-0 z-10">
          <button
            type="button"
            className="lg:hidden text-xl min-w-[44px] min-h-[44px] flex items-center justify-center -ml-2 hover:bg-surface rounded-xl transition-colors"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open agents"
          >
            ☰
          </button>
          <div className="flex items-center gap-3 flex-1 min-w-0">
            <div className="flex-shrink-0 w-10 h-10 rounded-xl bg-bigas-blue flex items-center justify-center border border-black/10">
              <span className={(activeAgent.icon || '').includes('<') ? 'font-mono text-[11px] font-semibold tracking-tight' : 'text-xl'}>
                {activeAgent.icon}
              </span>
            </div>
            <div className="min-w-0">
              <h1 className="font-semibold truncate text-base">{activeAgent.name}</h1>
              <p className="text-xs text-muted truncate">
                {showTyping ? (
                  <span>
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-bigas-blue mr-1.5 align-middle" />
                    Typing…
                  </span>
                ) : (
                  user?.email
                )}
              </p>
            </div>
          </div>
          <button
            type="button"
            className="lg:hidden text-sm text-muted hover:text-text px-3 py-2 rounded-lg hover:bg-surface min-h-[44px] transition-colors"
            onClick={() => setActivityOpen(true)}
          >
            Activity
          </button>
          <button
            type="button"
            onClick={handleLogout}
            className="text-sm text-muted hover:text-text px-3 py-2 rounded-lg hover:bg-surface min-h-[44px] transition-colors"
          >
            Log out
          </button>
        </header>

        <MobileAgentTabs
          agents={agents}
          activeAgentId={activeAgentId}
          onSelectAgent={setActiveAgentId}
        />

        <div className="flex-1 overflow-y-auto scrollbar-thin">
          <div className="max-w-3xl mx-auto w-full px-4 py-6 space-y-5">
            {messages.length === 0 && !showTyping && (
              <div className="text-center py-12 sm:py-20 px-4">
                <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-bigas-blue border border-black/10 mb-5 text-3xl shadow-soft">
                  {activeAgent.icon}
                </div>
                <p className="text-xl font-semibold text-text mb-2">Chat with {activeAgent.name}</p>
                <p className="text-sm text-muted max-w-md mx-auto leading-relaxed">
                  Ask questions, request reports, or delegate tasks across your AI team.
                </p>
              </div>
            )}
            {messages.map((m) => (
              <MessageBubble
                key={m.message_id}
                message={m}
                agentIcon={activeAgent.icon}
                onProposalResolved={refreshMessages}
              />
            ))}
            {showTyping && (
              <TypingIndicator agentName={activeAgent.name} agentIcon={activeAgent.icon} />
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-border bg-white/95 backdrop-blur-sm px-3 sm:px-4 py-3 sm:py-4">
          <form onSubmit={handleSend} className="max-w-3xl mx-auto">
            <div className="flex gap-2 items-end bg-white border border-border rounded-2xl shadow-input px-3 py-2 sm:px-4 sm:py-2.5 focus-within:ring-2 focus-within:ring-bigas-blue/40 transition-shadow">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={`Message ${activeAgent.name}…`}
                className="flex-1 bg-transparent border-0 px-1 py-2 text-sm sm:text-base min-w-0 focus:outline-none placeholder:text-muted"
                aria-label={`Message ${activeAgent.name}`}
              />
              <button
                type="submit"
                disabled={sending || !input.trim()}
                className="bg-bigas-black text-white font-semibold rounded-xl w-11 h-11 sm:w-12 sm:h-12 disabled:opacity-30 flex-shrink-0 flex items-center justify-center hover:opacity-90 transition-opacity"
                aria-label="Send message"
              >
                {sending ? (
                  <span className="text-lg">…</span>
                ) : (
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M12 19V5M12 5L5 12M12 5L19 12" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </button>
            </div>
            <p className="text-[11px] text-muted text-center mt-2 hidden sm:block">
              Bigas specialists use the same tools as Discord and scheduled workflows.
            </p>
          </form>
        </div>
      </main>

      <ActivityFeed events={events} open={activityOpen} onClose={() => setActivityOpen(false)} />

      <AgentSettings open={settingsOpen} onClose={() => { setSettingsOpen(false); loadAgents() }} />
    </div>
  )
}
