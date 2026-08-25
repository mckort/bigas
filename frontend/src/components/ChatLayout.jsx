import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import AgentSidebar, { UnreadDot } from './AgentSidebar'
import ActivityFeed from './ActivityFeed'
import { SettingsButton } from './AgentSettings'
import {
  approveProposal,
  createThread,
  fetchAgents,
  fetchChatAttachmentBlob,
  fetchFeed,
  fetchMessages,
  fetchThreads,
  pollDeployPostcheck,
  rejectProposal,
  sendMessage,
  transitionJiraIssue,
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

function parseBigasJiraTransitionHref(href) {
  if (!href || !href.startsWith('bigas://action/jira_transition')) return null
  try {
    const url = new URL(href.replace('bigas://', 'https://bigas.local/'))
    const issue = url.searchParams.get('issue')
    return issue ? issue.trim() : null
  } catch {
    return null
  }
}

function linkChildText(children) {
  if (typeof children === 'string') return children
  if (Array.isArray(children)) return children.map((c) => (typeof c === 'string' ? c : '')).join('')
  return 'Move to next column'
}

function JiraTransitionButton({ issueKey, label }) {
  const [state, setState] = useState('idle')
  const [statusLabel, setStatusLabel] = useState('')

  async function handleClick(e) {
    e.preventDefault()
    if (state === 'loading' || state === 'success') return
    setState('loading')
    try {
      const result = await transitionJiraIssue(issueKey)
      if (result.error || result.success === false) {
        throw new Error(result.error || 'Failed to move issue')
      }
      setStatusLabel(result.new_status || '')
      setState('success')
    } catch (err) {
      setState('idle')
      alert(err.message || 'Failed to move issue')
    }
  }

  let text = label || 'Move to next column'
  if (state === 'loading') text = 'Moving…'
  if (state === 'success') text = statusLabel ? `Moved to ${statusLabel}` : 'Moved'

  return (
    <span className="inline-block mt-2">
      <button
        type="button"
        onClick={handleClick}
        disabled={state === 'loading' || state === 'success'}
        className="inline-flex items-center justify-center bg-bigas-black text-white rounded-full px-4 py-2 text-sm font-medium disabled:opacity-50 min-h-[44px] max-w-full break-words hover:opacity-90 transition-opacity"
      >
        {text}
      </button>
    </span>
  )
}

const chatMarkdownComponents = {
  a({ href, children }) {
    const issueKey = parseBigasJiraTransitionHref(href)
    if (issueKey) {
      return <JiraTransitionButton issueKey={issueKey} label={linkChildText(children)} />
    }
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="text-bigas-black underline underline-offset-2 hover:opacity-70 break-all"
      >
        {children}
      </a>
    )
  },
}

function ChatMarkdown({ content }) {
  return (
    <ReactMarkdown components={chatMarkdownComponents}>
      {humanizeChatContent(content)}
    </ReactMarkdown>
  )
}

