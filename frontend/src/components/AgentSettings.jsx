import { useEffect, useState } from 'react'
import { fetchAgents, updateAgent } from '../lib/api'

export default function AgentSettings({ open, onClose }) {
  const [agents, setAgents] = useState([])
  const [selected, setSelected] = useState(null)
  const [name, setName] = useState('')
  const [goals, setGoals] = useState('')
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')

  useEffect(() => {
    if (!open) return
    fetchAgents().then((res) => {
      setAgents(res.agents || [])
      if (res.agents?.length) {
        selectAgent(res.agents[0])
      }
    })
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

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div className="relative w-full sm:max-w-2xl max-h-[90vh] overflow-y-auto bg-surface border border-border rounded-t-2xl sm:rounded-2xl p-5 sm:p-6">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold">Agent settings</h2>
          <button onClick={onClose} className="text-muted text-xl">✕</button>
        </div>

        <div className="flex flex-wrap gap-2 mb-4">
          {agents.map((a) => (
            <button
              key={a.agent_id}
              onClick={() => selectAgent(a)}
              className={`px-3 py-1.5 rounded-full text-sm border ${
                selected?.agent_id === a.agent_id
                  ? 'border-accent bg-accent/20'
                  : 'border-border hover:bg-bg'
              }`}
            >
              {a.icon} {a.name}
            </button>
          ))}
        </div>

        {selected && (
          <form onSubmit={handleSave} className="space-y-4">
            <div>
              <label className="text-sm text-muted">Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full mt-1 bg-bg border border-border rounded-xl px-4 py-2"
              />
            </div>
            <div>
              <label className="text-sm text-muted">Goals & responsibilities</label>
              <textarea
                value={goals}
                onChange={(e) => setGoals(e.target.value)}
                rows={8}
                className="w-full mt-1 bg-bg border border-border rounded-xl px-4 py-3 resize-y"
              />
            </div>
            {message && <p className="text-sm text-muted">{message}</p>}
            <button
              type="submit"
              disabled={saving}
              className="bg-accent text-white font-semibold rounded-full px-6 py-2.5 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save changes'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
