import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import {
  addTicketComment,
  createBoard,
  createTicket,
  deleteBoard,
  deleteTicket,
  fetchBoards,
  fetchBoardTickets,
  fetchTicket,
  fetchTicketByKey,
  syncBoardFromJira,
  updateTicket,
} from '../lib/api'

const SYSTEM_COMMENT_MARKER = '[bigas-jira-ai]'

const AI_WORKING_STATUSES = new Set([
  'Research and describe (AI)',
  'Design and plan (AI)',
  'In Progress (AI)',
])

function TicketAiMark({ status }) {
  const active = AI_WORKING_STATUSES.has(status)
  return (
    <img
      src="/favicon.png"
      alt=""
      title={active ? 'Bigas is working' : 'Bigas'}
      className={`w-4 h-4 rounded-sm flex-shrink-0 ${active ? 'ticket-ai-active' : 'ticket-ai-idle'}`}
      aria-hidden="true"
    />
  )
}

function StatusSelect({ value, columns, onChange, className = '' }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`text-xs border border-border rounded-lg px-2 py-1.5 bg-white min-h-[36px] ${className}`}
      aria-label="Change status"
    >
      {columns.map((col) => (
        <option key={col} value={col}>
          {col}
        </option>
      ))}
    </select>
  )
}

function normalizeLabel(raw) {
  return String(raw || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-._]+|[-._]+$/g, '')
    .slice(0, 255)
}

function ticketLabels(ticket) {
  const fromList = Array.isArray(ticket?.labels) ? ticket.labels : []
  const labels = fromList.map(normalizeLabel).filter(Boolean)
  if (ticket?.marketing && !labels.includes('marketing')) labels.push('marketing')
  return labels
}

function LabelChips({ labels, onRemove, className = '' }) {
  if (!labels?.length) return null
  return (
    <div className={`flex flex-wrap gap-1 ${className}`}>
      {labels.map((label) => (
        <span
          key={label}
          className="inline-flex items-center gap-1 max-w-full text-[10px] leading-tight px-1.5 py-0.5 rounded-md bg-surface border border-border text-muted"
        >
          <span className="truncate">{label}</span>
          {onRemove && (
            <button
              type="button"
              onClick={() => onRemove(label)}
              className="text-muted hover:text-text min-w-[16px]"
              aria-label={`Remove ${label}`}
            >
              ×
            </button>
          )}
        </span>
      ))}
    </div>
  )
}

function LabelEditor({ labels, onChange }, ref) {
  const [draft, setDraft] = useState('')

  const addDraft = () => {
    const next = normalizeLabel(draft)
    if (!next) {
      setDraft('')
      return labels
    }
    if (labels.includes(next)) {
      setDraft('')
      return labels
    }
    const merged = [...labels, next]
    onChange(merged)
    setDraft('')
    return merged
  }

  useImperativeHandle(ref, () => ({
    flushDraft() {
      return addDraft()
    },
  }))

  return (
    <div className="block text-sm">
      <span className="text-muted text-xs">Labels</span>
      <LabelChips
        labels={labels}
        onRemove={(label) => onChange(labels.filter((item) => item !== label))}
        className="mt-1"
      />
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            addDraft()
          }
        }}
        onBlur={addDraft}
        placeholder="customer-request, then Enter"
        className="mt-1 w-full border border-border rounded-xl px-3 py-2 min-h-[44px]"
      />
    </div>
  )
}

const LabelEditorWithRef = forwardRef(LabelEditor)

