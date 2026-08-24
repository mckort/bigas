import { useEffect, useState } from 'react'
import {
  fetchAgents,
  fetchBoardJiraSyncStatus,
  fetchBoards,
  syncBoardFromJira,
  updateAgent,
} from '../lib/api'

export function SettingsButton({ onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-xl hover:bg-surface text-muted hover:text-text transition-colors"
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

async function pollJiraSyncUntilDone(boardId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2000))
    const data = await fetchBoardJiraSyncStatus(boardId)
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

export default function AgentSettings({ open, onClose }) {
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

  useEffect(() => {
    if (!open) return
    fetchAgents().then((res) => {
      setAgents(res.agents || [])
      if (res.agents?.length) {
        selectAgent(res.agents[0])
      }
    })
    fetchBoards()
      .then((res) => {
        setBoards(res.boards || [])
        setJiraImportAvailable(Boolean(res.jira_import_available))
      })
      .catch(() => {
        setBoards([])
        setJiraImportAvailable(false)
      })
    setSyncMessage('')
  }, [open])

  function selectAgent(agent) {
    setSelected(agent)
    setName(agent.name || '')
    setGoals(agent.system_prompt_goals || '')
    setMessage('')
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
      setAgents(res.agents || [])
    } catch (err) {
      setMessage(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function handleSyncJira(board) {
    if (!board?.board_id || syncingBoardId) return
    setSyncingBoardId(board.board_id)
    setSyncMessage(`Syncing ${board.name}…`)
    try {
      const data = await syncBoardFromJira(board.board_id)
      if (data.status === 'started' || data.status === 'running') {
        const done = await pollJiraSyncUntilDone(board.board_id)
        setSyncMessage(`${board.name}: ${done}`)
      } else {
        setSyncMessage(`${board.name}: ${formatSyncResult(data)}`)
      }
    } catch (err) {
      setSyncMessage(err.message || 'Jira sync failed')
    } finally {
      setSyncingBoardId('')
    }
  }

  if (!open) return null

  const syncableBoards = boards.filter((board) => board.workflow_enabled)

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} aria-hidden="true" />
      <div className="relative w-full sm:max-w-2xl max-h-[90vh] overflow-y-auto bg-white border border-border rounded-t-2xl sm:rounded-2xl p-5 sm:p-6 shadow-card">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold">Settings</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-muted hover:text-text p-2 min-w-[44px] min-h-[44px] flex items-center justify-center"
            aria-label="Close settings"
          >
            ✕
          </button>
        </div>

        <h3 className="text-sm font-medium mb-2">Agents</h3>
        <div className="flex flex-wrap gap-2 mb-4">
          {agents.map((a) => (
            <button
              key={a.agent_id}
              type="button"
              onClick={() => selectAgent(a)}
              className={`px-3 py-2 rounded-full text-sm border min-h-[44px] transition-colors ${
                selected?.agent_id === a.agent_id
                  ? 'border-black/20 bg-bigas-blue text-bigas-black'
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
                className="w-full mt-1.5 bg-white border border-border rounded-xl px-4 py-2.5 focus:outline-none focus:ring-2 focus:ring-bigas-blue/50"
              />
            </div>
            <div>
              <label className="text-sm text-muted font-medium">Goals & responsibilities</label>
              <textarea
                value={goals}
                onChange={(e) => setGoals(e.target.value)}
                rows={8}
                className="w-full mt-1.5 bg-white border border-border rounded-xl px-4 py-3 resize-y focus:outline-none focus:ring-2 focus:ring-bigas-blue/50"
              />
            </div>
            {message && <p className="text-sm text-muted">{message}</p>}
            <button
              type="submit"
              disabled={saving}
              className="bg-bigas-black text-white font-semibold rounded-full px-6 py-2.5 disabled:opacity-50 min-h-[44px] hover:opacity-90 transition-opacity"
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
                  className="text-sm px-3 py-2 rounded-xl border border-border min-h-[44px] disabled:opacity-50 flex-shrink-0"
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
