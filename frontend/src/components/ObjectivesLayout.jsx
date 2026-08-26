import { useEffect, useState } from 'react'
import { createTicket, deleteTicket, fetchObjectives, fetchTicket, updateTicket } from '../lib/api'
import { SettingsButton } from './AgentSettings'
import { healthLabel, percentLabel } from '../lib/okr'

function healthClass(health) {
  if (health === 'on_track') return 'bg-emerald-50 text-emerald-900 border-emerald-200'
  if (health === 'at_risk') return 'bg-amber-50 text-amber-900 border-amber-200'
  if (health === 'off_track') return 'bg-red-50 text-red-800 border-red-200'
  return 'bg-surface text-muted border-border'
}

function ProgressTrack({ value, expected, label }) {
  const pct = Math.max(0, Math.min(100, Math.round((value || 0) * 100)))
  const exp = Math.max(0, Math.min(100, Math.round((expected || 0) * 100)))
  return (
    <div>
      <div className="flex justify-between text-[11px] text-muted mb-1">
        <span>{label}</span>
        <span>
          {percentLabel(value)}
          {expected != null ? ` · expected ${percentLabel(expected)}` : ''}
        </span>
      </div>
      <div className="relative h-2 rounded-full bg-black/5 overflow-hidden">
        <div className="absolute inset-y-0 left-0 bg-bigas-blue" style={{ width: `${pct}%` }} />
        <div
          className="absolute top-0 bottom-0 w-px bg-black/50"
          style={{ left: `${exp}%` }}
          title="Expected pace"
        />
      </div>
    </div>
  )
}

function StatusCard({ label, value, tone = 'default' }) {
  const tones = {
    default: 'border-border bg-white',
    good: 'border-emerald-200 bg-emerald-50',
    warn: 'border-amber-200 bg-amber-50',
    bad: 'border-red-200 bg-red-50',
    muted: 'border-border bg-surface',
  }
  return (
    <div className={`rounded-2xl border p-4 min-h-[5.5rem] ${tones[tone] || tones.default}`}>
      <p className="text-2xl font-semibold leading-none">{value}</p>
      <p className="text-xs text-muted mt-2">{label}</p>
    </div>
  )
}

function linkedTicketCount(objective) {
  const keys = new Set()
  for (const kr of objective.key_results || []) {
    for (const ticket of kr.tickets || []) {
      if (ticket.key) keys.add(ticket.key)
    }
  }
  for (const ticket of objective.unlinked_tickets || []) {
    if (ticket.key) keys.add(ticket.key)
  }
  return keys.size
}

function DeleteObjectiveDialog({ objective, onCancel, onConfirm, busy }) {
  const [deleteChildren, setDeleteChildren] = useState(false)
  const count = linkedTicketCount(objective)
  return (
    <div className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/30" onClick={busy ? undefined : onCancel} aria-hidden="true" />
      <div className="relative bg-white w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl shadow-card p-5 space-y-4">
        <h3 className="font-semibold">Delete {objective.key}?</h3>
        <p className="text-sm text-muted leading-relaxed">
          This removes the Objective. Linked tasks stay on the board unless you choose to delete them too.
        </p>
        {count > 0 && (
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              checked={deleteChildren}
              onChange={(e) => setDeleteChildren(e.target.checked)}
              className="mt-1"
            />
            <span>Also delete {count} linked task{count === 1 ? '' : 's'}</span>
          </label>
        )}
        <div className="flex flex-wrap gap-2 justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="text-sm px-3 py-2 rounded-xl border border-border min-h-[44px] disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(deleteChildren)}
            disabled={busy}
            className="text-sm px-3 py-2 rounded-xl bg-red-600 text-white min-h-[44px] disabled:opacity-50"
          >
            {busy ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  )
}

