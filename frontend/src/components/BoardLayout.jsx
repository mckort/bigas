import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import {
  addTicketComment,
  createBoard,
  createTicket,
  deleteBoard,
  deleteTicket,
  deleteTicketAttachment,
  fetchBoardJiraSyncStatus,
  fetchBoards,
  fetchBoardTickets,
  fetchTicket,
  fetchTicketAttachmentBlob,
  fetchTicketByKey,
  updateTicket,
  uploadTicketAttachment,
} from '../lib/api'
import {
  emptyKeyResult,
  isEpic,
  isObjective,
  keyResultsOf,
  normalizeLabel,
  objectiveChipLabel,
  krProgress,
  objectiveOptionsFromTickets,
  percentLabel,
  ticketLabels,
  ticketMatchesObjectiveFilter,
  ticketParentKey,
  ticketParentKrId,
} from '../lib/okr'
import { SettingsButton } from './AgentSettings'
import ThemeToggle from './ThemeToggle'

const SYSTEM_COMMENT_MARKER = '[bigas-jira-ai]'

const AI_WORKING_STATUSES = new Set([
  'Research and describe (AI)',
  'Design and plan (AI)',
  'In Progress (AI)',
])

function isTodoColumn(col) {
  return String(col || '').trim().toLowerCase() === 'to do'
}

const BOARD_FILTER_QUERY_KEYS = ['kr', 'objective', 'ticket']

function boardQuery() {
  const params = new URLSearchParams(window.location.search)
  return {
    boardId: (params.get('board') || '').trim(),
    ticketKey: (params.get('ticket') || '').trim().toUpperCase(),
    krId: (params.get('kr') || '').trim(),
    objectiveKey: (params.get('objective') || '').trim().toUpperCase(),
  }
}

const BOARD_URL_SYNC_EVENT = 'bigas-board-url'

function replaceBoardLocation(mutateParams) {
  const params = new URLSearchParams(window.location.search)
  mutateParams(params)
  const qs = params.toString()
  const pathname = window.location.pathname
  const hash = window.location.hash || ''
  const next = `${qs ? `${pathname}?${qs}` : pathname}${hash}`
  const current = `${pathname}${window.location.search}${hash}`
  if (current === next) return false
  window.history.replaceState({}, '', next)
  window.dispatchEvent(new Event(BOARD_URL_SYNC_EVENT))
  return true
}

function replaceBoardUrl(boardId, { keepFilters = true } = {}) {
  replaceBoardLocation((params) => {
    if (!keepFilters) {
      for (const key of BOARD_FILTER_QUERY_KEYS) params.delete(key)
    }
    if (boardId) params.set('board', String(boardId))
    else params.delete('board')
  })
}

function filterFromQuery(query) {
  if (query.krId) return `kr:${query.krId}`
  if (query.objectiveKey) return query.objectiveKey
  return ''
}

function upsertTicket(list, ticket) {
  if (!ticket?.ticket_id) return list || []
  const tickets = Array.isArray(list) ? list : []
  const index = tickets.findIndex((item) => item.ticket_id === ticket.ticket_id)
  if (index === -1) return [...tickets, ticket]
  const next = [...tickets]
  next[index] = { ...tickets[index], ...ticket }
  return next
}

function clearObjectiveFilterFromUrl() {
  replaceBoardLocation((params) => {
    params.delete('kr')
    params.delete('objective')
  })
}

function parentKeyFromFilter(filter, tickets) {
  if (!filter || filter === '__none__') return ''
  if (filter.startsWith('kr:')) {
    const krId = filter.slice(3)
    const fromChild = tickets.find((ticket) => ticketParentKrId(ticket) === krId)?.parent_key
    if (fromChild) return fromChild
    const objective = tickets.find(
      (ticket) =>
        isObjective(ticket) &&
        keyResultsOf(ticket).some((kr) => String(kr.id || '') === krId),
    )
    return objective?.key || ''
  }
  return filter
}

function parentKrIdFromFilter(filter) {
  if (!filter || !filter.startsWith('kr:')) return ''
  return filter.slice(3)
}