function ActionProposalCard({ message, onResolved }) {
  const [resolving, setResolving] = useState(false)
  const meta = message.metadata || {}
  const actions = meta.actions || []
  const draftActions = actions.filter((action) => action.kind === 'draft_reply')
  const otherActions = actions.filter((action) => action.kind !== 'draft_reply')
  const [drafts, setDrafts] = useState(() => {
    const initial = {}
    for (const action of draftActions) {
      initial[action.id] = (action.params && action.params.text) || ''
    }
    return initial
  })

  if (meta.type !== 'action_proposal' || meta.status !== 'pending') return null

  const proposalId = meta.proposal_id

  async function handleApprove(actionId, extra = {}) {
    if (resolving || !proposalId) return
    setResolving(true)
    try {
      await approveProposal(proposalId, message.message_id, actionId, extra)
      onResolved()
    } catch (err) {
      alert(err.message || 'Failed to approve action')
      setResolving(false)
      return
    }
    setResolving(false)
  }

  async function handleReject() {
    if (resolving || !proposalId) return
    setResolving(true)
    try {
      await rejectProposal(proposalId, message.message_id)
      onResolved()
    } catch (err) {
      alert(err.message || 'Failed to reject proposal')
      setResolving(false)
      return
    }
    setResolving(false)
  }

  return (
    <div className="mt-3 pt-3 border-t border-black/10 space-y-3">
      {draftActions.map((action) => (
        <div key={action.id}>
          <p className="text-xs text-muted mb-2 font-medium">Suggested reply — edit before sending</p>
          <textarea
            value={drafts[action.id] ?? ''}
            onChange={(e) => setDrafts((prev) => ({ ...prev, [action.id]: e.target.value }))}
            disabled={resolving}
            rows={8}
            className="w-full rounded-xl border border-border bg-white px-3 py-2 text-sm text-text resize-y min-h-[8rem] disabled:opacity-50"
          />
          <div className="mt-2 flex flex-col sm:flex-row sm:flex-wrap gap-2">
            <button
              type="button"
              disabled={resolving || !(drafts[action.id] || '').trim()}
              onClick={() => handleApprove(action.id, { text: drafts[action.id] })}
              className="bg-bigas-black text-white rounded-full px-4 py-2 text-sm font-medium disabled:opacity-50 w-full sm:w-auto text-center min-h-[44px] hover:opacity-90 transition-opacity"
            >
              Send
            </button>
          </div>
        </div>
      ))}
      {draftActions.length > 0 && otherActions.length === 0 && (
        <div className="flex flex-col sm:flex-row sm:flex-wrap gap-2">
          <button
            type="button"
            disabled={resolving}
            onClick={handleReject}
            className="border border-border rounded-full px-4 py-2 text-sm text-muted hover:text-text disabled:opacity-50 w-full sm:w-auto text-center min-h-[44px] bg-white transition-colors"
          >
            Reject
          </button>
        </div>
      )}
      {otherActions.length > 0 && (
        <>
          <p className="text-xs text-muted font-medium">Suggested actions</p>
          <div className="flex flex-col sm:flex-row sm:flex-wrap gap-2">
            {otherActions.map((action) => (
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
        </>
      )}
      {draftActions.length === 0 && otherActions.length === 0 && (
        <button
          type="button"
          disabled={resolving}
          onClick={handleReject}
          className="border border-border rounded-full px-4 py-2 text-sm text-muted hover:text-text disabled:opacity-50 w-full sm:w-auto text-center min-h-[44px] bg-white transition-colors"
        >
          Reject all
        </button>
      )}
    </div>
  )
}

function EmailTriageBody({ message }) {
  const meta = message.metadata || {}
  const body = (meta.email_body || '').trim()
  if (!body) {
    return <ChatMarkdown content={message.content} />
  }
  return (
    <div className="text-sm sm:text-base break-words">
      <p className="font-medium">📬 Email triage — {meta.email_subject || 'Email'}</p>
      <p className="mt-2 text-muted">From: {meta.email_from}</p>
      <p className="text-muted">Subject: {meta.email_subject}</p>
      <pre className="mt-3 whitespace-pre-wrap font-sans text-sm sm:text-base">{body}</pre>
    </div>
  )
}

const CHAT_ATTACHMENT_ACCEPT =
  'image/png,image/jpeg,image/webp,image/gif,application/pdf,text/plain,text/markdown,text/csv,application/json,.md'
const MAX_CHAT_ATTACHMENTS = 5

function pendingFileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`
}

function isImageAttachment(attachment) {
  return String(attachment?.content_type || attachment?.type || '').startsWith('image/')
}

function formatAttachmentSize(bytes) {
  const size = Number(bytes) || 0
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function LocalChatImagePreview({ file }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    if (!file?.type?.startsWith('image/')) return undefined
    const objectUrl = URL.createObjectURL(file)
    setUrl(objectUrl)
    return () => URL.revokeObjectURL(objectUrl)
  }, [file])
  if (!url) return null
  return (
    <img
      src={url}
      alt={file.name}
      className="mt-2 max-h-40 w-full object-contain rounded-lg bg-white/70"
    />
  )
}

function ChatAttachmentDownload({ threadId, attachment }) {
  const [downloading, setDownloading] = useState(false)

  async function handleDownload() {
    if (!threadId || !attachment?.id || downloading) return
    setDownloading(true)
    try {
      const blob = await fetchChatAttachmentBlob(threadId, attachment.id)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = attachment.filename || 'attachment'
      link.click()
      URL.revokeObjectURL(url)
    } catch {
      /* ignore download errors */
    } finally {
      setDownloading(false)
    }
  }

  return (
    <button
      type="button"
      onClick={handleDownload}
      disabled={downloading}
      className="mt-1 text-xs underline underline-offset-2 hover:opacity-70 disabled:opacity-50"
    >
      {downloading ? 'Downloading…' : 'Download'}
    </button>
  )
}

function ChatAttachmentPreview({ threadId, attachment }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    if (!threadId || !attachment?.id || !isImageAttachment(attachment)) return undefined
    let objectUrl = ''
    let cancelled = false
    fetchChatAttachmentBlob(threadId, attachment.id)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
      .catch(() => {
        if (!cancelled) setUrl('')
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [threadId, attachment?.id, attachment?.content_type])
  if (!url) return null
  return (
    <img
      src={url}
      alt={attachment.filename}
      className="mt-2 max-h-48 w-full object-contain rounded-lg bg-white/70"
    />
  )
}

function MessageAttachments({ message, threadId }) {
  const attachments = message?.metadata?.attachments || []
  const localFiles = message?.metadata?.local_files || []
  if (!attachments.length && !localFiles.length) return null
  return (
    <div className="mt-2 space-y-2">
      {localFiles.map((file) => (
        <div key={pendingFileKey(file)} className="rounded-xl px-3 py-2 bg-white/40 text-sm">
          <p className="font-medium truncate">{file.name}</p>
          <LocalChatImagePreview file={file} />
        </div>
      ))}
      {attachments.map((attachment) => (
        <div key={attachment.id} className="rounded-xl px-3 py-2 bg-white/40 text-sm">
          <p className="font-medium truncate">{attachment.filename}</p>
          <p className="text-[11px] opacity-70">{formatAttachmentSize(attachment.size_bytes)}</p>
          {isImageAttachment(attachment) ? (
            <ChatAttachmentPreview threadId={threadId} attachment={attachment} />
          ) : (
            <ChatAttachmentDownload threadId={threadId} attachment={attachment} />
          )}
        </div>
      ))}
    </div>
  )
}

function MessageBubble({ message, agentIcon, onProposalResolved, threadId }) {
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'
  const meta = message.metadata || {}
  const isHandoff = meta.type === 'handoff'
  const showResolved =
    meta.type === 'action_proposal' && meta.status && meta.status !== 'pending'

  return (
    <div className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
      {!isUser && (
        <div className="flex-shrink-0 w-9 h-9 rounded-xl bg-white border border-border flex items-center justify-center shadow-soft">
          {isHandoff ? (
            <span className="text-sm">📥</span>
          ) : isSystem ? (
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
            : isHandoff
              ? 'bg-surface border border-border text-text'
              : isSystem
                ? 'bg-surface border border-border text-muted italic'
                : 'bg-white border border-border text-text shadow-soft'
        }`}
      >
        <div className="markdown-body text-sm sm:text-base break-words">
          {meta.source === 'email' ? (
            <EmailTriageBody message={message} />
          ) : (
            <ChatMarkdown content={message.content} />
          )}
        </div>
        <MessageAttachments message={message} threadId={threadId} />
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

const LAST_OPENED_KEY = 'bigas_chat_last_opened'
const UNREAD_CHANNEL = 'bigas-chat-unread'
const THREAD_POLL_MS = 20000

const STARTER_PROMPTS = [
  {
    label: 'Summarize my latest GitHub PRs',
    prompt: 'Summarize my latest GitHub pull requests and flag anything that needs my attention.',
  },
  {
    label: 'Draft a tweet about recent releases',
    prompt: 'Draft a tweet about my recent Jira releases — short, founder-friendly, ready to post.',
  },
  {
    label: 'What is my GA4 traffic this week?',
    prompt: 'What is my GA4 traffic this week? Give me a quick summary of sessions, top pages, and trends.',
  },
  {
    label: 'Help me prioritize this week',
    prompt: 'I am a solo founder juggling multiple projects. Help me prioritize dev, maintenance, and distribution work this week.',
  },
]

function StarterPrompts({ onSelect, disabled }) {
  return (
    <div className="mt-6 max-w-lg mx-auto">
      <p className="text-xs font-medium text-muted uppercase tracking-wide mb-3">Try asking</p>
      <div className="flex flex-wrap gap-3 justify-center">
        {STARTER_PROMPTS.map(({ label, prompt }) => (
          <button
            key={label}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(prompt)}
            className="text-left bg-white border border-border rounded-2xl px-4 py-3 text-sm text-text shadow-soft hover:border-bigas-blue/60 hover:bg-bigas-blue/10 disabled:opacity-50 transition-colors min-h-[44px] max-w-full sm:max-w-[280px]"
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}

function readLastOpened() {
  try {
    const raw = localStorage.getItem(LAST_OPENED_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeLastOpened(map) {
  try {
    localStorage.setItem(LAST_OPENED_KEY, JSON.stringify(map))
  } catch {
    /* ignore quota / private mode */
  }
}

function seedLastOpened(existing) {
  return existing && typeof existing === 'object' ? existing : {}
}

function latestServerTimestamp(threads) {
  let max = ''
  for (const thread of threads || []) {
    for (const field of ['last_incoming_at', 'updated_at']) {
      const ts = thread?.[field] || ''
      if (ts > max) max = ts
    }
  }
  return max
}

function mergeLastOpened(a, b) {
  if (!b) return a || {}
  if (!a) return { ...b }
  let changed = false
  const out = { ...a }
  for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
    const left = a[key] || ''
    const right = b[key] || ''
    const merged = left > right ? left : right
    if ((a[key] || '') !== merged) {
      out[key] = merged
      changed = true
    }
  }
  return changed ? out : a
}

function latestThreadByAgent(threads) {
  const byAgent = {}
  for (const thread of threads || []) {
    const agentId = thread?.agent_id
    if (!agentId) continue
    const prev = byAgent[agentId]
    if (!prev || (thread.updated_at || '') > (prev.updated_at || '')) {
      byAgent[agentId] = thread
    }
  }
  return byAgent
}

function unreadAgentIdSet(threads, lastOpened, activeAgentId) {
  const ids = new Set()
  const byAgent = latestThreadByAgent(threads)
  for (const [agentId, thread] of Object.entries(byAgent)) {
    if (agentId === activeAgentId) continue
    if (thread.last_message_role === 'user') continue
    const incomingAt = thread.last_incoming_at || ''
    if (!incomingAt) continue
    const openedAt = lastOpened[agentId] || lastOpened._seeded || ''
    if (incomingAt > openedAt) ids.add(agentId)
  }
  return ids
}

function MobileAgentTabs({ agents, activeAgentId, onSelectAgent, unreadAgentIds }) {
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
              <span className="relative inline-flex">
                <span className={(agent.icon || '').includes('<') ? 'font-mono text-[10px] font-semibold' : ''}>
                  {agent.icon || '🤖'}
                </span>
                <UnreadDot
                  show={Boolean(unreadAgentIds?.has(agent.agent_id))}
                  className="-top-1 -right-1"
                />
              </span>
              <span className="whitespace-nowrap">{agent.name}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export default function ChatLayout({
  user,
  onLogout,
  onSwitchView,
  discussContext,
  onClearDiscussContext,
  onOpenSettings,
  agentsRefreshKey = 0,
}) {
  const [agents, setAgents] = useState([])
  const [activeAgentId, setActiveAgentId] = useState('chief')
  const [threadId, setThreadId] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [pendingFiles, setPendingFiles] = useState([])
  const [attachError, setAttachError] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const [sending, setSending] = useState(false)
  const fileInputRef = useRef(null)
  const [waitingForReply, setWaitingForReply] = useState(false)
  const [deployPollActive, setDeployPollActive] = useState(false)
  const [events, setEvents] = useState([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [activityOpen, setActivityOpen] = useState(false)
  const [threads, setThreads] = useState([])
  const [lastOpened, setLastOpened] = useState(() => seedLastOpened(readLastOpened()))
  const bottomRef = useRef(null)
  const lastMsgTs = useRef('')
  const lastFeedTs = useRef('')
  const unreadChannelRef = useRef(null)
  const messagesThreadIdRef = useRef(null)

  const addPendingFiles = useCallback((files) => {
    const incoming = Array.from(files || []).filter(Boolean)
    if (!incoming.length) return
    setPendingFiles((prev) => {
      const existingKeys = new Set(prev.map(pendingFileKey))
      const next = [...prev]
      let overflow = false
      for (const file of incoming) {
        const key = pendingFileKey(file)
        if (existingKeys.has(key)) continue
        if (next.length >= MAX_CHAT_ATTACHMENTS) {
          overflow = true
          break
        }
        existingKeys.add(key)
        next.push(file)
      }
      setAttachError(overflow ? `At most ${MAX_CHAT_ATTACHMENTS} attachments per message` : '')
      return next
    })
  }, [])

  const activeAgent = agents.find((a) => a.agent_id === activeAgentId) || { icon: '🤖', name: 'Agent' }
  const unreadAgentIds = useMemo(
    () => unreadAgentIdSet(threads, lastOpened, activeAgentId),
    [threads, lastOpened, activeAgentId],
  )

  const bumpLastOpened = useCallback((agentId, at) => {
    if (!agentId || !at) return
    setLastOpened((prev) => {
      const current = prev[agentId] || prev._seeded || ''
      if (at <= current) return prev
      const next = { ...prev, [agentId]: at }
      writeLastOpened(next)
      unreadChannelRef.current?.postMessage({ type: 'lastOpened', map: next })
      return next
    })
  }, [])

  const handleSelectAgent = useCallback(
    (agentId) => {
      messagesThreadIdRef.current = null
      const at = latestServerTimestamp(threads)
      if (at) bumpLastOpened(agentId, at)
      setActiveAgentId(agentId)
    },
    [bumpLastOpened, threads],
  )

  const loadAgents = useCallback(async () => {
    const res = await fetchAgents()
    setAgents(res.agents || [])
  }, [])

  const openAgentThread = useCallback(async (agentId, { cancelled } = {}) => {
    messagesThreadIdRef.current = null
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
      if (cancelled?.()) return
      setThreads(listed.threads || [])
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
    messagesThreadIdRef.current = thread.thread_id

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
    if (!agentsRefreshKey) return
    loadAgents()
  }, [agentsRefreshKey, loadAgents])

  useEffect(() => {
    if (!discussContext) return
    const prefix = `Let's discuss ticket ${discussContext.key}: ${discussContext.title}\n\n`
    setInput((prev) => {
      if (prev.startsWith(prefix)) return prev
      return prev.trim() ? `${prefix}${prev}` : prefix
    })
    setActiveAgentId('chief')
    onClearDiscussContext?.()
  }, [discussContext, onClearDiscussContext])

  useEffect(() => {
    let channel
    try {
      channel = new BroadcastChannel(UNREAD_CHANNEL)
      unreadChannelRef.current = channel
      channel.onmessage = (event) => {
        if (event.data?.type !== 'lastOpened' || !event.data.map) return
        setLastOpened((prev) => mergeLastOpened(prev, event.data.map))
      }
    } catch {
      unreadChannelRef.current = null
    }

    function onStorage(event) {
      if (event.key !== LAST_OPENED_KEY || !event.newValue) return
      try {
        const parsed = JSON.parse(event.newValue)
        if (parsed && typeof parsed === 'object') {
          setLastOpened((prev) => mergeLastOpened(prev, parsed))
        }
      } catch {
        /* ignore malformed storage */
      }
    }
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener('storage', onStorage)
      channel?.close()
      unreadChannelRef.current = null
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    async function pollThreads() {
      try {
        const res = await fetchThreads()
        if (!cancelled) setThreads(res.threads || [])
      } catch {
        /* ignore poll errors */
      }
    }

    pollThreads()
    const id = setInterval(pollThreads, THREAD_POLL_MS)
    const onVis = () => {
      if (document.visibilityState === 'visible') pollThreads()
    }
    document.addEventListener('visibilitychange', onVis)
    return () => {
      cancelled = true
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVis)
    }
  }, [])

  useEffect(() => {
    const serverTs = latestServerTimestamp(threads)
    if (!serverTs) return
    setLastOpened((prev) => {
      let changed = false
      const next = { ...prev }
      if (!prev._seeded || prev._seeded > serverTs) {
        next._seeded = serverTs
        changed = true
      }
      for (const [key, val] of Object.entries(prev)) {
        if (key.startsWith('_')) continue
        if (val > serverTs) {
          next[key] = serverTs
          changed = true
        }
      }
      if (!changed) return prev
      writeLastOpened(next)
      return next
    })
  }, [threads])

  useEffect(() => {
    const at = latestServerTimestamp(threads)
    if (at) bumpLastOpened(activeAgentId, at)
  }, [activeAgentId, bumpLastOpened, threads])

  useEffect(() => {
    if (!threadId || messagesThreadIdRef.current !== threadId) return
    const last = messages[messages.length - 1]
    if (!last?.created_at) return
    bumpLastOpened(activeAgentId, last.created_at)
  }, [activeAgentId, messages, threadId, bumpLastOpened])

  useEffect(() => {
    let cancelled = false
    openAgentThread(activeAgentId, { cancelled: () => cancelled })
    return () => {
      cancelled = true
    }
  }, [activeAgentId, openAgentThread])

  useEffect(() => {
    setPendingFiles([])
    setAttachError('')
  }, [threadId])

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

  async function handleSend(e, messageText) {
    e?.preventDefault?.()
    const text = (messageText ?? input).trim()
    const files = messageText ? [] : pendingFiles
    if ((!text && !files.length) || !threadId || sending) return
    setSending(true)
    setWaitingForReply(true)
    const clientId = createClientMessageId()
    const optimistic = {
      message_id: `local-${clientId}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
      metadata: { client_id: clientId, local_files: files },
    }
    setMessages((prev) => [...prev, optimistic])
    if (!messageText) {
      setInput('')
      setPendingFiles([])
      setAttachError('')
    }
    let sendSucceeded = false
    let result
    try {
      result = await sendMessage(threadId, text, clientId, files)
      sendSucceeded = true
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
      if (!sendSucceeded) {
        if (!messageText) {
          setInput(text)
          setPendingFiles(files)
        }
        setWaitingForReply(false)
        setMessages((prev) => [
          ...prev.filter((m) => m.message_id !== optimistic.message_id),
          {
            message_id: `err-${Date.now()}`,
            role: 'assistant',
            content: `Error: ${err.message}`,
            created_at: new Date().toISOString(),
          },
        ])
      } else {
        setMessages((prev) => [
          ...prev,
          {
            message_id: `err-${Date.now()}`,
            role: 'assistant',
            content: `Message sent, but failed to refresh: ${err.message}`,
            created_at: new Date().toISOString(),
          },
        ])
        if (result?.deploy_poll_active) {
          setDeployPollActive(true)
          setWaitingForReply(true)
        }
      }
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
        onSelectAgent={handleSelectAgent}
        onOpenSettings={onOpenSettings}
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        unreadAgentIds={unreadAgentIds}
      />

      <main className="flex-1 flex flex-col min-w-0 bg-bg">
        <header className="flex items-center gap-3 px-4 py-3 border-b border-border bg-white/90 backdrop-blur-sm sticky top-0 z-10">
          <SettingsButton onClick={onOpenSettings} />
          <button
            type="button"
            className="relative lg:hidden text-xl min-w-[44px] min-h-[44px] flex items-center justify-center -ml-2 hover:bg-surface rounded-xl transition-colors"
            onClick={() => setSidebarOpen(true)}
            aria-label={unreadAgentIds.size ? 'Open agents, unread messages' : 'Open agents'}
          >
            ☰
            <UnreadDot show={unreadAgentIds.size > 0} />
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
          {onSwitchView && (
            <>
              <button
                type="button"
                onClick={() => onSwitchView('board')}
                className="text-sm text-muted hover:text-text px-3 py-2 rounded-lg hover:bg-surface min-h-[44px] transition-colors hidden sm:block"
              >
                Board
              </button>
              <button
                type="button"
                onClick={() => onSwitchView('objectives')}
                className="text-sm text-muted hover:text-text px-3 py-2 rounded-lg hover:bg-surface min-h-[44px] transition-colors hidden sm:block"
              >
                Objectives
              </button>
            </>
          )}
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
          onSelectAgent={handleSelectAgent}
          unreadAgentIds={unreadAgentIds}
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
                <StarterPrompts
                  disabled={sending || !threadId}
                  onSelect={(prompt) => handleSend({ preventDefault: () => {} }, prompt)}
                />
              </div>
            )}
            {messages.map((m) => (
              <MessageBubble
                key={m.message_id}
                message={m}
                agentIcon={activeAgent.icon}
                onProposalResolved={refreshMessages}
                threadId={threadId}
              />
            ))}
            {showTyping && (
              <TypingIndicator agentName={activeAgent.name} agentIcon={activeAgent.icon} />
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-border bg-white/95 backdrop-blur-sm px-3 sm:px-4 py-3 sm:py-4">
          <form
            onSubmit={handleSend}
            className="max-w-3xl mx-auto"
            onDragOver={(e) => {
              e.preventDefault()
              setDragOver(true)
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragOver(false)
              addPendingFiles(e.dataTransfer.files)
            }}
          >
            {pendingFiles.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-2">
                {pendingFiles.map((file) => (
                  <div
                    key={pendingFileKey(file)}
                    className="flex items-center gap-2 rounded-xl border border-border bg-surface px-2 py-1.5 max-w-[220px]"
                  >
                    <div className="min-w-0">
                      <p className="text-xs font-medium truncate">{file.name}</p>
                      <p className="text-[10px] text-muted">{formatAttachmentSize(file.size)}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() =>
                        setPendingFiles((prev) =>
                          prev.filter((item) => pendingFileKey(item) !== pendingFileKey(file)),
                        )
                      }
                      className="text-muted hover:text-text text-sm px-1"
                      aria-label={`Remove ${file.name}`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
            {attachError && <p className="text-xs text-red-600 mb-2">{attachError}</p>}
            <div
              className={`flex gap-2 items-end bg-white border rounded-2xl shadow-input px-3 py-2 sm:px-4 sm:py-2.5 focus-within:ring-2 focus-within:ring-bigas-blue/40 transition-shadow ${
                dragOver ? 'border-bigas-blue' : 'border-border'
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={CHAT_ATTACHMENT_ACCEPT}
                className="sr-only"
                onChange={(e) => {
                  addPendingFiles(e.target.files)
                  e.target.value = ''
                }}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={sending || !threadId}
                className="text-muted hover:text-text w-11 h-11 flex-shrink-0 flex items-center justify-center rounded-xl hover:bg-surface disabled:opacity-30"
                aria-label="Attach file"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path
                    d="M21.44 11.05l-8.49 8.49a5.5 5.5 0 01-7.78-7.78l8.49-8.49a3.5 3.5 0 014.95 4.95l-8.49 8.49a1.5 1.5 0 01-2.12-2.12l7.78-7.78"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={(e) => {
                  const images = Array.from(e.clipboardData?.items || [])
                    .filter((item) => item.type.startsWith('image/'))
                    .map((item) => item.getAsFile())
                    .filter(Boolean)
                  if (!images.length) return
                  const pastedText = (e.clipboardData?.getData('text/plain') || '').trim()
                  if (pastedText) {
                    addPendingFiles(images)
                    return
                  }
                  e.preventDefault()
                  addPendingFiles(images)
                }}
                placeholder={`Message ${activeAgent.name}…`}
                className="flex-1 bg-transparent border-0 px-1 py-2 text-sm sm:text-base min-w-0 focus:outline-none placeholder:text-muted"
                aria-label={`Message ${activeAgent.name}`}
              />
              <button
                type="submit"
                disabled={sending || (!input.trim() && !pendingFiles.length)}
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
              Attach screenshots or files — they are interpreted for the AI in this conversation.
            </p>
          </form>
        </div>
      </main>

      <ActivityFeed events={events} open={activityOpen} onClose={() => setActivityOpen(false)} />
    </div>
  )
}