function formatSignedOff(kr) {
  if (!kr?.signed_off_at) return ''
  const when = new Date(kr.signed_off_at)
  if (Number.isNaN(when.getTime())) return kr.signed_off_at
  const who = kr.signed_off_by ? ` by ${kr.signed_off_by}` : ''
  return `${when.toLocaleDateString()} ${when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}${who}`
}

function KeyResultPanel({ objective, kr, user, onClose, onReload, onShowOnBoard }) {
  const [current, setCurrent] = useState(kr.measurable ? String(kr.current ?? '') : '')
  const [taskTitle, setTaskTitle] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const signOff = async () => {
    if (!objective.ticket_id) return
    setBusy('sign')
    setError('')
    try {
      const live = await fetchTicket(objective.ticket_id)
      const existing = live.ticket?.key_results || objective.key_results || []
      const next = existing.map((item) =>
        item.id === kr.id
          ? {
              ...item,
              current: Number(current),
              measurable: true,
              signed_off_at: new Date().toISOString(),
              signed_off_by: user?.email || '',
            }
          : item,
      )
      await updateTicket(objective.ticket_id, { key_results: next })
      await onReload()
    } catch (err) {
      setError(err.message || 'Could not sign off')
    } finally {
      setBusy('')
    }
  }

  const createLinkedTask = async () => {
    const title = taskTitle.trim()
    if (!title || !objective.board_id) return
    setBusy('create')
    setError('')
    try {
      await createTicket(objective.board_id, {
        title,
        description: `Created from KR “${kr.title}” on ${objective.key}.`,
        issue_type: 'Task',
        status: 'To Do',
        parent_key: objective.key,
        parent_kr_id: kr.id,
        labels: ['okr'],
      })
      setTaskTitle('')
      await onReload()
    } catch (err) {
      setError(err.message || 'Could not create task')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} aria-hidden="true" />
      <div className="relative bg-white w-full sm:max-w-2xl rounded-t-2xl sm:rounded-2xl shadow-card max-h-[92vh] flex flex-col">
        <div className="p-4 border-b border-border flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <p className="text-[11px] font-mono text-muted">
              {objective.key} · Key Result
            </p>
            <h3 className="font-semibold leading-snug mt-0.5">{kr.title}</h3>
          </div>
          <span className={`text-[10px] px-1.5 py-0.5 rounded-md border flex-shrink-0 ${healthClass(kr.health)}`}>
            {healthLabel(kr.health)}
          </span>
          <button type="button" onClick={onClose} className="p-2 min-w-[44px] min-h-[44px]" aria-label="Close">
            ✕
          </button>
        </div>

        <div className="p-4 overflow-y-auto flex-1 space-y-5">
          {error && <p className="text-sm text-red-600">{error}</p>}

          <section className="border border-border rounded-2xl p-4 space-y-3">
            <p className="text-sm text-muted">
              {kr.metric}
              {kr.unit ? ` · ${kr.unit}` : ''} · {kr.source}
            </p>
            {kr.measurable ? (
              <p className="text-lg font-semibold">
                {kr.current} / {kr.target}
              </p>
            ) : (
              <p className="text-sm">{kr.measurement_gap || 'Not measurable yet.'}</p>
            )}
            <ProgressTrack
              value={kr.progress}
              expected={kr.expected_progress}
              label={kr.measurable ? 'Progress vs expected pace' : 'Cannot score yet'}
            />
            {kr.activity_without_outcome && (
              <p className="text-sm">Tickets closed, metric barely moved.</p>
            )}
            {kr.ai_note && <p className="text-[11px] text-muted">{kr.ai_note}</p>}
          </section>

          <section className="border border-border rounded-2xl p-4 space-y-3">
            <p className="text-xs font-medium">Human sign-off</p>
            <p className="text-[11px] text-muted">
              Current is signed by a person. Closed tasks are not a score.
            </p>
            {kr.signed_off_at && (
              <p className="text-[11px] text-muted">Last signed {formatSignedOff(kr)}</p>
            )}
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                type="number"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                className="flex-1 border border-border rounded-xl px-3 py-2 min-h-[44px]"
                placeholder="Current value"
              />
              <button
                type="button"
                onClick={signOff}
                disabled={busy === 'sign' || current === ''}
                className="px-4 py-2 rounded-xl bg-bigas-blue text-bigas-black font-medium min-h-[44px] disabled:opacity-50"
              >
                {busy === 'sign' ? 'Saving…' : 'Sign off'}
              </button>
            </div>
          </section>

          <section className="border border-border rounded-2xl p-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium">Linked tasks</p>
              <p className="text-[11px] text-muted">
                {kr.linked_done || 0} done · {kr.linked_open || 0} open
              </p>
            </div>
            <p className="text-[11px] text-muted">
              Work for this KR lives on the board. Filter there to move cards.
            </p>
            <button
              type="button"
              onClick={() => onShowOnBoard(objective, kr)}
              className="w-full text-sm px-3 py-2 rounded-xl border border-border min-h-[44px]"
            >
              Show on board
            </button>
          </section>

          <section className="border border-dashed border-border rounded-2xl p-4 space-y-2">
            <p className="text-xs font-medium">Create task</p>
            <p className="text-[11px] text-muted">Lands in To Do on {objective.board_name || 'the board'}.</p>
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                value={taskTitle}
                onChange={(e) => setTaskTitle(e.target.value)}
                placeholder="Task title"
                className="flex-1 border border-border rounded-xl px-3 py-2 min-h-[44px]"
              />
              <button
                type="button"
                onClick={createLinkedTask}
                disabled={busy === 'create' || !taskTitle.trim()}
                className="px-4 py-2 rounded-xl border border-border min-h-[44px] disabled:opacity-50"
              >
                {busy === 'create' ? 'Creating…' : 'Add to To Do'}
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}

function ObjectiveCard({ objective, onOpenKr, onOpenBoard, onDiscuss, onDelete }) {
  return (
    <article className="border border-border rounded-2xl bg-white overflow-hidden flex flex-col h-full min-w-[18rem]">
      <div className="p-4 sm:p-5 flex-1">
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span className="text-[11px] font-mono text-muted">{objective.key}</span>
          {objective.board_name && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-surface border border-border text-muted">
              {objective.board_name}
            </span>
          )}
          <span className={`text-[10px] px-1.5 py-0.5 rounded-md border ${healthClass(objective.health)}`}>
            {healthLabel(objective.health)}
          </span>
          {objective.phase && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-surface border border-border text-muted">
              {objective.phase}
            </span>
          )}
          {objective.owner && <span className="text-[10px] text-muted">{objective.owner}</span>}
        </div>
        <h3 className="font-semibold leading-snug">{objective.title}</h3>
        {objective.briefing && (
          <p className="text-sm text-muted mt-2 leading-relaxed line-clamp-4">{objective.briefing}</p>
        )}
        <div className="mt-4">
          <ProgressTrack
            value={objective.progress}
            expected={objective.expected_progress}
            label="Objective (mean of measurable KRs)"
          />
        </div>
        <div className="mt-4 space-y-2">
          {(objective.key_results || []).map((kr) => (
            <button
              type="button"
              key={kr.id}
              onClick={() => onOpenKr(objective, kr)}
              className="w-full text-left border border-border rounded-xl p-3 hover:bg-surface/70"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium leading-snug">{kr.title}</p>
                  <p className="text-[11px] text-muted mt-1">
                    {kr.measurable ? `${kr.current} / ${kr.target}` : 'Not measurable'}
                    {` · ${kr.linked_done || 0} done / ${kr.linked_open || 0} open`}
                  </p>
                </div>
                <span className={`text-[10px] px-1.5 py-0.5 rounded-md border flex-shrink-0 ${healthClass(kr.health)}`}>
                  {healthLabel(kr.health)}
                </span>
              </div>
              <div className="mt-2">
                <ProgressTrack
                  value={kr.progress}
                  expected={kr.expected_progress}
                  label={kr.measurable ? 'Progress vs expected pace' : 'Cannot score'}
                />
              </div>
            </button>
          ))}
          {!(objective.key_results || []).length && (
            <p className="text-sm text-muted">
              No Key Results yet. Drag this Objective to Research and describe on the board.
            </p>
          )}
        </div>
      </div>
      <div className="px-4 sm:px-5 py-3 border-t border-border bg-surface/50 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onOpenBoard(objective)}
          className="text-xs px-3 py-2 rounded-lg border border-border min-h-[44px] bg-white"
        >
          Open on board
        </button>
        <button
          type="button"
          onClick={() => onDiscuss(objective)}
          className="text-xs px-3 py-2 rounded-lg border border-border min-h-[44px] bg-white"
        >
          Discuss with Chief of Staff
        </button>
        <button
          type="button"
          onClick={() => onDelete(objective)}
          className="text-xs px-3 py-2 rounded-lg border border-red-200 text-red-700 min-h-[44px] bg-white ml-auto"
        >
          Delete
        </button>
      </div>
    </article>
  )
}

