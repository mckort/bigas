import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { addTicketComment, fetchCockpit, fetchTicket, sendMessage, updateTicket } from '../lib/api'
import { WEEKLY_ACTIONS, cursorPrompt, sendToAgent, timeAgo } from '../lib/cockpit'
import { SettingsButton } from './AgentSettings'
import ThemeToggle from './ThemeToggle'

const POLL_MS = 15000
const SWIPE_PX = 90
const SYSTEM_COMMENT_MARKER = '[bigas-jira-ai]'

const KIND_STYLE = {
  description: { icon: '✎', cls: 'bg-amber-500/15 text-amber-600 dark:text-amber-400', verb: 'Approve' },
  design: { icon: '◇', cls: 'bg-accent-muted text-accent', verb: 'Approve' },
  final: { icon: '✓', cls: 'bg-green-500/15 text-green-600 dark:text-green-400', verb: 'Done' },
  ship: { icon: '🚀', cls: 'bg-violet-500/15 text-violet-600 dark:text-violet-400', verb: 'Review' },
}

const TABS = [
  { id: 'needs', label: 'Needs you' },
  { id: 'working', label: 'Working' },
  { id: 'ship', label: 'Ship' },
  { id: 'week', label: 'Week' },
]

function greeting() {
  const h = new Date().getHours()
  if (h < 10) return 'Good morning'
  if (h < 18) return 'Good afternoon'
  return 'Good evening'
}

function Section({ id, title, count, accent, right, children, mobileTab, className = '' }) {
  return (
    <section
      aria-labelledby={`cockpit-${id}`}
      className={`card p-4 lg:p-5 min-w-0 ${accent ? 'border-accent/40 ring-2 ring-accent/10' : ''} ${
        mobileTab === id ? '' : 'hidden'
      } lg:block ${className}`}
    >
      <div className="flex items-center gap-2 mb-3">
        <h2 id={`cockpit-${id}`} className="font-semibold text-[15px]">
          {title}
        </h2>
        {count != null && (
          <span
            className={`text-xs font-semibold rounded-full px-2 py-0.5 ${
              accent && count ? 'bg-accent text-accent-fg' : 'bg-surface-muted text-muted'
            }`}
          >
            {count}
          </span>
        )}
        {right && <div className="ml-auto flex items-center gap-2">{right}</div>}
      </div>
      {children}
    </section>
  )
}

function KindIcon({ kind }) {
  const style = KIND_STYLE[kind] || KIND_STYLE.design
  return (
    <span
      aria-hidden="true"
      className={`w-9 h-9 flex-none rounded-lg flex items-center justify-center text-base ${style.cls}`}
    >
      {style.icon}
    </span>
  )
}

function itemTitle(item) {
  if (item.type === 'ticket') return item.ticket.title
  return item.title
}

function itemKey(item) {
  if (item.type === 'ticket') return item.ticket.key
  return item.project_key
}

function itemSub(item) {
  if (item.type === 'ticket') {
    const parts = [item.label, item.ticket.board_name]
    return parts.filter(Boolean).join(' · ')
  }
  const parts = [item.label]
  if (item.risk_level) parts.push(`risk ${item.risk_level}`)
  if (item.open_ticket_keys?.length) parts.push(`${item.open_ticket_keys.length} open cards`)
  return parts.join(' · ')
}

