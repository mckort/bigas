import { useCallback, useEffect, useState } from 'react'
import {
  createBoard,
  createTicket,
  deleteBoard,
  deleteTicket,
  fetchBoards,
  fetchBoardTickets,
  updateTicket,
} from '../lib/api'

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

function TicketCard({ ticket, columns, onEdit, onStatusChange, onDiscuss, dragging, onDragStart, onDragEnd }) {
  return (
    <div
      draggable
      onDragStart={(e) => onDragStart(e, ticket)}
      onDragEnd={onDragEnd}
      className={`bg-white border border-border rounded-xl p-3 shadow-soft cursor-grab active:cursor-grabbing ${
        dragging ? 'opacity-50' : ''
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <span className="text-[11px] font-mono text-muted">{ticket.key}</span>
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

function TicketModal({ ticket, columns, board, onClose, onSave, onDelete }) {
  const [form, setForm] = useState({
    title: ticket?.title || '',
    description: ticket?.description || '',
    status: ticket?.status || columns[0] || 'To Do',
    assignee: ticket?.assignee || '',
    fix_version: ticket?.fix_version || '',
    issue_type: ticket?.issue_type || 'Task',
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
        </div>
        <div className="p-4 border-t border-border flex flex-col sm:flex-row gap-2">
          {!isNew && (
            <button
              type="button"
              onClick={() => onDelete(ticket)}
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
            onClick={() => onSave(form)}
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

  const activeBoard = boards.find((b) => b.board_id === activeBoardId)
  const columns = activeBoard?.columns || ['To Do', 'In Progress', 'Review', 'Done']

  const loadBoards = useCallback(async () => {
    const data = await fetchBoards()
    setBoards(data.boards || [])
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
    const ticketKey = params.get('ticket')
    if (ticketKey && tickets.length) {
      const found = tickets.find((t) => t.key === ticketKey)
      if (found) setModalTicket(found)
    }
  }, [tickets])

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
              <p className="text-xs text-muted">AI workflow enabled</p>
            )}
          </div>
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