export default function ObjectivesLayout({ user, onLogout, onSwitchView, onDiscussTicket, onOpenSettings }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [activeKr, setActiveKr] = useState(null)
  const [pendingDelete, setPendingDelete] = useState(null)
  const [deleting, setDeleting] = useState(false)

  const load = async () => {
    setError('')
    const payload = await fetchObjectives()
    setData(payload)
    return payload
  }

  useEffect(() => {
    load()
      .catch((err) => setError(err.message || 'Could not load objectives'))
      .finally(() => setLoading(false))
  }, [])

  const openBoard = (objective, kr) => {
    const params = new URLSearchParams()
    if (objective?.board_id) params.set('board', objective.board_id)
    if (kr?.id) params.set('kr', kr.id)
    else if (objective?.key) params.set('objective', objective.key)
    const qs = params.toString()
    window.history.replaceState({}, '', qs ? `/board?${qs}` : '/board')
    onSwitchView?.('board')
  }

  const reloadAndKeepKr = async () => {
    const payload = await load()
    if (!activeKr || !payload) return
    const nextObj = (payload.objectives || []).find((item) => item.key === activeKr.objective.key)
    const nextKr = (nextObj?.key_results || []).find((item) => item.id === activeKr.kr.id)
    if (nextObj && nextKr) setActiveKr({ objective: nextObj, kr: nextKr })
  }

  const discuss = (objective) => {
    onDiscussTicket?.({
      ...objective,
      issue_type: 'Objective',
      title: objective.title,
    })
  }

  const handleDelete = async (deleteChildren) => {
    if (!pendingDelete?.ticket_id) return
    setDeleting(true)
    setError('')
    try {
      await deleteTicket(pendingDelete.ticket_id, { deleteChildren })
      setPendingDelete(null)
      await load()
    } catch (err) {
      setError(err.message || 'Could not delete objective')
    } finally {
      setDeleting(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg text-muted">
        Loading objectives…
      </div>
    )
  }

  const stats = data?.stats || {}
  const briefing = data?.briefing || {}
  const objectives = data?.objectives || []

  return (
    <div className="min-h-screen-safe bg-bg text-text flex flex-col mobile-nav-offset">
      <header className="sticky top-0 z-10 bg-bg/90 backdrop-blur-sm border-b border-border px-4 py-3 flex items-center gap-2">
        <SettingsButton onClick={onOpenSettings} />
        <div className="flex-1 min-w-0">
          <p className="text-[11px] uppercase tracking-wide text-muted">OKR prototype</p>
          <h1 className="font-bold truncate">Objectives · {data?.cycle || 'Current cycle'}</h1>
        </div>
        <button
          type="button"
          onClick={() => onSwitchView('chat')}
          className="text-sm px-3 py-2 rounded-xl border border-border min-h-[44px] hidden lg:block"
        >
          Chat
        </button>
        <button
          type="button"
          onClick={() => onSwitchView('board')}
          className="text-sm px-3 py-2 rounded-xl border border-border min-h-[44px] hidden lg:block"
        >
          Board
        </button>
        <button type="button" onClick={onLogout} className="text-sm text-muted px-2 min-h-[44px]">
          Log out
        </button>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="max-w-7xl mx-auto px-4 py-6 space-y-8">
          {error && <p className="text-sm text-red-600">{error}</p>}

          <section className="space-y-4">
            <div>
              <p className="text-xs text-muted mb-1">
                Chief of Staff briefing · {user?.email || 'local'}
              </p>
              <h2 className="text-xl font-semibold leading-snug">{briefing.headline || 'No objectives yet.'}</h2>
              <p className="text-sm text-muted mt-2 max-w-2xl">{briefing.principle}</p>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3">
              <StatusCard label="Objectives" value={stats.objectives || 0} />
              <StatusCard label="On track" value={stats.on_track || 0} tone="good" />
              <StatusCard label="At risk" value={stats.at_risk || 0} tone="warn" />
              <StatusCard label="Off track" value={stats.off_track || 0} tone="bad" />
              <StatusCard label="Unmeasured" value={stats.unmeasured || 0} tone="muted" />
              <StatusCard label="Busy but stuck" value={stats.activity_without_outcome || 0} tone="warn" />
            </div>
            {(briefing.this_week || []).length > 0 && (
              <div className="grid md:grid-cols-2 gap-4">
                <div className="border border-border rounded-2xl bg-white p-4">
                  <p className="text-xs font-medium mb-2">This week</p>
                  <ul className="space-y-1.5 text-sm">
                    {briefing.this_week.map((item) => (
                      <li key={item} className="leading-snug">
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="border border-border rounded-2xl bg-white p-4">
                  <p className="text-xs font-medium mb-2">Watch</p>
                  <ul className="space-y-1.5 text-sm text-muted">
                    {(briefing.risks || []).slice(0, 4).map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                    {(briefing.unmeasured || []).slice(0, 2).map((item) => (
                      <li key={`u-${item}`}>Unmeasured · {item}</li>
                    ))}
                    {(briefing.activity_without_outcome || []).map((item) => (
                      <li key={`a-${item}`}>Activity without outcome · {item}</li>
                    ))}
                    {!briefing.risks?.length && !briefing.unmeasured?.length && (
                      <li>No active risks in this snapshot.</li>
                    )}
                  </ul>
                </div>
              </div>
            )}
          </section>

          {objectives.length === 0 && (
            <section className="border border-dashed border-border rounded-2xl p-8 text-center bg-surface/60">
              <h3 className="font-semibold">No objectives on your boards yet</h3>
              <p className="text-sm text-muted mt-2 max-w-xl mx-auto">
                Create a ticket with type Objective on a board, then drag it to Research and describe.
              </p>
            </section>
          )}

          {objectives.length > 0 && (
            <div className="grid gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
              {objectives.map((objective) => (
                <ObjectiveCard
                  key={objective.key}
                  objective={objective}
                  onOpenKr={(obj, kr) => setActiveKr({ objective: obj, kr })}
                  onOpenBoard={openBoard}
                  onDiscuss={discuss}
                  onDelete={setPendingDelete}
                />
              ))}
            </div>
          )}
        </div>
      </main>

      {activeKr && (
        <KeyResultPanel
          objective={activeKr.objective}
          kr={activeKr.kr}
          user={user}
          onClose={() => setActiveKr(null)}
          onReload={reloadAndKeepKr}
          onShowOnBoard={openBoard}
        />
      )}
      {pendingDelete && (
        <DeleteObjectiveDialog
          objective={pendingDelete}
          busy={deleting}
          onCancel={() => !deleting && setPendingDelete(null)}
          onConfirm={handleDelete}
        />
      )}
    </div>
  )
}