function NeedRow({ item, now, busy, onOpen, onPrimary, onSendBack }) {
  const rowRef = useRef(null)
  const drag = useRef({ x0: null, dx: 0, moved: false })
  const style = KIND_STYLE[item.kind] || KIND_STYLE.design
  const canSendBack = item.type === 'ticket'

  const reset = () => {
    if (rowRef.current) rowRef.current.style.transform = ''
  }
  const onPointerDown = (e) => {
    if (e.pointerType === 'mouse' || busy) return
    drag.current = { x0: e.clientX, dx: 0, moved: false }
    if (rowRef.current) rowRef.current.style.transition = 'none'
  }
  const onPointerMove = (e) => {
    const d = drag.current
    if (d.x0 == null) return
    d.dx = e.clientX - d.x0
    if (Math.abs(d.dx) > 8) {
      d.moved = true
      if (rowRef.current) rowRef.current.style.transform = `translateX(${d.dx}px)`
    }
  }
  const onPointerEnd = () => {
    const d = drag.current
    if (d.x0 == null) return
    d.x0 = null
    if (rowRef.current) rowRef.current.style.transition = ''
    if (d.dx > SWIPE_PX) {
      reset()
      onPrimary(item)
    } else if (d.dx < -SWIPE_PX && canSendBack) {
      reset()
      onSendBack(item)
    } else {
      reset()
    }
  }

  return (
    <li className="relative overflow-hidden border-t border-border first:border-t-0">
      <div className="absolute inset-0 flex items-center justify-between px-4 text-sm font-semibold lg:hidden" aria-hidden="true">
        <span className="text-green-600 dark:text-green-400">✓ {style.verb}</span>
        {canSendBack && <span className="text-amber-600 dark:text-amber-400">Send back ↩</span>}
      </div>
      <div
        ref={rowRef}
        onClick={() => {
          if (drag.current.moved) {
            drag.current.moved = false
            return
          }
          onOpen(item)
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        className="relative bg-elevated flex items-center gap-3 py-2.5 cursor-pointer transition-transform duration-200 group"
        style={{ touchAction: 'pan-y' }}
      >
        <KindIcon kind={item.kind} />
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            if (drag.current.moved) {
              drag.current.moved = false
              return
            }
            onOpen(item)
          }}
          className="min-w-0 flex-1 text-left rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          <span className="block truncate font-medium group-hover:text-accent transition-colors">
            <span className="font-mono text-xs text-muted mr-1.5">{itemKey(item)}</span>
            {itemTitle(item)}
          </span>
          <span className="block truncate text-xs text-muted">{itemSub(item)}</span>
        </button>
        <span className="text-[11px] text-muted w-9 text-right flex-none">{timeAgo(item.since, now)}</span>
        <div className="hidden sm:flex gap-1.5 flex-none">
          {canSendBack && item.kind !== 'final' && (
            <button
              type="button"
              disabled={busy}
              onClick={(e) => {
                e.stopPropagation()
                onSendBack(item)
              }}
              className="btn-secondary text-xs px-2.5 py-1 min-h-[32px]"
            >
              Send back
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation()
              onPrimary(item)
            }}
            className={`${item.kind === 'ship' ? 'btn-accent' : 'btn-secondary'} text-xs px-2.5 py-1 min-h-[32px]`}
          >
            {busy ? '…' : style.verb}
          </button>
        </div>
      </div>
    </li>
  )
}

function WorkingRow({ item, now, onOpenChat }) {
  const isTicket = item.type === 'ticket'
  const link = isTicket ? item.ticket.pr_url || item.ticket.agent_url : item.run_url
  return (
    <li className="flex items-center gap-3 py-2.5 border-t border-border first:border-t-0">
      <span className="relative flex h-2.5 w-2.5 flex-none" aria-hidden="true">
        <span className="absolute inline-flex h-full w-full rounded-full bg-green-500 opacity-60 animate-ping" />
        <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-green-500" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">
          <span className="font-mono text-xs text-muted mr-1.5">{isTicket ? item.ticket.key : item.project_key}</span>
          {isTicket ? item.ticket.title : item.version ? `${item.project_key} ${item.version}` : 'Deploy pipeline'}
        </div>
        <div className="truncate text-xs text-muted">{item.stage}</div>
      </div>
      {link && (
        <a href={link} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline flex-none">
          {isTicket && item.ticket.pr_url ? 'PR' : isTicket ? 'Agent' : 'Run'}
        </a>
      )}
      {!isTicket && (
        <button type="button" onClick={() => onOpenChat('devops')} className="text-xs text-accent hover:underline flex-none">
          Chat
        </button>
      )}
      <span className="text-[11px] text-muted w-9 text-right flex-none">{timeAgo(item.since, now)}</span>
    </li>
  )
}

const STATE_BADGE = {
  waiting: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  running: 'bg-green-500/15 text-green-700 dark:text-green-300',
  idle: 'bg-surface text-muted',
}

