import { useCallback, useEffect, useState } from 'react'
import {
  approveGatewayRequest,
  connectGatewayAgent,
  denyGatewayRequest,
  fetchGateway,
  fetchGatewayAgent,
  newGatewayToken,
  pauseGateway,
  removeGatewayAgent,
  updateGatewayAgent,
} from '../lib/api'

// Outside agents (Grok and other MCP clients) and their approval requests.
// Renders nothing when the gateway is off, so installs without it see no change.

const REFRESH_MS = 30000

function ago(ts) {
  if (!ts) return 'never'
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} d ago`
}

function inTime(ts) {
  if (!ts) return ''
  const s = Math.round(ts - Date.now() / 1000)
  if (s <= 0) return 'expired'
  if (s < 3600) return `expires in ${Math.max(1, Math.round(s / 60))} min`
  return `expires in ${Math.round(s / 3600)} h`
}

const LEVEL_STYLE = {
  look: 'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  propose: 'bg-violet-500/15 text-violet-700 dark:text-violet-300',
  operate: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
}

function LevelTag({ level, label }) {
  return (
    <span className={`text-[11px] font-semibold rounded px-1.5 py-px ${LEVEL_STYLE[level] || 'bg-surface-muted text-muted'}`}>
      {label || level}
    </span>
  )
}

function CopyButton({ text, label = 'Copy' }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      className="btn-secondary text-xs px-2.5 py-1 min-h-[32px]"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text)
          setDone(true)
          setTimeout(() => setDone(false), 1500)
        } catch {
          setDone(false)
        }
      }}
    >
      {done ? 'Copied' : label}
    </button>
  )
}

function LevelPicker({ levels, value, onChange, name }) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium mb-1">Access level</legend>
      {levels.map((lvl) => (
        <label
          key={lvl.id}
          className={`flex gap-3 items-start border rounded-xl p-3 cursor-pointer ${
            value === lvl.id ? 'border-accent/50 ring-2 ring-accent/10' : 'border-border'
          }`}
        >
          <input
            type="radio"
            name={name}
            value={lvl.id}
            checked={value === lvl.id}
            onChange={() => onChange(lvl.id)}
            className="mt-1"
          />
          <span>
            <span className="font-semibold">{lvl.label}</span>
            <span className="block text-xs text-muted mt-0.5">{lvl.summary}</span>
          </span>
        </label>
      ))}
    </fieldset>
  )
}

function TokenAndSetup({ token, instructions }) {
  const [pick, setPick] = useState(instructions?.[0]?.id || '')
  const current = (instructions || []).find((i) => i.id === pick) || instructions?.[0]
  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-3">
        <p className="text-sm font-semibold">Copy the token now</p>
        <p className="text-xs text-muted">Bigas shows it once. Put it in the agent's secrets, not in chat.</p>
        <div className="flex gap-2 items-center mt-2">
          <code className="text-xs break-all flex-1 bg-bg rounded px-2 py-1.5 border border-border">{token}</code>
          <CopyButton text={token} />
        </div>
      </div>
      {current && (
        <div>
          <div className="flex items-center gap-2 mb-1.5">
            <label className="text-sm font-medium" htmlFor="gw-setup-client">
              Set up in
            </label>
            <select
              id="gw-setup-client"
              className="input-field min-h-[36px] py-1 text-sm w-auto"
              value={current.id}
              onChange={(e) => setPick(e.target.value)}
            >
              {instructions.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.label}
                </option>
              ))}
            </select>
            <div className="ml-auto">
              <CopyButton text={current.code} />
            </div>
          </div>
          <pre className="text-xs bg-bg border border-border rounded-lg p-3 whitespace-pre-wrap break-all">{current.code}</pre>
          <p className="text-xs text-muted mt-2">
            Then ask the agent to call <code>gateway_whoami</code>. It answers with what it may do.
          </p>
        </div>
      )}
    </div>
  )
}

function ApprovalCard({ item, busy, onApprove, onDeny }) {
  const [denying, setDenying] = useState(false)
  const [note, setNote] = useState('')
  const [showArgs, setShowArgs] = useState(false)
  const args = item.arguments || {}
  const argCount = Object.keys(args).length
  return (
    <div className="border border-border rounded-xl p-3.5 bg-bg flex flex-col">
      <p className="text-sm">
        <b>{item.agent_name}</b> wants to run <code className="text-[13px]">{item.tool}</code>
      </p>
      {item.intent && <p className="text-sm text-muted mt-1">“{item.intent}”</p>}
      <p className="text-xs text-muted mt-1">
        {ago(item.created_at)} · {inTime(item.expires_at)}
      </p>
      {argCount > 0 && (
        <button
          type="button"
          onClick={() => setShowArgs((v) => !v)}
          aria-expanded={showArgs}
          className="text-xs text-accent mt-2 self-start"
        >
          {showArgs ? 'Hide details' : `Show what it will send (${argCount})`}
        </button>
      )}
      {showArgs && (
        <pre className="text-xs bg-elevated border border-border rounded-lg p-2 mt-1 overflow-x-auto max-h-60">
          {JSON.stringify(args, null, 2)}
        </pre>
      )}
      {denying ? (
        <div className="mt-3 space-y-2">
          <input
            className="input-field text-sm min-h-[36px]"
            placeholder="Why not? (optional, the agent sees this)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            autoFocus
          />
          <div className="flex gap-1.5">
            <button
              type="button"
              disabled={busy}
              onClick={() => onDeny(item, note)}
              className="btn-secondary text-xs px-2.5 py-1 min-h-[32px]"
            >
              Deny
            </button>
            <button type="button" onClick={() => setDenying(false)} className="btn-ghost text-xs px-2.5 py-1 min-h-[32px]">
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex gap-1.5 mt-3">
          <button
            type="button"
            disabled={busy}
            onClick={() => onApprove(item)}
            className="btn-accent text-xs px-3 py-1 min-h-[32px]"
          >
            {busy ? 'Running…' : 'Approve and run'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setDenying(true)}
            className="btn-secondary text-xs px-2.5 py-1 min-h-[32px]"
          >
            Deny
          </button>
        </div>
      )}
    </div>
  )
}

function ConnectForm({ levels, onDone, showToast }) {
  const [name, setName] = useState('')
  const [level, setLevel] = useState('look')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  if (result) {
    return (
      <div className="space-y-3">
        <p className="text-sm">
          <b>{result.agent.name}</b> is connected with <LevelTag level={result.agent.level} label={result.agent.level_label} />{' '}
          access.
        </p>
        <TokenAndSetup token={result.token} instructions={result.instructions} />
        <button type="button" className="btn-primary w-full" onClick={onDone}>
          Done
        </button>
      </div>
    )
  }

  return (
    <form
      className="space-y-4"
      onSubmit={async (e) => {
        e.preventDefault()
        setBusy(true)
        setError('')
        try {
          const res = await connectGatewayAgent(name, level)
          setResult(res)
          showToast?.(`${res.agent.name} connected`)
        } catch (err) {
          setError(err.message)
        } finally {
          setBusy(false)
        }
      }}
    >
      <div>
        <label className="text-sm font-medium" htmlFor="gw-agent-name">
          Name
        </label>
        <input
          id="gw-agent-name"
          className="input-field mt-1"
          placeholder="grok-growth"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          autoFocus
        />
        <p className="text-xs text-muted mt-1">A short name you will recognise in approvals and the activity log.</p>
      </div>
      <LevelPicker levels={levels} value={level} onChange={setLevel} name="gw-new-level" />
      <p className="text-xs text-muted">Start low. You can raise the level later without a new token.</p>
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <button type="submit" className="btn-primary w-full" disabled={busy || !name.trim()}>
        {busy ? 'Connecting…' : 'Connect'}
      </button>
    </form>
  )
}

function ToolGroup({ title, tools, open = false }) {
  const [show, setShow] = useState(open)
  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setShow((v) => !v)}
        aria-expanded={show}
        className="text-xs text-muted hover:text-text"
      >
        {show ? '▾' : '▸'} {title} ({tools.length})
      </button>
      {show && (
        <div className="flex flex-wrap gap-1 mt-1">
          {tools.length === 0 && <span className="text-xs text-muted">Nothing</span>}
          {tools.map((t) => (
            <code key={t} className="text-[11px] bg-surface-muted rounded px-1.5 py-px">
              {t}
            </code>
          ))}
        </div>
      )}
    </div>
  )
}

function AgentManager({ agentId, levels, onChanged, onRemoved, showToast }) {
  const [detail, setDetail] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState(null)
  const [confirmRemove, setConfirmRemove] = useState(false)

  const load = useCallback(async () => {
    try {
      setDetail(await fetchGatewayAgent(agentId))
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [agentId])

  useEffect(() => {
    load()
  }, [load])

  const change = async (payload, message) => {
    setBusy(true)
    try {
      await updateGatewayAgent(agentId, payload)
      showToast?.(message)
      await load()
      onChanged()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (error && !detail) return <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
  if (!detail) return <p className="text-sm text-muted">Loading…</p>
  const { agent, activity } = detail
  const tools = agent.tools || { runs: [], needs_approval: [] }

  return (
    <div className="space-y-5">
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <LevelPicker
        levels={levels}
        value={agent.level}
        name="gw-change-level"
        onChange={(lvl) => change({ level: lvl }, `${agent.name} now has ${lvl} access`)}
      />

      <div>
        <h4 className="text-sm font-medium mb-1">Today's limits</h4>
        <ul className="text-sm space-y-0.5">
          {agent.limits.map((l) => (
            <li key={l.label} className="flex justify-between">
              <span className="text-muted">{l.label}</span>
              <span>
                {l.used} / {l.max}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h4 className="text-sm font-medium mb-1">What it can do</h4>
        <ToolGroup title="Asks you first" tools={tools.needs_approval} open />
        <ToolGroup title="Runs without asking" tools={tools.runs} />
        <p className="text-xs text-muted mt-1">{(tools.not_available || []).length} other tools are off for this level.</p>
      </div>

      <div>
        <h4 className="text-sm font-medium mb-1">Recent activity</h4>
        {activity.length === 0 && <p className="text-xs text-muted">No calls yet.</p>}
        <ul className="text-xs space-y-1">
          {activity.slice(0, 15).map((row, i) => (
            <li key={`${row.ts}-${i}`} className="flex gap-2">
              <span className="text-muted w-20 shrink-0">{ago(row.ts)}</span>
              <code className="truncate flex-1">{row.tool}</code>
              <span
                className={
                  row.decision === 'denied'
                    ? 'text-red-600 dark:text-red-400'
                    : row.decision === 'held'
                      ? 'text-amber-600 dark:text-amber-400'
                      : 'text-muted'
                }
              >
                {row.decision}
              </span>
            </li>
          ))}
        </ul>
      </div>

      {token ? (
        <div>
          <h4 className="text-sm font-medium mb-1">New token</h4>
          <p className="text-xs text-muted mb-2">The old token stopped working.</p>
          <TokenAndSetup token={token.token} instructions={token.instructions} />
        </div>
      ) : null}

      <div className="flex flex-wrap gap-2 pt-2 border-t border-border">
        <button
          type="button"
          disabled={busy}
          className="btn-secondary text-sm"
          onClick={() =>
            change({ paused: !agent.paused }, agent.paused ? `${agent.name} resumed` : `${agent.name} paused`)
          }
        >
          {agent.paused ? 'Resume' : 'Pause'}
        </button>
        <button
          type="button"
          disabled={busy}
          className="btn-secondary text-sm"
          onClick={async () => {
            setBusy(true)
            try {
              setToken(await newGatewayToken(agentId))
            } catch (err) {
              setError(err.message)
            } finally {
              setBusy(false)
            }
          }}
        >
          New token
        </button>
        {confirmRemove ? (
          <button
            type="button"
            disabled={busy}
            className="btn-secondary text-sm text-red-600 dark:text-red-400"
            onClick={async () => {
              setBusy(true)
              try {
                await removeGatewayAgent(agentId)
                showToast?.(`${agent.name} removed`)
                onRemoved()
              } catch (err) {
                setError(err.message)
                setBusy(false)
              }
            }}
          >
            Remove for good
          </button>
        ) : (
          <button type="button" className="btn-ghost text-sm" onClick={() => setConfirmRemove(true)}>
            Remove…
          </button>
        )}
      </div>
    </div>
  )
}

export function useGateway(showToast) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(() => new Set())

  const load = useCallback(async () => {
    try {
      setData(await fetchGateway())
      setError('')
    } catch (err) {
      setError(err.message)
    }
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, REFRESH_MS)
    return () => clearInterval(id)
  }, [load])

  const withBusy = useCallback(async (id, fn) => {
    setBusy((s) => new Set(s).add(id))
    try {
      await fn()
    } finally {
      setBusy((s) => {
        const next = new Set(s)
        next.delete(id)
        return next
      })
    }
  }, [])

  const approve = useCallback(
    (item) =>
      withBusy(item.id, async () => {
        try {
          const res = await approveGatewayRequest(item.id)
          const ok = res.approval?.status === 'succeeded'
          showToast?.(ok ? `${item.tool} ran for ${item.agent_name}` : `${item.tool} failed. The agent can see why.`)
        } catch (err) {
          showToast?.(err.message)
        }
        await load()
      }),
    [load, showToast, withBusy],
  )

  const deny = useCallback(
    (item, note) =>
      withBusy(item.id, async () => {
        try {
          await denyGatewayRequest(item.id, note)
          showToast?.(`Denied ${item.tool} for ${item.agent_name}`)
        } catch (err) {
          showToast?.(err.message)
        }
        await load()
      }),
    [load, showToast, withBusy],
  )

  const enabled = Boolean(data?.enabled)
  return {
    enabled,
    data: enabled ? data : null,
    approvals: enabled ? data.approvals || [] : [],
    error,
    busy,
    load,
    approve,
    deny,
    withBusy,
  }
}

// Agent requests waiting for the owner. Shown at the top of Needs you.
export function GatewayApprovals({ gateway, className = '' }) {
  if (!gateway.approvals.length) return null
  return (
    <div className={className}>
      <p className="text-xs text-muted mb-2">Outside agents ask to run</p>
      <div className="grid gap-3 grid-cols-1 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,18rem),1fr))]">
        {gateway.approvals.map((item) => (
          <ApprovalCard
            key={item.id}
            item={item}
            busy={gateway.busy.has(item.id)}
            onApprove={gateway.approve}
            onDeny={gateway.deny}
          />
        ))}
      </div>
    </div>
  )
}

export default function OutsideAgents({ gateway, Section, Drawer, mobileTab, showToast }) {
  const [drawer, setDrawer] = useState(null)
  const { data, load, busy, withBusy, error } = gateway
  if (!data) return null
  const agents = data.agents || []
  const waiting = gateway.approvals.length

  const togglePause = () =>
    withBusy('pause', async () => {
      try {
        const res = await pauseGateway(!data.paused)
        showToast?.(res.paused ? 'Outside agents paused' : 'Outside agents resumed')
      } catch (err) {
        showToast?.(err.message)
      }
      await load()
    })

  return (
    <>
      <Section
        id="outside-agents"
        pane="needs"
        title="Outside agents"
        count={agents.length}
        mobileTab={mobileTab}
        right={
          <>
            <button
              type="button"
              onClick={togglePause}
              disabled={busy.has('pause')}
              className={`text-xs px-2.5 py-1 min-h-[32px] whitespace-nowrap ${data.paused ? 'btn-accent' : 'btn-ghost'}`}
            >
              {data.paused ? 'Resume all' : 'Pause all'}
            </button>
            <button
              type="button"
              onClick={() => setDrawer({ type: 'connect' })}
              className="btn-secondary text-xs px-2.5 py-1 min-h-[32px] whitespace-nowrap"
            >
              Connect
            </button>
          </>
        }
      >
        {error && <p className="text-sm text-red-600 dark:text-red-400 mb-2">{error}</p>}
        {data.paused && (
          <p className="text-sm rounded-lg bg-amber-500/10 border border-amber-500/30 px-3 py-2 mb-3">
            All outside agents are paused. Your own Cursor and Claude still work.
          </p>
        )}
        {waiting > 0 && (
          <p className="text-sm text-muted mb-2">
            {waiting} request{waiting === 1 ? '' : 's'} waiting for you in Needs you.
          </p>
        )}

        {agents.length === 0 ? (
          <div className="py-4 text-center">
            <p className="font-semibold">No outside agents yet</p>
            <p className="text-sm text-muted">
              Connect Grok or another MCP client with a limited level. Your own Cursor and Claude keep full access.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {agents.map((a) => (
              <li key={a.id}>
                <button
                  type="button"
                  className="w-full text-left py-2.5 flex items-center gap-2 hover:bg-surface rounded-lg px-1"
                  onClick={() => setDrawer({ type: 'agent', id: a.id, name: a.name })}
                >
                  <span className="font-medium truncate min-w-0">{a.name}</span>
                  <LevelTag level={a.level} label={a.level_label} />
                  {a.paused && <span className="text-[11px] text-muted">paused</span>}
                  <span className="ml-auto text-xs text-muted truncate hidden sm:inline">
                    {a.last_used_at ? `${a.last_tool} · ${ago(a.last_used_at)}` : 'not used yet'}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Drawer
        open={drawer?.type === 'connect'}
        title="Connect an agent"
        onClose={() => {
          setDrawer(null)
          load()
        }}
      >
        {drawer?.type === 'connect' && (
          <ConnectForm
            levels={data.levels || []}
            showToast={showToast}
            onDone={() => {
              setDrawer(null)
              load()
            }}
          />
        )}
      </Drawer>

      <Drawer open={drawer?.type === 'agent'} title={drawer?.name || 'Agent'} onClose={() => setDrawer(null)}>
        {drawer?.type === 'agent' && (
          <AgentManager
            agentId={drawer.id}
            levels={data.levels || []}
            showToast={showToast}
            onChanged={load}
            onRemoved={() => {
              setDrawer(null)
              load()
            }}
          />
        )}
      </Drawer>
    </>
  )
}