function ColumnCreateButton({ hiddenUntilHover = false, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-muted hover:bg-elevated hover:text-text min-h-[40px] transition-opacity duration-150 ${
        hiddenUntilHover ? 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100' : ''
      }`}
    >
      <span aria-hidden="true">+</span>
      Create
    </button>
  )
}

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
      className={`text-xs border border-border rounded-lg px-2 py-1.5 bg-elevated min-h-[36px] ${className}`}
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

function ObjectiveChip({ epic, onClick, className = '' }) {
  if (!epic) return null
  const label = objectiveChipLabel(epic)
  const classes = `chip-accent ${className}`
  if (!onClick) {
    return <span className={classes}><span className="truncate">{label}</span></span>
  }
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseDown={(e) => e.stopPropagation()}
      className={`${classes} hover:bg-accent/20 transition-colors duration-150`}
      title={`Show tickets in ${epic.key}`}
    >
      <span className="truncate">{label}</span>
    </button>
  )
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
        onPaste={(e) => {
          const text = e.clipboardData.getData('text')
          if (!text.includes(',')) return
          e.preventDefault()
          const parts = text.split(',').map((part) => normalizeLabel(part)).filter(Boolean)
          if (!parts.length) return
          const merged = [...labels]
          for (const part of parts) {
            if (!merged.includes(part)) merged.push(part)
          }
          onChange(merged)
          setDraft('')
        }}
        onBlur={addDraft}
        placeholder="customer-request, then Enter"
        className="mt-1 w-full border border-border rounded-xl px-3 py-2 min-h-[44px]"
      />
    </div>
  )
}

const LabelEditorWithRef = forwardRef(LabelEditor)

function TicketCard({ ticket, parentEpic, parentKr, columns, onEdit, onStatusChange, onDiscuss, onFilterEpic, dragging, onDragStart, onDragEnd }) {
  const results = keyResultsOf(ticket)
  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', ticket.ticket_id || ticket.key || '')
        onDragStart(e, ticket)
      }}
      onDragEnd={onDragEnd}
      className={`bg-elevated border border-border rounded-lg p-3 shadow-soft cursor-grab active:cursor-grabbing transition-all duration-150 hover:shadow-card hover:border-border-strong ${
        dragging ? 'opacity-50 scale-[0.98]' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="flex items-center gap-1.5 min-w-0">
          <TicketAiMark status={ticket.status} />
          <span className="text-[11px] font-mono text-muted truncate">{ticket.key}</span>
          {isObjective(ticket) && (
            <span className="chip-accent flex-shrink-0">
              Objective
            </span>
          )}
          {isEpic(ticket) && (
            <span className="text-[10px] leading-tight px-1.5 py-0.5 rounded-md bg-surface border border-border text-muted flex-shrink-0">
              Epic
            </span>
          )}
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
        className="text-left w-full font-medium text-sm leading-snug hover:text-accent transition-colors duration-150"
      >
        {ticket.title}
      </button>
      {parentEpic && (
        <ObjectiveChip
          epic={parentEpic}
          onClick={() => onFilterEpic?.(parentEpic.key)}
          className="mt-2"
        />
      )}
      {parentKr && (
        <button
          type="button"
          onClick={() => onFilterEpic?.(`kr:${parentKr.id}`)}
          onMouseDown={(e) => e.stopPropagation()}
          className="mt-1 inline-flex max-w-full text-[10px] leading-tight px-1.5 py-0.5 rounded-md bg-surface border border-border text-muted truncate"
          title="Filter by Key Result"
        >
          KR · {parentKr.title}
        </button>
      )}
      {isObjective(ticket) && results.length > 0 && (
        <div className="mt-2 space-y-1">
          {results.slice(0, 3).map((kr) => (
            <div key={kr.id || kr.title} className="flex items-center gap-2">
              <span className="text-[10px] text-muted truncate flex-1">{kr.title}</span>
              <span className="text-[10px] font-mono text-muted">
                {kr.measurable === false ? '—' : percentLabel(krProgress(kr))}
              </span>
            </div>
          ))}
        </div>
      )}
      <LabelChips labels={ticketLabels(ticket)} className="mt-2" />
      {ticket.attachment_count > 0 && (
        <p className="text-[11px] text-muted mt-2">
          {ticket.attachment_count} attachment{ticket.attachment_count === 1 ? '' : 's'}
        </p>
      )}
      {ticket.assignee && (
        <p className="text-xs text-muted mt-2 truncate">{ticket.assignee}</p>
      )}
      <div className="flex gap-2 mt-3">
        <button
          type="button"
          onClick={() => onDiscuss(ticket)}
          className="text-xs px-2 py-1 rounded-lg border border-border hover:bg-surface min-h-[32px] transition-colors duration-150"
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

function formatAttachmentSize(bytes) {
  const size = Number(bytes) || 0
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function isImageAttachment(attachment) {
  return String(attachment?.content_type || '').startsWith('image/')
}

const ATTACHMENT_ACCEPT =
  'image/png,image/jpeg,image/webp,image/gif,application/pdf,text/plain,text/markdown,text/csv,application/json,.md'
const MAX_TICKET_ATTACHMENTS = 10
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024

function attachmentSizeError(file) {
  if ((file?.size || 0) > MAX_ATTACHMENT_BYTES) {
    return `${file.name} is larger than 10 MB`
  }
  return ''
}

function pendingFileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`
}

function LocalImagePreview({ file }) {
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
      className="mt-2 max-h-48 w-full object-contain rounded-lg bg-elevated border border-border"
    />
  )
}

function AttachmentPreview({ ticketId, attachment }) {
  const [url, setUrl] = useState('')
  const [loadError, setLoadError] = useState(false)

  useEffect(() => {
    if (!isImageAttachment(attachment)) return undefined
    let objectUrl = ''
    let cancelled = false
    setLoadError(false)
    setUrl('')
    fetchTicketAttachmentBlob(ticketId, attachment.id)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
        setLoadError(false)
      })
      .catch(() => {
        if (!cancelled) {
          setUrl('')
          setLoadError(true)
        }
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [ticketId, attachment.id, attachment.content_type])

  if (loadError) {
    return <p className="mt-2 text-xs text-red-600">Could not load image preview</p>
  }
  if (!url) return null
  return (
    <img
      src={url}
      alt={attachment.filename}
      className="mt-2 max-h-48 w-full object-contain rounded-lg bg-elevated border border-border"
    />
  )
}

function TicketAttachments({ ticketId, pendingFiles, onPendingFilesChange, refreshToken = 0 }) {
  const [attachments, setAttachments] = useState([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [dragOver, setDragOver] = useState(false)
  const inputRef = useRef(null)
  const isPending = !ticketId
  const pending = pendingFiles || []

  useEffect(() => {
    if (!ticketId) return undefined
    let cancelled = false
    fetchTicket(ticketId)
      .then((data) => {
        if (!cancelled) setAttachments(data.ticket?.attachments || [])
      })
      .catch(() => {
        if (!cancelled) setError('Could not load attachments')
      })
    return () => {
      cancelled = true
    }
  }, [ticketId, refreshToken])

  const addPendingFiles = (files) => {
    const incoming = Array.from(files || []).filter(Boolean)
    if (!incoming.length) return
    const existingKeys = new Set(pending.map(pendingFileKey))
    const next = [...pending]
    const errors = []
    for (const file of incoming) {
      const sizeError = attachmentSizeError(file)
      if (sizeError) {
        errors.push(sizeError)
        continue
      }
      const key = pendingFileKey(file)
      if (existingKeys.has(key)) continue
      if (next.length >= MAX_TICKET_ATTACHMENTS) {
        setError(`At most ${MAX_TICKET_ATTACHMENTS} attachments per ticket`)
        break
      }
      existingKeys.add(key)
      next.push(file)
    }
    if (errors.length) setError(errors.join('; '))
    else setError('')
    onPendingFilesChange?.(next)
  }

  const uploadFiles = async (files) => {
    const list = Array.from(files || []).filter(Boolean)
    if (!list.length) return
    if (isPending) {
      addPendingFiles(list)
      if (inputRef.current) inputRef.current.value = ''
      return
    }
    if (uploading) {
      setError('Please wait for the current upload to finish')
      return
    }
    setUploading(true)
    setError('')
    const errors = []
    try {
      for (const file of list) {
        const sizeError = attachmentSizeError(file)
        if (sizeError) {
          errors.push(sizeError)
          continue
        }
        try {
          const data = await uploadTicketAttachment(ticketId, file)
          if (data.attachment) {
            setAttachments((prev) => [...prev, data.attachment])
          }
        } catch (err) {
          errors.push(err.message || `Could not upload ${file.name}`)
        }
      }
      if (errors.length) setError(errors.join('; '))
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const handleRemove = async (attachment) => {
    if (!attachment?.id) return
    setError('')
    try {
      await deleteTicketAttachment(ticketId, attachment.id)
      setAttachments((prev) => prev.filter((item) => item.id !== attachment.id))
    } catch (err) {
      setError(err.message || 'Could not delete attachment')
    }
  }

  const currentCount = attachments.length + pending.length
  const dropLabel = uploading
    ? 'Interpreting and uploading…'
    : isPending
      ? 'Drop files or click to attach'
      : 'Drop files or click to attach'

  return (
    <div className="pt-3 border-t border-border space-y-3">
      <div>
        <p className="text-muted text-xs">Attachments</p>
        <p className="text-[11px] text-muted mt-0.5">
          {isPending
            ? 'Screenshots are interpreted when you create the ticket. The next AI step reads them.'
            : 'The next AI step reads these files. Screenshots are interpreted automatically.'}
        </p>
      </div>
      <div className="space-y-2">
        {currentCount === 0 && (
          <p className="text-xs text-muted">No attachments yet.</p>
        )}
        {pending.length > 0 &&
          pending.map((file) => (
            <div key={pendingFileKey(file)} className="rounded-xl px-3 py-2 bg-surface border border-border">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{file.name}</p>
                  <p className="text-[11px] text-muted">
                    {formatAttachmentSize(file.size)}
                    {ticketId ? ' · not uploaded yet' : ''}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() =>
                    onPendingFilesChange?.(pending.filter((item) => pendingFileKey(item) !== pendingFileKey(file)))
                  }
                  className="text-xs text-muted hover:text-red-600 min-h-[32px] px-2"
                >
                  Remove
                </button>
              </div>
              <LocalImagePreview file={file} />
            </div>
          ))}
        {ticketId &&
          attachments.map((attachment) => (
            <div key={attachment.id} className="rounded-xl px-3 py-2 bg-surface border border-border">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{attachment.filename}</p>
                  <p className="text-[11px] text-muted">
                    {formatAttachmentSize(attachment.size_bytes)}
                    {isImageAttachment(attachment) ? ' · screenshot interpreted for AI' : ''}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => handleRemove(attachment)}
                  className="text-xs text-muted hover:text-red-600 min-h-[32px] px-2"
                >
                  Remove
                </button>
              </div>
              <AttachmentPreview ticketId={ticketId} attachment={attachment} />
              {attachment.extracted_text && (
                <details className="mt-2">
                  <summary className="text-[11px] text-muted cursor-pointer">What AI sees</summary>
                  <p className="mt-1 text-xs whitespace-pre-wrap break-words text-text leading-snug">
                    {attachment.extracted_text}
                  </p>
                </details>
              )}
            </div>
          ))}
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <label
        className={`block w-full border border-dashed rounded-xl px-3 py-4 text-center text-sm min-h-[44px] cursor-pointer ${
          dragOver ? 'border-accent bg-accent-muted' : 'border-border hover:bg-elevated'
        }`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          uploadFiles(e.dataTransfer.files)
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ATTACHMENT_ACCEPT}
          className="sr-only"
          onChange={(e) => uploadFiles(e.target.files)}
        />
        {dropLabel}
      </label>
    </div>
  )
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

function TicketModal({ ticket, columns, board, initialStatus, initialParentKey, initialParentKrId, epics, saveError, onSaveError, onClose, onSave, onDelete }) {
  const labelEditorRef = useRef(null)
  const [form, setForm] = useState({
    title: ticket?.title || '',
    description: ticket?.description || '',
    status: ticket?.status || initialStatus || columns[0] || 'To Do',
    assignee: ticket?.assignee || '',
    fix_version: ticket?.fix_version || '',
    issue_type: ticket?.issue_type || 'Task',
    labels: ticketLabels(ticket),
    parent_key: ticket ? ticketParentKey(ticket) : (initialParentKey || ''),
    parent_kr_id: ticketParentKrId(ticket) || initialParentKrId || '',
    okr_cycle: ticket?.okr_cycle || '',
    okr_owner: ticket?.okr_owner || '',
    key_results: keyResultsOf(ticket).length ? keyResultsOf(ticket) : [],
  })
  const isNew = !ticket?.ticket_id
  const isObjectiveType = form.issue_type === 'Objective'
  const isParentType = form.issue_type === 'Objective' || form.issue_type === 'Epic'
  const selectableEpics = (epics || []).filter((epic) => epic.key && epic.key !== ticket?.key)
  const parentGoal = selectableEpics.find((item) => item.key === form.parent_key)
  const parentKrs = isObjective(parentGoal) ? keyResultsOf(parentGoal) : []
  const [pendingFiles, setPendingFiles] = useState([])
  const [attachmentRefreshToken, setAttachmentRefreshToken] = useState(0)
  const [saving, setSaving] = useState(false)

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 modal-overlay" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-elevated w-full sm:max-w-2xl lg:max-w-3xl xl:max-w-4xl rounded-t-xl sm:rounded-xl shadow-card max-h-[90vh] sm:max-h-[85vh] flex flex-col">
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
              className="mt-1 w-full input-field"
            />
          </label>
          <label className="block text-sm">
            <span className="text-muted text-xs">Description</span>
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              rows={12}
              className="mt-1 w-full input-field min-h-[12rem] sm:min-h-[16rem] lg:min-h-[20rem] resize-y"
            />
          </label>
          <label className="block text-sm">
            <span className="text-muted text-xs">Issue type</span>
            <select
              value={form.issue_type}
              onChange={(e) => {
                const issue_type = e.target.value
                setForm({
                  ...form,
                  issue_type,
                  parent_key: issue_type === 'Objective' || issue_type === 'Epic' ? '' : form.parent_key,
                  parent_kr_id: issue_type === 'Objective' || issue_type === 'Epic' ? '' : form.parent_kr_id,
                })
              }}
              className="mt-1 w-full input-field"
            >
              <option value="Task">Task</option>
              <option value="Bug">Bug</option>
              <option value="Epic">Epic</option>
              <option value="Objective">Objective</option>
            </select>
          </label>
          {isObjectiveType && (
            <div className="space-y-3 border border-border rounded-xl p-3 bg-surface/50">
              <p className="text-xs font-medium">Key Results</p>
              <p className="text-[11px] text-muted">
                3–5 measurable outcomes. If a KR cannot be scored, say what instrumentation is missing.
                Drag to Research to let Bigas propose these.
              </p>
              <div className="grid sm:grid-cols-2 gap-2">
                <label className="block text-sm">
                  <span className="text-muted text-xs">Cycle</span>
                  <input
                    value={form.okr_cycle}
                    onChange={(e) => setForm({ ...form, okr_cycle: e.target.value })}
                    placeholder="2026-Q3"
                    className="mt-1 w-full input-field min-h-[40px]"
                  />
                </label>
                <label className="block text-sm">
                  <span className="text-muted text-xs">Owner</span>
                  <input
                    value={form.okr_owner}
                    onChange={(e) => setForm({ ...form, okr_owner: e.target.value })}
                    placeholder="Chief of Staff"
                    className="mt-1 w-full input-field min-h-[40px]"
                  />
                </label>
              </div>
              {form.key_results.map((kr, index) => (
                <div key={kr.id || index} className="border border-border rounded-lg p-3 bg-elevated space-y-2">
                  <input
                    value={kr.title}
                    onChange={(e) => {
                      const key_results = [...form.key_results]
                      key_results[index] = { ...kr, title: e.target.value }
                      setForm({ ...form, key_results })
                    }}
                    placeholder="KR title"
                    className="w-full border border-border rounded-lg px-2 py-2 text-sm"
                  />
                  <div className="grid grid-cols-3 gap-2">
                    {['baseline', 'current', 'target'].map((field) => (
                      <label key={field} className="text-[11px] text-muted">
                        {field}
                        <input
                          type="number"
                          value={kr[field] ?? ''}
                          onChange={(e) => {
                            const key_results = [...form.key_results]
                            key_results[index] = { ...kr, [field]: e.target.value }
                            setForm({ ...form, key_results })
                          }}
                          className="mt-1 w-full border border-border rounded-lg px-2 py-1.5 text-sm text-text"
                        />
                      </label>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-2 items-center">
                    <select
                      value={kr.source || 'manual'}
                      onChange={(e) => {
                        const key_results = [...form.key_results]
                        key_results[index] = { ...kr, source: e.target.value }
                        setForm({ ...form, key_results })
                      }}
                      className="text-xs border border-border rounded-lg px-2 py-1.5 bg-elevated"
                    >
                      <option value="ga4">ga4</option>
                      <option value="github">github</option>
                      <option value="jira">jira</option>
                      <option value="ads">ads</option>
                      <option value="manual">manual</option>
                      <option value="unknown">unknown</option>
                    </select>
                    <label className="text-xs text-muted flex items-center gap-1">
                      <input
                        type="checkbox"
                        checked={kr.measurable !== false}
                        onChange={(e) => {
                          const key_results = [...form.key_results]
                          key_results[index] = { ...kr, measurable: e.target.checked }
                          setForm({ ...form, key_results })
                        }}
                      />
                      Measurable
                    </label>
                    <button
                      type="button"
                      onClick={() =>
                        setForm({
                          ...form,
                          key_results: form.key_results.filter((_, i) => i !== index),
                        })
                      }
                      className="text-xs text-muted ml-auto"
                    >
                      Remove
                    </button>
                  </div>
                  {kr.measurable === false && (
                    <textarea
                      value={kr.measurement_gap || ''}
                      onChange={(e) => {
                        const key_results = [...form.key_results]
                        key_results[index] = { ...kr, measurement_gap: e.target.value }
                        setForm({ ...form, key_results })
                      }}
                      placeholder="What must exist before this can be scored?"
                      className="w-full border border-border rounded-lg px-2 py-2 text-xs"
                      rows={2}
                    />
                  )}
                </div>
              ))}
              <button
                type="button"
                onClick={() =>
                  setForm({ ...form, key_results: [...form.key_results, emptyKeyResult()] })
                }
                className="text-sm px-3 py-2 rounded-xl border border-dashed border-border min-h-[40px] w-full"
              >
                + Key Result
              </button>
            </div>
          )}
          {!isParentType && (
            <>
            <label className="block text-sm">
              <span className="text-muted text-xs">Parent (Epic or Objective)</span>
              <select
                value={form.parent_key || ''}
                onChange={(e) => setForm({ ...form, parent_key: e.target.value, parent_kr_id: '' })}
                className="mt-1 w-full input-field"
              >
                <option value="">None</option>
                {selectableEpics.map((epic) => (
                  <option key={epic.key} value={epic.key}>
                    {objectiveChipLabel(epic)}
                  </option>
                ))}
              </select>
            </label>
            {parentKrs.length > 0 && (
              <label className="block text-sm">
                <span className="text-muted text-xs">Key Result</span>
                <select
                  value={form.parent_kr_id || ''}
                  onChange={(e) => setForm({ ...form, parent_kr_id: e.target.value })}
                  className="mt-1 w-full input-field"
                >
                  <option value="">Whole objective</option>
                  {parentKrs.map((kr) => (
                    <option key={kr.id} value={kr.id}>
                      {kr.title}
                    </option>
                  ))}
                </select>
              </label>
            )}
            </>
          )}
          <label className="block text-sm">
            <span className="text-muted text-xs">Status</span>
            <select
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
              className="mt-1 w-full input-field"
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
              className="mt-1 w-full input-field"
            />
          </label>
          {board?.workflow_enabled && (
            <label className="block text-sm">
              <span className="text-muted text-xs">Fix version</span>
              <input
                value={form.fix_version}
                onChange={(e) => setForm({ ...form, fix_version: e.target.value })}
                placeholder="e.g. v1.2.0"
                className="mt-1 w-full input-field"
              />
            </label>
          )}
          <LabelEditorWithRef
            ref={labelEditorRef}
            labels={form.labels}
            onChange={(labels) => setForm({ ...form, labels })}
          />
          <TicketAttachments
            ticketId={isNew ? null : ticket.ticket_id}
            pendingFiles={pendingFiles}
            onPendingFilesChange={setPendingFiles}
            refreshToken={attachmentRefreshToken}
          />
          {!isNew && <TicketComments ticketId={ticket.ticket_id} />}
          {saveError && <p className="text-xs text-red-600">{saveError}</p>}
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
            onClick={async () => {
              if (saving) return
              const labels = labelEditorRef.current?.flushDraft?.() ?? form.labels
              setSaving(true)
              onSaveError?.('')
              try {
                const result = await onSave({
                  ...form,
                  labels,
                  parent_key: isParentType ? '' : form.parent_key,
                  parent_kr_id: isParentType ? '' : form.parent_kr_id,
                  files: pendingFiles,
                })
                if (result?.failedFiles?.length) {
                  setPendingFiles(result.failedFiles)
                  setAttachmentRefreshToken((value) => value + 1)
                }
              } catch (err) {
                onSaveError?.(err.message || 'Could not save ticket')
              } finally {
                setSaving(false)
              }
            }}
            disabled={!form.title.trim() || saving}
            className="flex-1 btn-accent rounded-lg py-2 min-h-[44px] order-2 sm:order-3 disabled:opacity-50"
          >
            {saving
              ? isNew && pendingFiles.length
                ? 'Creating and interpreting…'
                : 'Saving…'
              : isNew
                ? 'Create'
                : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

function BoardSidebar({ boards, activeBoardId, onSelect, onCreate, onDelete, onLogout, mobileOpen, onClose }) {
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
        <div className="fixed inset-0 bg-overlay z-40 lg:hidden" onClick={onClose} aria-hidden="true" />
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
                  className={`flex-1 text-left px-3 py-2.5 rounded-lg text-sm min-h-[44px] truncate transition-all duration-150 ${
                    active
                      ? 'nav-item-active font-medium'
                      : 'hover:bg-elevated text-text'
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
        <div className="p-3 border-t border-border space-y-2">
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
                  className="flex-1 btn-accent rounded-lg text-sm py-2"
                >
                  Add
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setShowCreate(true)}
              className="w-full text-sm py-2 rounded-lg border border-dashed border-border hover:bg-elevated min-h-[44px] transition-colors duration-150"
            >
              + New board
            </button>
          )}
          <button
            type="button"
            onClick={onLogout}
            className="lg:hidden w-full text-sm text-muted py-2 rounded-lg border border-border hover:bg-elevated min-h-[44px]"
          >
            Log out
          </button>
        </div>
      </aside>
    </>
  )
}