function ShipCard({ row, busy, onCommand, onOpenChat, onReview }) {
  const primary = row.mode === 'versioned' ? row.commands[0] : row.commands.find((c) => /prepare staging/i.test(c.prompt))
  const others = row.commands.filter((c) => c !== primary)
  return (
    <div className="border border-border rounded-xl p-3.5 bg-bg min-w-0 flex flex-col">
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-semibold truncate">{row.name}</span>
        <span className="font-mono text-[11px] text-muted truncate">{row.key}</span>
        <span className={`ml-auto text-[11px] rounded-md px-2 py-0.5 flex-none ${STATE_BADGE[row.state] || STATE_BADGE.idle}`}>
          {row.state === 'idle' ? 'Idle' : row.state === 'running' ? 'Running' : 'Waiting for you'}
        </span>
      </div>
      <p className="text-xs text-muted mt-1">
        {row.mode === 'versioned'
          ? row.version
            ? `Version ${row.version} · ${row.cut_count} card${row.cut_count === 1 ? '' : 's'} in the cut`
            : row.state === 'idle'
              ? 'No open version. Create one under Releases on the board.'
              : 'Deploy in progress'
          : 'Staging rehearsal, then production'}
      </p>
      <p className="text-sm mt-2 min-h-[20px]">{row.detail || ''}</p>
      <div className="flex flex-wrap gap-1.5 mt-3">
        {row.state === 'waiting' ? (
          <button type="button" onClick={() => onReview(row)} className="btn-accent text-xs px-3 py-1 min-h-[34px]">
            Review deploy
          </button>
        ) : row.state === 'running' ? (
          <button type="button" onClick={() => onOpenChat('devops')} className="btn-secondary text-xs px-3 py-1 min-h-[34px]">
            Open DevOps chat
          </button>
        ) : (
          primary && (
            <button
              type="button"
              disabled={busy || (row.mode === 'versioned' && !row.version)}
              onClick={() => onCommand(row, primary)}
              className="btn-accent text-xs px-3 py-1 min-h-[34px]"
            >
              {primary.label}
            </button>
          )
        )}
        {others.map((cmd) => (
          <button
            key={cmd.prompt}
            type="button"
            disabled={busy}
            onClick={() => onCommand(row, cmd)}
            className="btn-ghost text-xs px-2.5 py-1 min-h-[34px]"
          >
            {cmd.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function Drawer({ open, title, onClose, children, footer }) {
  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[60]" role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : 'Details'}>
      <div className="modal-overlay" onClick={onClose} />
      <div className="absolute bottom-0 inset-x-0 h-[92dvh] rounded-t-2xl lg:rounded-none lg:inset-y-0 lg:left-auto lg:right-0 lg:h-auto lg:w-[560px] bg-elevated shadow-card flex flex-col">
        <div className="flex items-center gap-3 px-4 lg:px-5 py-3 border-b border-border">
          <h3 className="font-semibold flex-1 min-w-0 truncate">{title}</h3>
          <button type="button" onClick={onClose} className="btn-ghost px-2" aria-label="Close">
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto scrollbar-thin px-4 lg:px-5 py-4">{children}</div>
        {footer && (
          <div className="px-4 lg:px-5 py-3 border-t border-border flex flex-wrap gap-2 modal-footer-safe">{footer}</div>
        )}
      </div>
    </div>
  )
}

function TicketDetail({ item, comment, setComment }) {
  const [full, setFull] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let cancelled = false
    setFull(null)
    setError('')
    fetchTicket(item.ticket.ticket_id)
      .then((res) => {
        if (!cancelled) setFull(res.ticket)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Could not load the card')
      })
    return () => {
      cancelled = true
    }
  }, [item.ticket.ticket_id])

  const comments = full?.comments || []
  const lastAi = [...comments].reverse().find((c) => (c.body || '').includes(SYSTEM_COMMENT_MARKER))
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2 text-xs">
        <span className="chip-accent">{item.ticket.status}</span>
        {item.ticket.fix_version && <span className="chip-accent">v{item.ticket.fix_version}</span>}
        {item.ticket.pr_url && (
          <a className="text-accent hover:underline" href={item.ticket.pr_url} target="_blank" rel="noreferrer">
            Pull request
          </a>
        )}
        {item.ticket.agent_url && (
          <a className="text-accent hover:underline" href={item.ticket.agent_url} target="_blank" rel="noreferrer">
            Cursor agent
          </a>
        )}
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {!full && !error && <p className="text-sm text-muted">Loading the card…</p>}
      {full && (
        <div className="markdown-body cockpit-md text-sm bg-surface rounded-lg p-3 lg:p-4 max-w-none">
          {full.description ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{full.description}</ReactMarkdown>
          ) : (
            <p className="text-muted">No description yet.</p>
          )}
        </div>
      )}
      {lastAi && (
        <div>
          <h4 className="text-[11px] uppercase tracking-wider text-muted font-semibold mb-1">Latest AI note</h4>
          <div className="markdown-body cockpit-md text-sm bg-surface rounded-lg p-3">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {(lastAi.body || '').replace(SYSTEM_COMMENT_MARKER, '').trim()}
            </ReactMarkdown>
          </div>
        </div>
      )}
      <div>
        <label htmlFor="cockpit-comment" className="text-[11px] uppercase tracking-wider text-muted font-semibold">
          Comment for the AI
        </label>
        <textarea
          id="cockpit-comment"
          rows={3}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Answers to open questions or what to change. Posted on the card before it moves."
          className="input-field mt-1 text-sm"
        />
      </div>
    </div>
  )
}

function riskLabel(level) {
  const text = (level || 'low').toLowerCase()
  return `${text.charAt(0).toUpperCase()}${text.slice(1)} risk.`
}

function DeployDetail({ item }) {
  return (
    <div className="space-y-3 text-sm">
      <div
        className={`rounded-lg p-3 ${
          item.risk_level === 'high'
            ? 'bg-red-500/10'
            : item.risk_level === 'medium'
              ? 'bg-amber-500/15'
              : 'bg-green-500/10'
        }`}
      >
        <b>{riskLabel(item.risk_level)}</b>{' '}
        {item.action && item.action !== 'deploy'
          ? `DevOps is waiting for you to confirm ${item.action.replace('_', ' ')}.`
          : 'DevOps has finished the checks and is waiting for your go.'}
      </div>
      {item.open_ticket_keys?.length > 0 && (
        <p>
          <b>Open cards in this version:</b> {item.open_ticket_keys.join(', ')}
        </p>
      )}
      {item.extra_commit_count > 0 && <p>{item.extra_commit_count} commits are not linked to a card.</p>}
      <p className="text-muted">
        Confirming sends <b>yes</b> to the DevOps thread, exactly as typing it in chat. Open the chat to read the full risk report first.
      </p>
    </div>
  )
}

export default function CockpitLayout({ user, onSwitchView, onOpenSettings, onOpenChat, onDiscussTicket, onLogout }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('needs')
  const [now, setNow] = useState(Date.now())
  const [busyIds, setBusyIds] = useState(() => new Set())
  const [drawer, setDrawer] = useState(null)
  const [comment, setComment] = useState('')
  const [toast, setToast] = useState(null)
  const toastTimer = useRef(null)

  const load = useCallback(async () => {
    try {
      const res = await fetchCockpit()
      setData(res)
      setError('')
    } catch (err) {
      setError(err.message || 'Could not load the cockpit')
    }
    setNow(Date.now())
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(() => {
      if (document.visibilityState === 'visible') load()
    }, POLL_MS)
    return () => clearInterval(id)
  }, [load])

  const showToast = useCallback((message, action) => {
    setToast({ message, action })
    clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 4000)
  }, [])

  const setBusy = (id, on) =>
    setBusyIds((prev) => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })

  const dropNeed = (id) =>
    setData((prev) =>
      prev ? { ...prev, needs: prev.needs.filter((n) => n.id !== id), counts: { ...prev.counts, needs: Math.max(0, (prev.counts?.needs || 1) - 1) } } : prev,
    )

  const closeDrawer = useCallback(() => {
    setDrawer(null)
    setComment('')
  }, [])

  const moveTicket = useCallback(
    async (item, status, { note = '', message }) => {
      setBusy(item.id, true)
      try {
        if (note.trim()) await addTicketComment(item.ticket.ticket_id, note.trim())
        await updateTicket(item.ticket.ticket_id, { status })
        dropNeed(item.id)
        showToast(message || `${item.ticket.key} → ${status}`)
        closeDrawer()
        load()
      } catch (err) {
        showToast(err.message || 'Could not move the card')
      } finally {
        setBusy(item.id, false)
      }
    },
    [closeDrawer, load, showToast],
  )

  const answerDeploy = useCallback(
    async (item, answer) => {
      setBusy(item.id, true)
      try {
        await sendMessage(item.thread_id, answer, `cockpit-${Date.now()}`)
        dropNeed(item.id)
        closeDrawer()
        showToast(answer === 'yes' ? `Deploying ${item.title}` : `Stopped ${item.title}`, {
          label: 'Open chat',
          run: () => onOpenChat('devops'),
        })
        setTimeout(load, 1500)
      } catch (err) {
        showToast(err.message || 'Could not reach DevOps')
      } finally {
        setBusy(item.id, false)
      }
    },
    [closeDrawer, load, onOpenChat, showToast],
  )

  const runAgentPrompt = useCallback(
    async (busyId, agentId, prompt, label) => {
      setBusy(busyId, true)
      try {
        await sendToAgent(agentId, prompt)
        closeDrawer()
        showToast(label, { label: 'Open chat', run: () => onOpenChat(agentId) })
        setTimeout(load, 1500)
      } catch (err) {
        showToast(err.message || 'Could not send to the agent')
      } finally {
        setBusy(busyId, false)
      }
    },
    [closeDrawer, load, onOpenChat, showToast],
  )

  const openNeed = (item) => {
    setComment('')
    setDrawer({ type: item.type === 'ticket' ? 'ticket' : 'deploy', item })
  }
  const primaryNeed = (item) => {
    if (item.type === 'deploy_confirm') return openNeed(item)
    return moveTicket(item, item.approve_status, {
      message: `${item.ticket.key} → ${item.approve_status.replace(' (AI)', '')}`,
    })
  }
  const sendBackNeed = (item) => {
    if (item.type !== 'ticket') return
    setComment('')
    setDrawer({ type: 'ticket', item, focusSendBack: true })
  }

  const copyCursor = async (ticket) => {
    try {
      await navigator.clipboard.writeText(cursorPrompt(ticket))
      showToast('Cursor prompt copied. Paste it into Cursor.')
    } catch {
      showToast('Could not copy. Your browser blocked the clipboard.')
    }
  }

  const needs = data?.needs || []
  const working = data?.working || []
  const ship = data?.ship || []
  const counts = { needs: needs.length, working: working.length }
  const firstName = (user?.displayName || user?.email || '').split(/[ @]/)[0]

  let drawerTitle = ''
  let drawerBody = null
  let drawerFooter = null
  if (drawer?.type === 'ticket') {
    const { item } = drawer
    const busy = busyIds.has(item.id)
    drawerTitle = (
      <span>
        <span className="font-mono text-xs text-muted mr-2">{item.ticket.key}</span>
        {item.ticket.title}
      </span>
    )
    drawerBody = <TicketDetail item={item} comment={comment} setComment={setComment} />
    drawerFooter = (
      <>
        <button type="button" onClick={() => copyCursor(item.ticket)} className="btn-secondary text-sm px-3">
          Open in Cursor
        </button>
        <button
          type="button"
          onClick={() => onSwitchView('board', `/board?ticket=${encodeURIComponent(item.ticket.key)}`)}
          className="btn-ghost text-sm px-3"
        >
          Board
        </button>
        <button type="button" onClick={() => onDiscussTicket?.(item.ticket)} className="btn-ghost text-sm px-3 hidden sm:inline-flex">
          Discuss
        </button>
        <span className="flex-1" />
        <button
          type="button"
          disabled={busy}
          onClick={() =>
            moveTicket(item, item.send_back_status, {
              note: comment,
              message: `${item.ticket.key} sent back to ${item.send_back_status.replace(' (AI)', '')}`,
            })
          }
          className={`${drawer.focusSendBack ? 'btn-accent' : 'btn-secondary'} text-sm px-3`}
        >
          Send back
        </button>
        {!drawer.focusSendBack && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              moveTicket(item, item.approve_status, {
                note: comment,
                message: `${item.ticket.key} → ${item.approve_status.replace(' (AI)', '')}`,
              })
            }
            className="btn-accent text-sm px-3"
          >
            {item.kind === 'final' ? 'Mark done' : `Approve → ${item.approve_status.replace(' (AI)', '')}`}
          </button>
        )}
      </>
    )
  } else if (drawer?.type === 'deploy') {
    const { item } = drawer
    const busy = busyIds.has(item.id)
    drawerTitle = `${item.label} · ${item.project_key}${item.version ? ` ${item.version}` : ''}`
    drawerBody = <DeployDetail item={item} />
    drawerFooter = (
      <>
        <button type="button" onClick={() => onOpenChat('devops')} className="btn-secondary text-sm px-3">
          Open DevOps chat
        </button>
        <span className="flex-1" />
        <button type="button" disabled={busy} onClick={() => answerDeploy(item, 'no')} className="btn-secondary text-sm px-3">
          Stop
        </button>
        <button type="button" disabled={busy} onClick={() => answerDeploy(item, 'yes')} className="btn-accent text-sm px-3">
          {item.action && item.action !== 'deploy' ? 'Confirm' : 'Deploy'}
        </button>
      </>
    )
  } else if (drawer?.type === 'command') {
    const { row, cmd } = drawer
    const busyId = `ship:${row.key}`
    drawerTitle = `${cmd.label} · ${row.name}`
    drawerBody = (
      <div className="space-y-3 text-sm">
        <p>
          Sends <code className="font-mono bg-surface px-1.5 py-0.5 rounded">{cmd.prompt}</code> to DevOps.
        </p>
        <p className="text-muted">
          {/teardown/i.test(cmd.prompt)
            ? 'DevOps asks you to confirm before it destroys the staging stack.'
            : /prepare deploy/i.test(cmd.prompt)
              ? 'DevOps reviews and autofixes the cut, then comes back here and asks before anything reaches production.'
              : 'Progress shows under AI working. DevOps asks before anything reaches production.'}
        </p>
      </div>
    )
    drawerFooter = (
      <>
        <span className="flex-1" />
        <button type="button" onClick={closeDrawer} className="btn-secondary text-sm px-3">
          Cancel
        </button>
        <button
          type="button"
          disabled={busyIds.has(busyId)}
          onClick={() => runAgentPrompt(busyId, 'devops', cmd.prompt, `Sent “${cmd.prompt}” to DevOps`)}
          className="btn-accent text-sm px-3"
        >
          {cmd.label}
        </button>
      </>
    )
  }

  return (
    <div className="min-h-screen-safe bg-bg mobile-nav-offset">
      <header className="header-bar">
        <div className="max-w-[1320px] mx-auto flex items-center gap-3 px-4 lg:px-6 py-2.5">
          <img src="/bigas-logo.png" alt="" className="h-9 w-9 rounded-lg object-cover" />
          <div className="min-w-0 flex-1">
            <h1 className="font-bold leading-tight">Cockpit</h1>
            <p className="text-xs text-muted truncate">
              {data ? `${counts.needs} waiting on you · ${counts.working} running` : 'Loading…'}
            </p>
          </div>
          <button type="button" onClick={() => onSwitchView('board')} className="text-sm btn-secondary px-3 py-2 hidden lg:inline-flex">
            Board
          </button>
          <button type="button" onClick={() => onSwitchView('chat')} className="text-sm btn-secondary px-3 py-2 hidden lg:inline-flex">
            Chat
          </button>
          <button type="button" onClick={() => onSwitchView('objectives')} className="text-sm btn-ghost px-3 py-2 hidden lg:inline-flex">
            Objectives
          </button>
          <ThemeToggle />
          <SettingsButton onClick={onOpenSettings} />
          <button type="button" onClick={onLogout} className="hidden lg:inline-flex text-sm text-muted px-2 min-h-[44px] items-center">
            Log out
          </button>
        </div>
        <div className="lg:hidden flex gap-1 px-3 pb-2" role="tablist" aria-label="Cockpit sections">
          {TABS.map((t) => {
            const n = t.id === 'needs' ? counts.needs : t.id === 'working' ? counts.working : null
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`flex-1 rounded-lg text-[13px] font-medium py-1.5 min-h-[36px] transition-all ${
                  tab === t.id ? 'bg-accent-muted text-accent' : 'text-muted'
                }`}
              >
                {t.label}
                {n ? <span className="ml-1 text-[11px] font-semibold">{n}</span> : null}
              </button>
            )
          })}
        </div>
      </header>

      <main className="max-w-[1320px] mx-auto px-3 lg:px-6 py-4 lg:py-6">
        <div className="hidden lg:flex items-baseline gap-3 mb-4">
          <h2 className="text-xl font-bold">
            {greeting()}
            {firstName ? `, ${firstName}` : ''}
          </h2>
          {data && (
            <span className="text-sm text-muted">
              {counts.needs ? `${counts.needs} things need you` : 'Nothing waits on you'}
            </span>
          )}
        </div>
        {error && (
          <div className="card p-3 mb-4 text-sm text-red-600 flex items-center gap-3">
            {error}
            <button type="button" onClick={load} className="btn-secondary text-xs px-3 py-1 min-h-[32px] ml-auto">
              Retry
            </button>
          </div>
        )}

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <Section id="needs" title="Needs you" count={data ? counts.needs : null} accent mobileTab={tab}>
            <p className="text-xs text-muted mb-1 lg:hidden">Swipe right to approve, left to send back.</p>
            {!data && !error && <p className="text-sm text-muted py-6 text-center">Loading…</p>}
            {data && needs.length === 0 && (
              <div className="py-8 text-center">
                <p className="font-semibold">You're clear</p>
                <p className="text-sm text-muted">Nothing waits on you. The AI keeps working in the background.</p>
              </div>
            )}
            <ul>
              {needs.map((item) => (
                <NeedRow
                  key={item.id}
                  item={item}
                  now={now}
                  busy={busyIds.has(item.id)}
                  onOpen={openNeed}
                  onPrimary={primaryNeed}
                  onSendBack={sendBackNeed}
                />
              ))}
            </ul>
          </Section>

          <Section id="working" title="AI working" count={data ? counts.working : null} mobileTab={tab}>
            {data && working.length === 0 && (
              <p className="text-sm text-muted py-6 text-center">No AI work running. Approve a card to start some.</p>
            )}
            <ul>
              {working.map((item) => (
                <WorkingRow key={item.id} item={item} now={now} onOpenChat={onOpenChat} />
              ))}
            </ul>
          </Section>

          <Section
            id="ship"
            title="Ship"
            mobileTab={tab}
            className="lg:col-span-2"
            right={<span className="text-xs text-muted hidden sm:inline">Versioned releases and staging rehearsals</span>}
          >
            {data && ship.length === 0 && (
              <p className="text-sm text-muted py-4">No deploy targets configured. See the README section on DevOps.</p>
            )}
            <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
              {ship.map((row) => (
                <ShipCard
                  key={row.key}
                  row={row}
                  busy={busyIds.has(`ship:${row.key}`)}
                  onOpenChat={onOpenChat}
                  onCommand={(r, cmd) => setDrawer({ type: 'command', row: r, cmd })}
                  onReview={(r) => {
                    const pending = needs.find((n) => n.type === 'deploy_confirm' && n.project_key === r.key)
                    if (pending) openNeed(pending)
                    else onOpenChat('devops')
                  }}
                />
              ))}
            </div>
          </Section>

          <Section id="week" title="This week" mobileTab={tab} className="lg:col-span-2">
            <div className="grid gap-3 grid-cols-1 md:grid-cols-3">
              {WEEKLY_ACTIONS.map((action) => {
                const busyId = `week:${action.id}`
                return (
                  <div key={action.id} className="border border-border rounded-xl p-3.5 bg-bg flex flex-col">
                    <h3 className="font-semibold">{action.title}</h3>
                    <p className="text-xs text-muted mt-1 flex-1">{action.body}</p>
                    <div className="flex gap-1.5 mt-3">
                      <button
                        type="button"
                        disabled={busyIds.has(busyId)}
                        onClick={() =>
                          runAgentPrompt(busyId, action.agentId, action.prompt, `${action.title}: asked ${action.agentId === 'cfo' ? 'CFO' : 'Product'}`)
                        }
                        className="btn-accent text-xs px-3 py-1 min-h-[34px]"
                      >
                        {busyIds.has(busyId) ? 'Sending…' : action.button}
                      </button>
                      <button type="button" onClick={() => onOpenChat(action.agentId)} className="btn-ghost text-xs px-2.5 py-1 min-h-[34px]">
                        Last result
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          </Section>
        </div>
      </main>

      <Drawer open={Boolean(drawer)} title={drawerTitle} onClose={closeDrawer} footer={drawerFooter}>
        {drawerBody}
      </Drawer>

      {toast && (
        <div
          role="status"
          className="fixed left-1/2 -translate-x-1/2 bottom-[76px] lg:bottom-6 z-[70] bg-primary text-primary-fg text-sm rounded-xl px-4 py-2.5 shadow-card flex items-center gap-3 max-w-[92vw]"
        >
          <span className="truncate">{toast.message}</span>
          {toast.action && (
            <button
              type="button"
              onClick={() => {
                toast.action.run()
                setToast(null)
              }}
              className="font-semibold text-bigas-blue flex-none"
            >
              {toast.action.label}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
