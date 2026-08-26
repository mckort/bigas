import { useEffect, useRef, useState } from 'react'
import {
  fetchAgents,
  fetchBoardJiraSyncStatus,
  fetchBoards,
  syncBoardFromJira,
  updateAgent,
} from '../lib/api'

import ThemeToggle from './ThemeToggle'

export function SettingsButton({ onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg hover:bg-surface text-muted hover:text-text transition-all duration-150"
      aria-label="Open settings"
      title="Settings"
    >
      <span aria-hidden="true" className="text-lg leading-none">
        ⚙
      </span>
    </button>
  )
}

function formatSyncResult(result) {
  return (
    `Jira sync: ${result?.created || 0} new, ${result?.updated || 0} updated` +
    (result?.skipped ? `, ${result.skipped} skipped` : '') +
    (result?.errors ? `, ${result.errors} errors` : '')
  )
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const id = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(id)
        reject(new DOMException('Aborted', 'AbortError'))
      },
      { once: true },
    )
  })
}

async function pollJiraSyncUntilDone(boardId, signal) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    await sleep(2000, signal)
    const data = await fetchBoardJiraSyncStatus(boardId)
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    const status = data.jira_sync?.status
    if (status === 'running') continue
    if (status === 'completed') {
      return formatSyncResult(data.jira_sync?.result)
    }
    if (status === 'failed') {
      throw new Error(data.jira_sync?.error || 'Jira sync failed')
    }
    return 'Jira sync finished'
  }
  throw new Error('Jira sync timed out')
}

export default function AgentSettings({ open, onClose, onAgentsUpdated, onJiraSyncComplete }) {
  const [agents, setAgents] = useState([])
  const [selected, setSelected] = useState(null)
  const [name, setName] = useState('')
  const [goals, setGoals] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [boards, setBoards] = useState([])
  const [jiraImportAvailable, setJiraImportAvailable] = useState(false)
  const [syncingBoardId, setSyncingBoardId] = useState('')
  const [syncMessage, setSyncMessage] = useState('')
  const syncAbortRef = useRef(null)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      syncAbortRef.current?.abort()
      syncAbortRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!open) {
      syncAbortRef.current?.abort()
      syncAbortRef.current = null
      return
    }
    fetchAgents().then((res) => {
      if (!mountedRef.current) return
      setAgents(res.agents || [])
      if (res.agents?.length) {
        selectAgent(res.agents[0])
      }
    })
    fetchBoards()
      .then((res) => {
        if (!mountedRef.current) return
        setBoards(res.boards || [])
        setJiraImportAvailable(Boolean(res.jira_import_available))
      })
      .catch(() => {
        if (!mountedRef.current) return
        setBoards([])
        setJiraImportAvailable(false)
      })
  }, [open])

  function selectAgent(agent) {
    setSelected(agent)
    setName(agent.name || '')
    setGoals(agent.system_prompt_goals || '')
    setMessage('')
  }

  function handleClose() {
    onClose?.()
  }

  async function handleSave(e) {
    e.preventDefault()
    if (!selected) return
    setSaving(true)
    setMessage('')
    try {
      await updateAgent(selected.agent_id, {
        name,
        system_prompt_goals: goals,
      })
      setMessage('Saved!')
      const res = await fetchAgents()
      if (!mountedRef.current) return
      setAgents(res.agents || [])
      onAgentsUpdated?.()
    } catch (err) {
      setMessage(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleSyncJira(board) {
    if (!board?.board_id || syncingBoardId) return
    syncAbortRef.current?.abort()
    const controller = new AbortController()
    syncAbortRef.current = controller
    setSyncingBoardId(board.board_id)
    setSyncMessage(`Syncing ${board.name}…`)
    try {
      const data = await syncBoardFromJira(board.board_id)
      if (controller.signal.aborted) return
      if (data.status === 'started' || data.status === 'running') {
        const done = await pollJiraSyncUntilDone(board.board_id, controller.signal)
        if (controller.signal.aborted) return
        setSyncMessage(`${board.name}: ${done}`)
      } else {
        setSyncMessage(`${board.name}: ${formatSyncResult(data)}`)
      }
      onJiraSyncComplete?.()
    } catch (err) {
      if (err?.name === 'AbortError') return
      setSyncMessage(err.message || 'Jira sync failed')
    } finally {
      if (syncAbortRef.current === controller) syncAbortRef.current = null
      if (!controller.signal.aborted && mountedRef.current) setSyncingBoardId('')
    }
  }

  if (!open) return null

  const syncableBoards = boards.filter((board) => board.workflow_enabled)

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 modal-overlay" onClick={handleClose} aria-hidden="true" />
      <div className="relative w-full sm:max-w-2xl max-h-[90vh] overflow-y-auto bg-elevated border border-border rounded-t-xl sm:rounded-xl p-5 sm:p-6 shadow-card">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold">Settings</h2>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <button
              type="button"
              onClick={handleClose}
              className="text-muted hover:text-text p-2 min-w-[44px] min-h-[44px] flex items-center justify-center"
              aria-label="Close settings"
            >
              ✕
            </button>
          </div>
        </div>

        <h3 className="text-sm font-medium mb-2">Agents</h3>
        <div className="flex flex-wrap gap-2 mb-4">
          {agents.map((a) => (
            <button
              key={a.agent_id}
              type="button"
              onClick={() => selectAgent(a)}
              className={`px-3 py-2 rounded-lg text-sm border min-h-[44px] transition-all duration-150 ${
                selected?.agent_id === a.agent_id
                  ? 'nav-item-active'
                  : 'border-border hover:bg-surface'
              }`}
            >
              {a.icon} {a.name}
            </button>
          ))}
        </div>

        {selected && (
          <form onSubmit={handleSave} className="space-y-4">
            <div>
              <label className="text-sm text-muted font-medium">Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full mt-1.5 input-field"
              />
            </div>
            <div>
              <label className="text-sm text-muted font-medium">Goals & responsibilities</label>
              <textarea
                value={goals}
                onChange={(e) => setGoals(e.target.value)}
                rows={8}
                className="w-full mt-1.5 input-field resize-y px-4 py-3"
              />
            </div>
            {message && <p className="text-sm text-muted">{message}</p>}
            <button
              type="submit"
              disabled={saving}
              className="btn-primary rounded-lg px-6 py-2.5 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save changes'}
            </button>
          </form>
        )}

        <section className="mt-8 pt-6 border-t border-border space-y-3">
          <h3 className="text-sm font-medium">Jira</h3>
          {!jiraImportAvailable && (
            <p className="text-sm text-muted">Jira import is not configured for this environment.</p>
          )}
          {jiraImportAvailable && !syncableBoards.length && (
            <p className="text-sm text-muted">No project boards available to sync.</p>
          )}
          {jiraImportAvailable &&
            syncableBoards.map((board) => (
              <div key={board.board_id} className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium truncate">{board.name}</p>
                  <p className="text-[11px] text-muted">{board.project_key}</p>
                </div>
                <button
                  type="button"
                  onClick={() => handleSyncJira(board)}
                  disabled={Boolean(syncingBoardId)}
                  className="text-sm btn-secondary px-3 py-2 disabled:opacity-50 flex-shrink-0"
                >
                  {syncingBoardId === board.board_id ? 'Syncing…' : 'Sync from Jira'}
                </button>
              </div>
            ))}
          {syncMessage && <p className="text-sm text-muted">{syncMessage}</p>}
        </section>
      </div>
    </div>
  )
}