export default function BoardLayout({ user, onLogout, onDiscussTicket, onSwitchView, onOpenSettings, boardRefreshKey = 0 }) {
  const [boards, setBoards] = useState([])
  const [activeBoardId, setActiveBoardId] = useState(null)
  const [tickets, setTickets] = useState([])
  const [loading, setLoading] = useState(true)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [modalTicket, setModalTicket] = useState(null)
  const [modalSaveError, setModalSaveError] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [createStatus, setCreateStatus] = useState(null)
  const [dragTicket, setDragTicket] = useState(null)
  const [pendingTicketKey, setPendingTicketKey] = useState(null)
  const [syncMessage, setSyncMessage] = useState('')
  const [epicFilter, setEpicFilter] = useState(() => filterFromQuery(boardQuery()))
  const optimisticTicketsRef = useRef([])

  const activeBoard = boards.find((b) => b.board_id === activeBoardId)
  const columns = activeBoard?.columns || ['To Do', 'In Progress', 'Review', 'Done']
  const epicOptions = objectiveOptionsFromTickets(tickets)
  const epicsByKey = Object.fromEntries(epicOptions.map((epic) => [epic.key, epic]))
  const krsById = Object.fromEntries(
    tickets.flatMap((ticket) => keyResultsOf(ticket).filter((kr) => kr.id).map((kr) => [kr.id, kr])),
  )
  const visibleTickets = tickets.filter((ticket) => ticketMatchesObjectiveFilter(ticket, epicFilter))
  const showEpicFilter = epicOptions.length > 0 || tickets.some((ticket) => ticketParentKey(ticket))

  const loadBoards = useCallback(async () => {
    const data = await fetchBoards()
    const list = data.boards || []
    setBoards(list)
    setActiveBoardId((current) => {
      if (current && list.some((board) => String(board.board_id) === String(current))) return current
      if (!list.length) return null
      const wanted = boardQuery().boardId
      const match = list.find((board) => String(board.board_id) === wanted)
      return match?.board_id || list[0].board_id
    })
  }, [])

  const trackOptimisticTicket = useCallback((ticket) => {
    if (!ticket?.ticket_id) return
    optimisticTicketsRef.current = upsertTicket(optimisticTicketsRef.current, ticket)
    setTickets((prev) => upsertTicket(prev, ticket))
  }, [])

  const loadTickets = useCallback(async () => {
    if (!activeBoardId) return
    const data = await fetchBoardTickets(activeBoardId)
    const incoming = data.tickets || []
    optimisticTicketsRef.current = optimisticTicketsRef.current.filter(
      (ticket) => !incoming.some((item) => item.ticket_id === ticket.ticket_id),
    )
    const merged = optimisticTicketsRef.current.reduce(
      (acc, ticket) => upsertTicket(acc, ticket),
      incoming,
    )
    setTickets(merged)
  }, [activeBoardId])

  useEffect(() => {
    loadBoards().finally(() => setLoading(false))
  }, [loadBoards])

  useEffect(() => {
    optimisticTicketsRef.current = []
  }, [activeBoardId])

  useEffect(() => {
    loadTickets()
    const id = setInterval(() => loadTickets(), 5000)
    return () => clearInterval(id)
  }, [loadTickets])

  useEffect(() => {
    if (!boardRefreshKey) return
    loadBoards()
    loadTickets()
  }, [boardRefreshKey, loadBoards, loadTickets])

  useEffect(() => {
    if (!activeBoardId) return
    const query = boardQuery()
    if (query.boardId === String(activeBoardId)) return
    replaceBoardUrl(activeBoardId, { keepFilters: !query.boardId })
  }, [activeBoardId])

  const syncBoardStateFromUrl = useCallback(() => {
    const query = boardQuery()
    if (query.boardId && boards.length) {
      const match = boards.find((board) => String(board.board_id) === query.boardId)
      if (match) {
        setActiveBoardId((current) =>
          String(current) === String(match.board_id) ? current : match.board_id,
        )
      }
    }
    const nextFilter = filterFromQuery(query)
    setEpicFilter(nextFilter)
    if (query.ticketKey) setPendingTicketKey(query.ticketKey)
    else setPendingTicketKey(null)
  }, [boards])

  useEffect(() => {
    window.addEventListener('popstate', syncBoardStateFromUrl)
    window.addEventListener(BOARD_URL_SYNC_EVENT, syncBoardStateFromUrl)
    return () => {
      window.removeEventListener('popstate', syncBoardStateFromUrl)
      window.removeEventListener(BOARD_URL_SYNC_EVENT, syncBoardStateFromUrl)
    }
  }, [syncBoardStateFromUrl])

  useEffect(() => {
    const next = filterFromQuery(boardQuery())
    if (next) {
      setEpicFilter(next)
      return
    }
    setEpicFilter('')
  }, [activeBoardId])

  useEffect(() => {
    const ticketKey = boardQuery().ticketKey
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
    const files = Array.from(form.files || []).filter(Boolean)
    const payload = { ...form }
    delete payload.files

    const uploadPendingFiles = async (ticketId) => {
      const failedFiles = []
      let uploadError = ''
      for (const file of files) {
        const sizeError = attachmentSizeError(file)
        if (sizeError) {
          failedFiles.push(file)
          uploadError = uploadError || sizeError
          continue
        }
        try {
          await uploadTicketAttachment(ticketId, file)
        } catch (err) {
          failedFiles.push(file)
          uploadError = uploadError || err.message || 'Could not upload attachment'
        }
      }
      if (failedFiles.length) {
        setModalSaveError(uploadError)
        await loadTickets()
        return { failedFiles }
      }
      return null
    }

    const revealCreatedTicket = (ticket) => {
      if (!ticket?.ticket_id) return
      trackOptimisticTicket(ticket)
      if (!ticketMatchesObjectiveFilter(ticket, epicFilter)) {
        clearObjectiveFilterFromUrl()
      }
    }

    if (modalTicket?.ticket_id) {
      await updateTicket(modalTicket.ticket_id, payload)
      if (files.length) {
        const uploadResult = await uploadPendingFiles(modalTicket.ticket_id)
        if (uploadResult?.failedFiles?.length) {
          return uploadResult
        }
      }
    } else {
      const data = await createTicket(activeBoardId, payload)
      const createdTicket = data.ticket
      revealCreatedTicket(createdTicket)
      if (createdTicket?.ticket_id && files.length) {
        const uploadResult = await uploadPendingFiles(createdTicket.ticket_id)
        if (uploadResult?.failedFiles?.length) {
          setModalTicket(createdTicket)
          setShowCreate(false)
          setCreateStatus(null)
          return uploadResult
        }
      }
    }
    setModalSaveError('')
    setModalTicket(null)
    setShowCreate(false)
    setCreateStatus(null)
    await loadTickets()
  }

  const openEditTicket = (ticket) => {
    setModalSaveError('')
    setModalTicket(ticket)
  }

  const openCreate = (status) => {
    setModalSaveError('')
    setModalTicket(null)
    setCreateStatus(status || columns[0] || 'To Do')
    setShowCreate(true)
  }

  const closeModal = () => {
    setModalSaveError('')
    setModalTicket(null)
    setShowCreate(false)
    setCreateStatus(null)
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

  const formatSyncResult = (result) =>
    `Jira sync: ${result?.created || 0} new, ${result?.updated || 0} updated` +
    (result?.skipped ? `, ${result.skipped} skipped` : '') +
    (result?.errors ? `, ${result.errors} errors` : '')

  const pollJiraSyncUntilDone = useCallback(async (boardId) => {
    for (let attempt = 0; attempt < 120; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000))
      const data = await fetchBoardJiraSyncStatus(boardId)
      const status = data.jira_sync?.status
      if (status === 'running') continue
      if (status === 'completed') {
        return { message: formatSyncResult(data.jira_sync?.result) }
      }
      if (status === 'failed') {
        throw new Error(data.jira_sync?.error || 'Jira sync failed')
      }
      return { message: 'Jira sync finished' }
    }
    throw new Error('Jira sync timed out')
  }, [])

  useEffect(() => {
    if (!activeBoardId || activeBoard?.jira_sync?.status !== 'running') return

    let cancelled = false
    setSyncMessage('Jira sync running in background…')

    pollJiraSyncUntilDone(activeBoardId)
      .then(async ({ message }) => {
        if (cancelled) return
        setSyncMessage(message)
        await loadTickets()
        await loadBoards()
      })
      .catch((err) => {
        if (cancelled) return
        setSyncMessage(err.message || 'Jira sync failed')
      })

    return () => {
      cancelled = true
    }
  }, [activeBoardId, activeBoard?.jira_sync?.status, loadBoards, loadTickets, pollJiraSyncUntilDone])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg text-muted">
        Loading board…
      </div>
    )
  }

  return (
    <div className="h-screen-safe flex flex-col lg:flex-row overflow-hidden bg-bg mobile-nav-offset">
      <BoardSidebar
        boards={boards}
        activeBoardId={activeBoardId}
        onSelect={setActiveBoardId}
        onCreate={handleCreateBoard}
        onDelete={handleDeleteBoard}
        onLogout={onLogout}
        mobileOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <main className="flex-1 flex flex-col min-w-0">
        <header className="header-bar px-3 sm:px-4 py-3 flex flex-col gap-2 lg:flex-row lg:items-center">
          <div className="flex items-center gap-2 min-w-0 lg:flex-1">
            <SettingsButton onClick={onOpenSettings} />
            <ThemeToggle />
            <button
              type="button"
              className="lg:hidden p-2 min-w-[44px] min-h-[44px] border border-border rounded-lg bg-elevated"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open boards"
            >
              ☰
            </button>
            <div className="flex-1 min-w-0">
              <h1 className="font-bold truncate">{activeBoard?.name || 'Board'}</h1>
              {activeBoard?.workflow_enabled && (
                <p className={`text-xs text-muted truncate ${syncMessage ? '' : 'hidden lg:block'}`}>
                  {syncMessage || 'AI workflow enabled'}
                </p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 min-w-0">
            {showEpicFilter && (
              <select
                value={epicFilter}
                onChange={(e) => setEpicFilter(e.target.value)}
                className="flex-1 min-w-0 text-sm px-2 py-2 rounded-lg border border-border min-h-[44px] lg:flex-none lg:max-w-[220px] bg-elevated input-field py-2"
                aria-label="Filter by objective"
              >
                <option value="">All tickets</option>
                <option value="__none__">No objective</option>
                {epicFilter.startsWith('kr:') && (
                  <option value={epicFilter}>
                    KR · {krsById[epicFilter.slice(3)]?.title || epicFilter.slice(3)}
                  </option>
                )}
                {epicOptions.map((epic) => (
                  <option key={epic.key} value={epic.key}>
                    {objectiveChipLabel(epic)}
                  </option>
                ))}
              </select>
            )}
            <button
              type="button"
              onClick={() => onSwitchView('objectives')}
              className="text-sm btn-secondary px-3 py-2 hidden lg:inline-flex"
            >
              Objectives
            </button>
            <button
              type="button"
              onClick={() => onSwitchView('chat')}
              className="text-sm btn-secondary px-3 py-2 hidden lg:inline-flex"
            >
              Chat
            </button>
            <button
              type="button"
              onClick={() => openCreate(columns[0])}
              className="flex-shrink-0 btn-accent font-medium px-3 py-2 rounded-lg min-h-[44px] text-sm"
            >
              + Ticket
            </button>
            <button
              type="button"
              onClick={onLogout}
              className="hidden lg:inline-flex text-sm text-muted px-2 min-h-[44px] items-center"
            >
              Log out
            </button>
          </div>
        </header>

        {/* Mobile: horizontal snap-scroll kanban */}
        <div className="flex-1 overflow-x-auto overflow-y-hidden lg:hidden snap-x-mandatory scrollbar-hide flex gap-3 p-3">
          {columns.map((col) => {
            const colTickets = visibleTickets.filter((t) => t.status === col)
            return (
              <section
                key={col}
                className="flex-shrink-0 w-[85vw] max-w-sm snap-start flex flex-col bg-surface rounded-lg border border-border max-h-full"
              >
                <h3 className="px-3 py-2 text-sm font-semibold border-b border-border flex justify-between flex-shrink-0">
                  <span className="truncate">{col}</span>
                  <span className="text-muted font-normal">{colTickets.length}</span>
                </h3>
                <div className="flex-1 overflow-y-auto p-2 space-y-2 min-h-0">
                  {colTickets.map((ticket) => (
                    <TicketCard
                      key={ticket.ticket_id}
                      ticket={ticket}
                      parentEpic={epicsByKey[ticketParentKey(ticket)]}
                      parentKr={krsById[ticketParentKrId(ticket)]}
                      columns={columns}
                      onEdit={openEditTicket}
                      onStatusChange={handleStatusChange}
                      onDiscuss={onDiscussTicket}
                      onFilterEpic={setEpicFilter}
                      dragging={dragTicket?.ticket_id === ticket.ticket_id}
                      onDragStart={(_, t) => setDragTicket(t)}
                      onDragEnd={() => setDragTicket(null)}
                    />
                  ))}
                  {isTodoColumn(col) && (
                    <ColumnCreateButton onClick={() => openCreate(col)} />
                  )}
                </div>
              </section>
            )
          })}
        </div>

        {/* Desktop: horizontal kanban */}
        <div className="hidden lg:flex flex-1 overflow-x-auto p-4 gap-3">
          {columns.map((col) => {
            const colTickets = visibleTickets.filter((t) => t.status === col)
            return (
              <section
                key={col}
                className="group flex-shrink-0 w-72 flex flex-col bg-surface rounded-lg border border-border"
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
                      parentEpic={epicsByKey[ticketParentKey(ticket)]}
                      parentKr={krsById[ticketParentKrId(ticket)]}
                      columns={columns}
                      onEdit={openEditTicket}
                      onStatusChange={handleStatusChange}
                      onDiscuss={onDiscussTicket}
                      onFilterEpic={setEpicFilter}
                      dragging={dragTicket?.ticket_id === ticket.ticket_id}
                      onDragStart={(_, t) => setDragTicket(t)}
                      onDragEnd={() => setDragTicket(null)}
                    />
                  ))}
                  {isTodoColumn(col) && (
                    <ColumnCreateButton hiddenUntilHover onClick={() => openCreate(col)} />
                  )}
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
          initialStatus={createStatus}
          initialParentKey={parentKeyFromFilter(epicFilter, tickets)}
          initialParentKrId={parentKrIdFromFilter(epicFilter)}
          epics={epicOptions}
          saveError={modalSaveError}
          onSaveError={setModalSaveError}
          onClose={closeModal}
          onSave={handleSave}
          onDelete={handleDeleteTicket}
        />
      )}
    </div>
  )
}