function TicketCard({ ticket, columns, onEdit, onStatusChange, onDiscuss, dragging, onDragStart, onDragEnd }) {
  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', ticket.ticket_id || ticket.key || '')
        onDragStart(e, ticket)
      }}
      onDragEnd={onDragEnd}
      className={`bg-white border border-border rounded-xl p-3 shadow-soft cursor-grab active:cursor-grabbing ${
        dragging ? 'opacity-50' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="flex items-center gap-1.5 min-w-0">
          <TicketAiMark status={ticket.status} />
          <span className="text-[11px] font-mono text-muted truncate">{ticket.key}</span>
        </div>
        <StatusSelect
          value={ticket.status}
          columns={columns}
          onChange={(st) => onStatusChange(ticket, st)}
          className="lg:hidden flex-shrink-0 max-w-[140px]"
        />
      </div>
      <button
        type="button"
        onClick={() => onEdit(ticket)}
        className="text-left w-full font-medium text-sm leading-snug hover:text-bigas-black"
      >
        {ticket.title}
      </button>
      <LabelChips labels={ticketLabels(ticket)} className="mt-2" />
      {ticket.assignee && (
        <p className="text-xs text-muted mt-2 truncate">{ticket.assignee}</p>
      )}
      <div className="flex gap-2 mt-3">
        <button
          type="button"
          onClick={() => onDiscuss(ticket)}
          className="text-xs px-2 py-1 rounded-lg border border-border hover:bg-surface min-h-[32px]"
        >
          Discuss
        </button>
      </div>
    </div>
  )
}

function isSystemComment(comment) {
  return (comment?.body || '').includes(SYSTEM_COMMENT_MARKER)
}

function formatCommentTime(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return String(iso).slice(0, 16).replace('T', ' ')
  }
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function commentBody(comment) {
  return (comment?.body || '').replace(SYSTEM_COMMENT_MARKER, '').trim()
}

function TicketComments({ ticketId }) {
  const [comments, setComments] = useState([])
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [showSystem, setShowSystem] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetchTicket(ticketId)
      .then((data) => {
        if (!cancelled) setComments(data.ticket?.comments || [])
      })
      .catch(() => {
        if (!cancelled) setError('Could not load comments')
      })
    return () => {
      cancelled = true
    }
  }, [ticketId])

  const human = comments.filter((c) => !isSystemComment(c))
  const system = comments.filter((c) => isSystemComment(c))
  const visible = showSystem ? comments : human

  const handlePost = async () => {
    const body = draft.trim()
    if (!body || sending) return
    setSending(true)
    setError('')
    try {
      const data = await addTicketComment(ticketId, body)
      if (data.comment) setComments((prev) => [...prev, data.comment])
      setDraft('')
    } catch (err) {
      setError(err.message || 'Could not post comment')
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="pt-3 border-t border-border space-y-3">
      <div>
        <p className="text-muted text-xs">Comments</p>
        <p className="text-[11px] text-muted mt-0.5">The next AI step reads these comments.</p>
      </div>
      <div className="space-y-2">
        {visible.length === 0 && (
          <p className="text-xs text-muted">No comments yet.</p>
        )}
        {visible.map((comment) => {
          const fromSystem = isSystemComment(comment)
          return (
            <div
              key={comment.id}
              className={`rounded-xl px-3 py-2 text-sm ${
                fromSystem ? 'bg-surface text-muted' : 'bg-surface border border-border'
              }`}
            >
              <div className="flex items-baseline justify-between gap-2 mb-1">
                <span className="text-xs font-medium truncate">
                  {fromSystem ? 'Bigas' : comment.author_name || 'Someone'}
                </span>
                <span className="text-[10px] text-muted flex-shrink-0">
                  {formatCommentTime(comment.created_at)}
                </span>
              </div>
              <p className="whitespace-pre-wrap break-words text-sm leading-snug">{commentBody(comment)}</p>
            </div>
          )
        })}
      </div>
      {system.length > 0 && (
        <button
          type="button"
          onClick={() => setShowSystem((v) => !v)}
          className="text-xs text-muted hover:text-text min-h-[32px]"
        >
          {showSystem ? 'Hide system notes' : `Show system notes (${system.length})`}
        </button>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
      <textarea
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        rows={3}
        placeholder="Feedback for the next AI step…"
        className="w-full border border-border rounded-xl px-3 py-2 text-sm"
      />
      <button
        type="button"
        onClick={handlePost}
        disabled={!draft.trim() || sending}
        className="w-full border border-border rounded-xl py-2 min-h-[44px] text-sm font-medium disabled:opacity-50 hover:bg-surface"
      >
        {sending ? 'Posting…' : 'Comment'}
      </button>
    </div>
  )
}

function TicketModal({ ticket, columns, board, onClose, onSave, onDelete }) {
  const labelEditorRef = useRef(null)
  const [form, setForm] = useState({
    title: ticket?.title || '',
    description: ticket?.description || '',
    status: ticket?.status || columns[0] || 'To Do',
    assignee: ticket?.assignee || '',
    fix_version: ticket?.fix_version || '',
    issue_type: ticket?.issue_type || 'Task',
    labels: ticketLabels(ticket),
  })
  const isNew = !ticket?.ticket_id

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-white w-full sm:max-w-lg rounded-t-2xl sm:rounded-2xl shadow-card max-h-[90vh] flex flex-col">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h3 className="font-semibold">{isNew ? 'New ticket' : ticket.key}</h3>
          <button type="button" onClick={onClose} className="p-2 min-w-[44px] min-h-[44px]" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="p-4 overflow-y-auto flex-1 space-y-3">
          <label className="block text-sm">
            <span className="text-muted text-xs">Title</span>
            <input
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              className="mt-1 w-full border border-border rounded-xl px-3 py-2 min-h-[44px]"
            />
          </label>
          <label className="block text-sm">
            <span className="text-muted text-xs">Description</span>
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              rows={4}
              className="mt-1 w-full border border-border rounded-xl px-3 py-2"
            />
          </label>
          <label className="block text-sm">
            <span className="text-muted text-xs">Issue type</span>
            <select
              value={form.issue_type}
              onChange={(e) => setForm({ ...form, issue_type: e.target.value })}
              className="mt-1 w-full border border-border rounded-xl px-3 py-2 min-h-[44px]"
            >
              <option value="Task">Task</option>
              <option value="Bug">Bug</option>
              <option value="Epic">Epic</option>
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-muted text-xs">Status</span>
            <select
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
              className="mt-1 w-full border border-border rounded-xl px-3 py-2 min-h-[44px]"
            >
              {columns.map((col) => (
                <option key={col} value={col}>
                  {col}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            <span className="text-muted text-xs">Assignee</span>
            <input
              value={form.assignee}
              onChange={(e) => setForm({ ...form, assignee: e.target.value })}
              className="mt-1 w-full border border-border rounded-xl px-3 py-2 min-h-[44px]"
            />
          </label>
          {board?.workflow_enabled && (
            <label className="block text-sm">
              <span className="text-muted text-xs">Fix version</span>
              <input
                value={form.fix_version}
                onChange={(e) => setForm({ ...form, fix_version: e.target.value })}
                placeholder="e.g. v1.2.0"
                className="mt-1 w-full border border-border rounded-xl px-3 py-2 min-h-[44px]"
              />
            </label>
          )}
          <LabelEditorWithRef
            ref={labelEditorRef}
            labels={form.labels}
            onChange={(labels) => setForm({ ...form, labels })}
          />
          {!isNew && <TicketComments ticketId={ticket.ticket_id} />}
        </div>
        <div className="p-4 border-t border-border flex flex-col sm:flex-row gap-2">
          {!isNew && (
            <button
              type="button"
              onClick={() => {
                if (!window.confirm(`Delete ticket ${ticket.key}?`)) return
                onDelete(ticket)
              }}
              className="text-red-600 text-sm px-4 py-2 min-h-[44px] order-3 sm:order-1 sm:mr-auto"
            >
              Delete
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="flex-1 border border-border rounded-xl py-2 min-h-[44px] order-1 sm:order-2"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              const labels = labelEditorRef.current?.flushDraft?.() ?? form.labels
              onSave({ ...form, labels })
            }}
            disabled={!form.title.trim()}
            className="flex-1 bg-bigas-blue text-bigas-black font-medium rounded-xl py-2 min-h-[44px] order-2 sm:order-3 disabled:opacity-50"
          >
            {isNew ? 'Create' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

function BoardSidebar({ boards, activeBoardId, onSelect, onCreate, onDelete, mobileOpen, onClose }) {
  const [newName, setNewName] = useState('')
  const [newProject, setNewProject] = useState('')
  const [showCreate, setShowCreate] = useState(false)

  const handleCreate = async () => {
    if (!newName.trim()) return
    await onCreate({ name: newName.trim(), project_key: newProject.trim() || null })
    setNewName('')
    setNewProject('')
    setShowCreate(false)
  }

  return (
    <>
      {mobileOpen && (
        <div className="fixed inset-0 bg-black/30 z-40 lg:hidden" onClick={onClose} aria-hidden="true" />
      )}
      <aside
        className={`fixed lg:static inset-y-0 left-0 z-50 w-64 bg-surface border-r border-border flex flex-col transform transition-transform duration-200 lg:translate-x-0 ${
          mobileOpen ? 'translate-x-0 shadow-card' : '-translate-x-full'
        }`}
      >
        <div className="p-4 border-b border-border">
          <h2 className="font-bold text-sm">Boards</h2>
        </div>
        <nav className="flex-1 overflow-y-auto p-2 space-y-1">
          {boards.map((board) => {
            const active = board.board_id === activeBoardId
            return (
              <div key={board.board_id} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => {
                    onSelect(board.board_id)
                    onClose?.()
                  }}
                  className={`flex-1 text-left px-3 py-2.5 rounded-xl text-sm min-h-[44px] truncate ${
                    active
                      ? 'bg-bigas-blue text-bigas-black font-medium'
                      : 'hover:bg-white text-text'
                  }`}
                >
                  <span className="block truncate">{board.name}</span>
                  {board.project_key && (
                    <span className="text-[10px] opacity-70">{board.project_key}</span>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => onDelete(board.board_id)}
                  className="p-2 text-muted hover:text-red-600 min-w-[36px] min-h-[36px] text-xs"
                  aria-label={`Delete ${board.name}`}
                  title="Delete board"
                >
                  ×
                </button>
              </div>
            )
          })}
        </nav>
        <div className="p-3 border-t border-border">
          {showCreate ? (
            <div className="space-y-2">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="Board name"
                className="w-full border border-border rounded-lg px-2 py-2 text-sm min-h-[40px]"
              />
              <input
                value={newProject}
                onChange={(e) => setNewProject(e.target.value)}
                placeholder="Project key (optional, e.g. VFA)"
                className="w-full border border-border rounded-lg px-2 py-2 text-sm min-h-[40px]"
              />
              <div className="flex gap-2">
                <button type="button" onClick={() => setShowCreate(false)} className="flex-1 text-sm py-2">
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleCreate}
                  className="flex-1 bg-bigas-blue rounded-lg text-sm py-2 font-medium"
                >
                  Add
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="w-full text-sm py-2 rounded-xl border border-dashed border-border hover:bg-white min-h-[44px]"
            >
              + New board
            </button>
          )}
        </div>
      </aside>
    </>
  )
}

export default function BoardLayout({ user, onLogout, onDiscussTicket, onSwitchView }) {
  const [boards, setBoards] = useState([])
  const [activeBoardId, setActiveBoardId] = useState(null)
  const [tickets, setTickets] = useState([])
  const [loading, setLoading] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [modalTicket, setModalTicket] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [dragTicket, setDragTicket] = useState(null)
  const [pendingTicketKey, setPendingTicketKey] = useState(null)
  const [jiraImportAvailable, setJiraImportAvailable] = useState(false)
  const [syncingJira, setSyncingJira] = useState(false)
  const [syncMessage, setSyncMessage] = useState('')

  const activeBoard = boards.find((b) => b.board_id === activeBoardId)
  const columns = activeBoard?.columns || ['To Do', 'In Progress', 'Review', 'Done']

  const loadBoards = useCallback(async () => {
    const data = await fetchBoards()
    setBoards(data.boards || [])
    setJiraImportAvailable(Boolean(data.jira_import_available))
    if (!activeBoardId && data.boards?.length) {
      setActiveBoardId(data.boards[0].board_id)
    }
  }, [activeBoardId])

  const loadTickets = useCallback(async () => {
    if (!activeBoardId) return
    const data = await fetchBoardTickets(activeBoardId)
    setTickets(data.tickets || [])
  }, [activeBoardId])

  useEffect(() => {
    loadBoards().finally(() => setLoading(false))
  }, [loadBoards])

  useEffect(() => {
    loadTickets()
    const id = setInterval(loadTickets, 5000)
    return () => clearInterval(id)
  }, [loadTickets])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const ticketKey = (params.get('ticket') || '').trim().toUpperCase()
    if (ticketKey) setPendingTicketKey(ticketKey)
  }, [])

  useEffect(() => {
    if (!pendingTicketKey) return

    let cancelled = false

    async function openTicketFromUrl() {
      const found = tickets.find((t) => t.key === pendingTicketKey)
      if (found) {
        setModalTicket(found)
        setPendingTicketKey(null)
        return
      }

      try {
        const data = await fetchTicketByKey(pendingTicketKey)
        if (cancelled || !data.ticket) return
        if (data.ticket.board_id && data.ticket.board_id !== activeBoardId) {
          setActiveBoardId(data.ticket.board_id)
          return
        }
        setModalTicket(data.ticket)
        setPendingTicketKey(null)
      } catch {
        /* ticket not found or forbidden */
      }
    }

    openTicketFromUrl()
    return () => {
      cancelled = true
    }
  }, [pendingTicketKey, tickets, activeBoardId])

  const handleStatusChange = async (ticket, status) => {
    await updateTicket(ticket.ticket_id, { status })
    await loadTickets()
  }

  const handleDrop = async (status) => {
    if (!dragTicket || dragTicket.status === status) {
      setDragTicket(null)
      return
    }
    await handleStatusChange(dragTicket, status)
    setDragTicket(null)
  }

  const handleSave = async (form) => {
    if (modalTicket?.ticket_id) {
      await updateTicket(modalTicket.ticket_id, form)
    } else {
      await createTicket(activeBoardId, form)
    }
    setModalTicket(null)
    setShowCreate(false)
    await loadTickets()
  }

  const handleDeleteTicket = async (ticket) => {
    await deleteTicket(ticket.ticket_id)
    setModalTicket(null)
    await loadTickets()
  }

  const handleCreateBoard = async ({ name, project_key }) => {
    const data = await createBoard({ name, project_key })
    await loadBoards()
    if (data.board?.board_id) setActiveBoardId(data.board.board_id)
  }

  const handleDeleteBoard = async (boardId) => {
    if (!window.confirm('Delete this board and all its tickets?')) return
    await deleteBoard(boardId)
    if (activeBoardId === boardId) setActiveBoardId(null)
    await loadBoards()
  }

  const handleSyncJira = async () => {
    if (!activeBoardId || syncingJira) return
    setSyncingJira(true)
    setSyncMessage('')
    try {
      const data = await syncBoardFromJira(activeBoardId)
      if (data.status === 'started') {
        setSyncMessage('Jira sync running in background…')
        await new Promise((resolve) => setTimeout(resolve, 3000))
        await loadTickets()
        setSyncMessage('Jira sync started — tickets refreshed')
      } else {
        setSyncMessage(
          `Jira sync: ${data.created || 0} new, ${data.updated || 0} updated` +
            (data.skipped ? `, ${data.skipped} skipped` : ''),
        )
        await loadTickets()
      }
    } catch (err) {
      setSyncMessage(err.message || 'Jira sync failed')
    } finally {
      setSyncingJira(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg text-muted">
        Loading board…
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col lg:flex-row overflow-hidden bg-bg">
      <BoardSidebar
        boards={boards}
        activeBoardId={activeBoardId}
        onSelect={setActiveBoardId}
        onCreate={handleCreateBoard}
        onDelete={handleDeleteBoard}
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <header className="sticky top-0 z-10 bg-bg/90 backdrop-blur-sm border-b border-border px-3 sm:px-4 py-3 flex items-center gap-2">
          <button
            type="button"
            className="lg:hidden p-2 min-w-[44px] min-h-[44px] border border-border rounded-xl"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open boards"
          >
            ☰
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="font-bold truncate">{activeBoard?.name || 'Board'}</h1>
            {activeBoard?.workflow_enabled && (
              <p className="text-xs text-muted">
                {syncMessage || 'AI workflow enabled'}
              </p>
            )}
          </div>
          {jiraImportAvailable && activeBoard?.workflow_enabled && (
            <button
              type="button"
              onClick={handleSyncJira}
              disabled={syncingJira}
              className="text-sm px-3 py-2 rounded-xl border border-border min-h-[44px] disabled:opacity-50"
            >
              {syncingJira ? 'Syncing…' : 'Sync Jira'}
            </button>
          )}
          <button
            type="button"
            onClick={() => onSwitchView('chat')}
            className="text-sm px-3 py-2 rounded-xl border border-border min-h-[44px] hidden sm:block"
          >
            Chat
          </button>
          <button
            type="button"
            onClick={() => {
              setModalTicket(null)
              setShowCreate(true)
            }}
            className="bg-bigas-blue text-bigas-black font-medium px-3 py-2 rounded-xl min-h-[44px] text-sm"
          >
            + Ticket
          </button>
          <button type="button" onClick={onLogout} className="text-sm text-muted px-2 min-h-[44px]">
            Log out
          </button>
        </header>

        {/* Mobile: stacked columns */}
        <div className="flex-1 overflow-y-auto lg:hidden p-3 space-y-4">
          {columns.map((col) => {
            const colTickets = tickets.filter((t) => t.status === col)
            return (
              <section key={col} className="border border-border rounded-xl bg-surface/50">
                <h3 className="px-3 py-2 text-sm font-semibold border-b border-border flex justify-between">
                  <span className="truncate">{col}</span>
                  <span className="text-muted font-normal">{colTickets.length}</span>
                </h3>
                <div className="p-2 space-y-2">
                  {colTickets.map((ticket) => (
                    <TicketCard
                      key={ticket.ticket_id}
                      ticket={ticket}
                      columns={columns}
                      onEdit={setModalTicket}
                      onStatusChange={handleStatusChange}
                      onDiscuss={onDiscussTicket}
                      dragging={dragTicket?.ticket_id === ticket.ticket_id}
                      onDragStart={(_, t) => setDragTicket(t)}
                      onDragEnd={() => setDragTicket(null)}
                    />
                  ))}
                </div>
              </section>
            )
          })}
        </div>

        {/* Desktop: horizontal kanban */}
        <div className="hidden lg:flex flex-1 overflow-x-auto p-4 gap-3">
          {columns.map((col) => {
            const colTickets = tickets.filter((t) => t.status === col)
            return (
              <section
                key={col}
                className="flex-shrink-0 w-72 flex flex-col bg-surface/60 rounded-xl border border-border"
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => handleDrop(col)}
              >
                <h3 className="px-3 py-2 text-sm font-semibold border-b border-border flex justify-between">
                  <span className="truncate">{col}</span>
                  <span className="text-muted font-normal">{colTickets.length}</span>
                </h3>
                <div className="flex-1 overflow-y-auto p-2 space-y-2 min-h-[120px]">
                  {colTickets.map((ticket) => (
                    <TicketCard
                      key={ticket.ticket_id}
                      ticket={ticket}
                      columns={columns}
                      onEdit={setModalTicket}
                      onStatusChange={handleStatusChange}
                      onDiscuss={onDiscussTicket}
                      dragging={dragTicket?.ticket_id === ticket.ticket_id}
                      onDragStart={(_, t) => setDragTicket(t)}
                      onDragEnd={() => setDragTicket(null)}
                    />
                  ))}
                </div>
              </section>
            )
          })}
        </div>
      </main>

      {(modalTicket || showCreate) && (
        <TicketModal
          ticket={showCreate ? null : modalTicket}
          columns={columns}
          board={activeBoard}
          onClose={() => {
            setModalTicket(null)
            setShowCreate(false)
          }}
          onSave={handleSave}
          onDelete={handleDeleteTicket}
        />
      )}
    </div>
  )
}
