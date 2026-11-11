import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  addTicketComment,
  fetchCockpit,
  fetchCockpitMoney,
  fetchTicket,
  sendMessage,
  signOffTickets,
  updateTicket,
} from '../lib/api'
import { WEEKLY_ACTIONS, cursorPrompt, sendToAgent, timeAgo } from '../lib/cockpit'
import { featureEnabled } from '../lib/features'
import { SettingsButton } from './AgentSettings'
import ThemeToggle from './ThemeToggle'

const POLL_MS = 15000
const SWIPE_PX = 90
// The click that follows a touch swipe must not open the row. It arrives after pointerup.
const SWIPE_CLICK_MS = 400
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

const PROJECT_COLORS = [
  'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  'bg-violet-500/15 text-violet-700 dark:text-violet-300',
  'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  'bg-rose-500/15 text-rose-700 dark:text-rose-300',
  'bg-teal-500/15 text-teal-700 dark:text-teal-300',
  'bg-indigo-500/15 text-indigo-700 dark:text-indigo-300',
  'bg-orange-500/15 text-orange-700 dark:text-orange-300',
]

function projectColor(project) {
  const text = String(project || '')
  let hash = 0
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) >>> 0
  return PROJECT_COLORS[hash % PROJECT_COLORS.length]
}

export function projectOf(item) {
  if (!item) return ''
  if (item.type === 'ticket') return (item.ticket?.project_key || '').toUpperCase()
  return (item.project_key || item.key || '').toUpperCase()
}

function ProjectTag({ project, label }) {
  return (
    <span
      className={`inline-block font-mono text-[11px] font-semibold rounded px-1.5 py-px mr-1.5 align-[1px] ${projectColor(project)}`}
    >
      {label || project}
    </span>
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
  const open = item.open_ticket_keys?.length || 0
  if (open) parts.push(`${open} open card${open === 1 ? '' : 's'}`)
  return parts.join(' · ')
}

function NeedRow({ item, now, busy, onOpen, onPrimary, onSendBack }) {
  const rowRef = useRef(null)
  const drag = useRef({ x0: null, dx: 0, moved: false, suppressUntil: 0 })
  const style = KIND_STYLE[item.kind] || KIND_STYLE.design
  const canSendBack = item.type === 'ticket'

  const reset = () => {
    const moved = drag.current.moved
    drag.current.x0 = null
    drag.current.dx = 0
    drag.current.moved = false
    if (moved) drag.current.suppressUntil = Date.now() + SWIPE_CLICK_MS
    if (rowRef.current) {
      rowRef.current.style.transition = ''
      rowRef.current.style.transform = ''
    }
  }
  const swallowClick = () => {
    if (drag.current.moved || Date.now() < drag.current.suppressUntil) {
      drag.current.moved = false
      drag.current.suppressUntil = 0
      return true
    }
    return false
  }
  const onPointerDown = (e) => {
    if (e.pointerType === 'mouse' || busy) return
    drag.current.x0 = e.clientX
    drag.current.dx = 0
    drag.current.moved = false
    if (rowRef.current) rowRef.current.style.transition = 'none'
  }
  const onPointerMove = (e) => {
    const d = drag.current
    if (d.x0 == null) return
    d.dx = e.clientX - d.x0
    if (Math.abs(d.dx) > 8) {
      if (!d.moved) {
        // Capture only once it is a swipe, so taps still reach the row's buttons.
        try {
          e.currentTarget.setPointerCapture(e.pointerId)
        } catch {
          /* pointer already released */
        }
      }
      d.moved = true
      if (rowRef.current) rowRef.current.style.transform = `translateX(${d.dx}px)`
    }
  }
  const onPointerEnd = () => {
    const d = drag.current
    if (d.x0 == null) return
    const dx = d.dx
    reset()
    if (dx > SWIPE_PX) onPrimary(item)
    else if (dx < -SWIPE_PX && canSendBack) onSendBack(item)
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
          if (swallowClick()) return
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
            if (swallowClick()) return
            onOpen(item)
          }}
          className="min-w-0 flex-1 text-left rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        >
          <span className="block truncate font-medium group-hover:text-accent transition-colors">
            <ProjectTag project={projectOf(item)} label={itemKey(item)} />
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

function WorkingRow({ item, now, onOpenChat, onOpenTicket, onRetry, busy }) {
  const isTicket = item.type === 'ticket'
  const link = isTicket ? item.ticket.pr_url || item.ticket.agent_url : item.run_url
  const open = () => (isTicket ? onOpenTicket(item) : onOpenChat('devops'))
  return (
    <li className="flex items-center gap-3 py-2.5 border-t border-border first:border-t-0">
      {item.stuck ? (
        <span className="h-2.5 w-2.5 flex-none rounded-full bg-amber-500" aria-hidden="true" />
      ) : (
        <span className="relative flex h-2.5 w-2.5 flex-none" aria-hidden="true">
          <span className="absolute inline-flex h-full w-full rounded-full bg-green-500 opacity-60 animate-ping" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-green-500" />
        </span>
      )}
      <button
        type="button"
        onClick={open}
        className="min-w-0 flex-1 text-left group rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
        title={isTicket ? 'Open the card' : 'Open the DevOps chat'}
      >
        <span className="block truncate font-medium group-hover:text-accent transition-colors">
          <ProjectTag project={projectOf(item)} label={isTicket ? item.ticket.key : item.project_key} />
          {isTicket ? item.ticket.title : item.version ? `Version ${item.version}` : 'Deploy pipeline'}
        </span>
        <span className="block truncate text-xs text-muted">
          {item.stuck ? `Stuck in ${item.stage.toLowerCase()} for ${timeAgo(item.since, now)}` : item.stage}
        </span>
      </button>
      {link && (
        <a href={link} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline flex-none">
          {isTicket && item.ticket.pr_url ? 'PR' : isTicket ? 'Agent' : 'Run'}
        </a>
      )}
      {item.stuck && isTicket && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onRetry(item)}
          className="btn-secondary text-xs px-2.5 py-1 min-h-[30px] flex-none"
          title="Move the card to To Do and back, so the AI step starts again"
        >
          {busy ? '…' : 'Retry'}
        </button>
      )}
      {!item.stuck && <span className="text-[11px] text-muted w-9 text-right flex-none">{timeAgo(item.since, now)}</span>}
    </li>
  )
}

const STATE_BADGE = {
  waiting: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  running: 'bg-green-500/15 text-green-700 dark:text-green-300',
  idle: 'bg-surface text-muted',
}

function ShipCard({ row, steps = [], busy, onCommand, onOpenChat, onReview }) {
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
          ? row.release_error
            ? 'Could not load releases. The cockpit retries every 15 seconds.'
            : row.version
            ? `Version ${row.version} · ${row.cut_count} card${row.cut_count === 1 ? '' : 's'} in the cut`
            : row.state === 'idle'
              ? 'No open version. Create one under Releases on the board.'
              : 'Deploy in progress'
          : 'Staging rehearsal, then production'}
      </p>
      {steps.length > 0 && (
        <div className="mt-3" aria-label={row.step == null ? 'Not started' : `Step ${row.step + 1} of ${steps.length}: ${steps[row.step]}`}>
          <div className="flex gap-1">
            {steps.map((label, i) => (
              <span
                key={label}
                className={`h-1.5 flex-1 rounded-full ${
                  row.step == null || i > row.step
                    ? 'bg-surface-muted'
                    : i < row.step
                      ? 'bg-green-500'
                      : row.state === 'waiting'
                        ? 'bg-amber-500'
                        : 'bg-accent animate-pulse'
                }`}
              />
            ))}
          </div>
          <div className="flex text-[10px] text-muted mt-1">
            {steps.map((label, i) => (
              <span key={label} className={`flex-1 ${row.step === i ? 'text-text font-semibold' : ''}`}>
                {label}
              </span>
            ))}
          </div>
        </div>
      )}
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

function formatAmount(value, currency) {
  if (value == null) return '–'
  const rounded = Math.round(value)
  const text = Math.abs(rounded).toLocaleString('sv-SE')
  const sign = rounded < 0 ? '−' : ''
  return currency === 'USD' ? `${sign}$${text}` : `${sign}${text} kr`
}

function Tile({ label, value, sub, tone = '' }) {
  return (
    <div className="bg-surface rounded-lg px-3 py-2.5 min-w-0">
      <p className="text-[11px] text-muted truncate">{label}</p>
      <p className={`text-lg font-semibold leading-tight truncate ${tone}`}>{value}</p>
      {sub && <p className="text-[11px] text-muted truncate">{sub}</p>}
    </div>
  )
}

function WeekTiles({ week, money, moneyError }) {
  const tiles = []
  if (week) {
    const top = Object.entries(week.shipped_by_project || {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([k, n]) => `${k} ${n}`)
      .join(' · ')
    tiles.push(<Tile key="shipped" label="Cards shipped, 7 days" value={week.shipped} sub={top || 'None yet'} />)
    tiles.push(
      <Tile
        key="releases"
        label="Releases, 7 days"
        value={week.releases.length}
        sub={week.releases.slice(0, 3).join(' · ') || 'None'}
      />,
    )
  }
  const ai = money?.ai
  if (ai?.configured) {
    tiles.push(
      <Tile
        key="ai"
        label="AI and cloud spend, 7 days"
        value={ai.error ? 'Error' : formatAmount(ai.week, 'USD')}
        tone={ai.change_pct > 20 ? 'text-red-600 dark:text-red-400' : ''}
        sub={
          ai.error
            ? ai.error
            : ai.change_pct == null
              ? (ai.top || []).map((t) => t.name).join(' · ')
              : `${ai.change_pct > 0 ? '+' : ''}${ai.change_pct}% vs the week before`
        }
      />,
    )
  }
  for (const c of money?.books?.companies || []) {
    tiles.push(
      <Tile
        key={`co-${c.label}`}
        label={`${c.label}, result this month`}
        value={c.error ? 'Error' : formatAmount(c.result, 'SEK')}
        tone={c.result > 0 ? 'text-green-600 dark:text-green-400' : c.result < 0 ? 'text-red-600 dark:text-red-400' : ''}
        sub={
          c.error
            ? c.error
            : `Revenue ${formatAmount(c.revenue, 'SEK')} · costs ${formatAmount(c.costs, 'SEK')}${
                c.unbooked ? ` · ${c.unbooked} unbooked` : ''
              }`
        }
      />,
    )
  }
  return (
    <div className="mb-4">
      <div className="grid gap-2 grid-cols-2 md:grid-cols-3 xl:grid-cols-5">{tiles}</div>
      {!money && !moneyError && <p className="text-[11px] text-muted mt-2">Loading money from Accounted and usage sources…</p>}
      {moneyError && <p className="text-[11px] text-red-600 mt-2">{moneyError}</p>}
      {money && !money.books?.configured && !money.ai?.configured && (
        <p className="text-[11px] text-muted mt-2">
          Add ACCOUNTED_API_KEY and ACCOUNTED_CFO_COMPANIES for the books, and BIGAS_USAGE_PROVIDERS for AI spend.
        </p>
      )}
    </div>
  )
}

function Drawer({ open, title, onClose, children, footer }) {
  useEffect(() => {
    if (!open) return undefined
    const { body } = document
    const previous = body.style.overflow
    body.style.overflow = 'hidden'
    return () => {
      body.style.overflow = previous
    }
  }, [open])

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
        {item.ticket.fix_version && (
          <span className="chip-accent">v{String(item.ticket.fix_version).replace(/^v/i, '')}</span>
        )}
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
      {item.extra_commit_count > 0 && (
        <p>
          {item.extra_commit_count === 1
            ? '1 commit is not linked to a card.'
            : `${item.extra_commit_count} commits are not linked to a card.`}
        </p>
      )}
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
  const [projectFilter, setProjectFilter] = useState('')
  const [showStuck, setShowStuck] = useState(false)
  const [money, setMoney] = useState(null)
  const [moneyError, setMoneyError] = useState('')
  const toastTimer = useRef(null)
  useEffect(() => () => clearTimeout(toastTimer.current), [])

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

  const loadMoney = useCallback(async (refresh = false) => {
    setMoneyError('')
    try {
      setMoney(await fetchCockpitMoney({ refresh }))
    } catch (err) {
      setMoneyError(err.message || 'Could not load the numbers')
    }
  }, [])

  useEffect(() => {
    loadMoney()
  }, [loadMoney])

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
    setData((prev) => {
      if (!prev) return prev
      const nextNeeds = prev.needs.filter((n) => n.id !== id)
      const nextSignoff = (prev.signoff || []).filter((n) => n.id !== id)
      return {
        ...prev,
        needs: nextNeeds,
        signoff: nextSignoff,
        counts: { ...prev.counts, needs: nextNeeds.length, signoff: nextSignoff.length },
      }
    })



  const closeDrawer = useCallback(() => {
    setDrawer(null)
    setComment('')
  }, [])

  const [showSignoff, setShowSignoff] = useState(false)
  const closeAllSignoff = useCallback(
    async (items) => {
      setBusy('signoff:all', true)
      try {
        const res = await signOffTickets(items.map((i) => i.ticket.ticket_id))
        const closed = res.closed?.length || 0
        const skipped = res.skipped?.length || 0
        setData((prev) => (prev ? { ...prev, signoff: [], counts: { ...prev.counts, signoff: 0 } } : prev))
        closeDrawer()
        showToast(
          `Marked ${closed} card${closed === 1 ? '' : 's'} done${skipped ? ` · ${skipped} skipped (moved elsewhere)` : ''}`,
        )
        setTimeout(load, 500)
      } catch (err) {
        showToast(err.message || 'Could not mark the cards done')
      } finally {
        setBusy('signoff:all', false)
      }
    },
    [closeDrawer, load, showToast],
  )

  const moveTicket = useCallback(
    async (item, status, { note = '', message }) => {
      setBusy(item.id, true)
      try {
        if (note.trim()) await addTicketComment(item.ticket.ticket_id, note.trim())
        await updateTicket(item.ticket.ticket_id, { status })
        dropNeed(item.id)
        showToast(message || `${item.ticket.key} → ${status}`)
        closeDrawer()
        setTimeout(load, 500)
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
    if (typeof navigator === 'undefined' || !navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') {
      showToast('Could not copy. Your browser blocked the clipboard.')
      return
    }
    try {
      await navigator.clipboard.writeText(cursorPrompt(ticket))
      showToast('Cursor prompt copied. Paste it into Cursor.')
    } catch {
      showToast('Could not copy. Your browser blocked the clipboard.')
    }
  }

  const inProject = (project) => !projectFilter || project === projectFilter
  const needs = (data?.needs || []).filter((i) => inProject(projectOf(i)))
  const signoff = (data?.signoff || []).filter((i) => inProject(projectOf(i)))
  const working = (data?.working || []).filter((i) => inProject(projectOf(i)))
  const stuck = (data?.stuck || []).filter((i) => inProject(projectOf(i)))
  const ship = (data?.ship || []).filter((row) => inProject((row.key || '').toUpperCase()))
  const counts = { needs: needs.length, working: working.length }
  const projectCounts = (() => {
    const out = {}
    for (const item of [...(data?.needs || []), ...(data?.signoff || []), ...(data?.working || []), ...(data?.stuck || [])]) {
      const p = projectOf(item)
      if (p) out[p] = (out[p] || 0) + 1
    }
    for (const row of data?.ship || []) {
      const p = (row.key || '').toUpperCase()
      if (p && !(p in out)) out[p] = 0
    }
    return Object.entries(out).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  })()

  const openTicket = (item) => {
    setComment('')
    setDrawer({ type: 'working', item })
  }

  const retryStuck = async (item) => {
    const status = item.ticket.status
    setBusy(item.id, true)
    try {
      await updateTicket(item.ticket.ticket_id, { status: 'To Do' })
      await updateTicket(item.ticket.ticket_id, { status })
      showToast(`${item.ticket.key} restarted in ${item.stage.toLowerCase()}`)
      setTimeout(load, 500)
    } catch (err) {
      showToast(err.message || 'Could not restart the card')
    } finally {
      setBusy(item.id, false)
    }
  }
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
          Copy Cursor prompt
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
  } else if (drawer?.type === 'working') {
    const { item } = drawer
    drawerTitle = (
      <span>
        <ProjectTag project={projectOf(item)} label={item.ticket.key} />
        {item.ticket.title}
      </span>
    )
    drawerBody = (
      <div className="space-y-3">
        <p className={`text-sm rounded-lg p-3 ${item.stuck ? 'bg-amber-500/15' : 'bg-green-500/10'}`}>
          {item.stuck
            ? `Stuck in ${item.stage.toLowerCase()} for ${timeAgo(item.since, now)}. The AI step most likely stopped. Retry moves the card to To Do and back, which starts it again.`
            : `The AI is ${item.stage.toLowerCase()} this card. It moves to Needs you when there is something to approve.`}
        </p>
        <TicketDetail item={item} comment={comment} setComment={setComment} />
      </div>
    )
    drawerFooter = (
      <>
        <button type="button" onClick={() => copyCursor(item.ticket)} className="btn-secondary text-sm px-3">
          Copy Cursor prompt
        </button>
        <button
          type="button"
          onClick={() => onSwitchView('board', `/board?ticket=${encodeURIComponent(item.ticket.key)}`)}
          className="btn-ghost text-sm px-3"
        >
          Board
        </button>
        <span className="flex-1" />
        {comment.trim() && (
          <button
            type="button"
            onClick={async () => {
              try {
                await addTicketComment(item.ticket.ticket_id, comment.trim())
                setComment('')
                showToast(`Comment added to ${item.ticket.key}`)
              } catch (err) {
                showToast(err.message || 'Could not add the comment')
              }
            }}
            className="btn-secondary text-sm px-3"
          >
            Add comment
          </button>
        )}
        {item.stuck && (
          <button
            type="button"
            disabled={busyIds.has(item.id)}
            onClick={() => {
              closeDrawer()
              retryStuck(item)
            }}
            className="btn-accent text-sm px-3"
          >
            Retry
          </button>
        )}
      </>
    )
  } else if (drawer?.type === 'signoff-all') {
    const items = data?.signoff || []
    drawerTitle = `Mark ${items.length} shipped card${items.length === 1 ? '' : 's'} done`
    drawerBody = (
      <div className="space-y-3 text-sm">
        <p>
          These cards are merged and in production, waiting in <b>Final approval</b>. Marking them done moves them to
          the Done column. Nothing is deployed or reverted.
        </p>
        <p className="text-muted">
          One note goes to the Product thread for the whole batch, instead of one per card. Cards still count toward their
          release.
        </p>
        <p className="text-xs text-muted">
          {items
            .slice(0, 12)
            .map((i) => i.ticket.key)
            .join(', ')}
          {items.length > 12 ? ` and ${items.length - 12} more` : ''}
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
          disabled={busyIds.has('signoff:all')}
          onClick={() => closeAllSignoff(items)}
          className="btn-accent text-sm px-3"
        >
          {busyIds.has('signoff:all') ? 'Marking…' : `Mark ${items.length} done`}
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
          {featureEnabled('objectives') && (
            <button type="button" onClick={() => onSwitchView('objectives')} className="text-sm btn-ghost px-3 py-2 hidden lg:inline-flex">
              Objectives
            </button>
          )}
          <ThemeToggle />
          <SettingsButton onClick={onOpenSettings} />
          <button type="button" onClick={onLogout} className="hidden lg:inline-flex text-sm text-muted px-2 min-h-[44px] items-center">
            Log out
          </button>
        </div>
        <div className="lg:hidden flex gap-0.5 px-2 pb-2" role="tablist" aria-label="Cockpit sections">
          {TABS.map((t) => {
            const n = t.id === 'needs' ? counts.needs : t.id === 'working' ? counts.working : null
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
                className={`flex-1 min-w-0 whitespace-nowrap rounded-lg text-[12px] min-[360px]:text-[13px] font-medium px-1 py-1.5 min-h-[36px] transition-all ${
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
        {projectCounts.length > 1 && (
          <div className="flex gap-1.5 overflow-x-auto scrollbar-hide -mx-1 px-1 pb-3" role="group" aria-label="Filter by project">
            <button
              type="button"
              onClick={() => setProjectFilter('')}
              aria-pressed={!projectFilter}
              className={`flex-none text-xs rounded-full px-3 py-1 border transition-all ${
                !projectFilter ? 'bg-text text-bg border-text' : 'border-border text-muted hover:text-text'
              }`}
            >
              All
            </button>
            {projectCounts.map(([project, n]) => (
              <button
                key={project}
                type="button"
                onClick={() => setProjectFilter((current) => (current === project ? '' : project))}
                aria-pressed={projectFilter === project}
                className={`flex-none text-xs rounded-full px-3 py-1 border font-mono transition-all ${
                  projectFilter === project
                    ? `${projectColor(project)} border-transparent ring-2 ring-accent/30`
                    : 'border-border text-muted hover:text-text'
                }`}
              >
                {project}
                {n ? <span className="ml-1 opacity-70">{n}</span> : null}
              </button>
            ))}
          </div>
        )}
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
            {signoff.length > 0 && (
              <div className="mt-3 pt-3 border-t border-border">
                <div className="flex items-center gap-2 flex-wrap">
                  <span aria-hidden="true" className="text-green-600 dark:text-green-400">✓</span>
                  <span className="text-sm">
                    <b>{signoff.length}</b> shipped card{signoff.length === 1 ? '' : 's'} waiting for sign-off
                  </span>
                  <div className="ml-auto flex gap-1.5">
                    <button
                      type="button"
                      onClick={() => setShowSignoff((v) => !v)}
                      aria-expanded={showSignoff}
                      className="btn-ghost text-xs px-2.5 py-1 min-h-[32px]"
                    >
                      {showSignoff ? 'Hide' : 'Show'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setDrawer({ type: 'signoff-all' })}
                      className="btn-secondary text-xs px-2.5 py-1 min-h-[32px]"
                    >
                      Mark all done
                    </button>
                  </div>
                </div>
                {showSignoff && (
                  <ul className="mt-1">
                    {signoff.map((item) => (
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
                )}
              </div>
            )}
          </Section>

          <Section id="working" title="AI working" count={data ? counts.working : null} mobileTab={tab}>
            {data && working.length === 0 && (
              <p className="text-sm text-muted py-6 text-center">Nothing running now. Approve a card to start some.</p>
            )}
            <ul>
              {working.map((item) => (
                <WorkingRow
                  key={item.id}
                  item={item}
                  now={now}
                  onOpenChat={onOpenChat}
                  onOpenTicket={openTicket}
                  onRetry={retryStuck}
                  busy={busyIds.has(item.id)}
                />
              ))}
            </ul>
            {stuck.length > 0 && (
              <div className="mt-3 pt-3 border-t border-border">
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-amber-500" aria-hidden="true" />
                  <span className="text-sm">
                    <b>{stuck.length}</b> stuck: in an AI column for over a day
                  </span>
                  <button
                    type="button"
                    onClick={() => setShowStuck((v) => !v)}
                    aria-expanded={showStuck}
                    className="btn-ghost text-xs px-2.5 py-1 min-h-[32px] ml-auto"
                  >
                    {showStuck ? 'Hide' : 'Show'}
                  </button>
                </div>
                {showStuck && (
                  <ul className="mt-1">
                    {stuck.map((item) => (
                      <WorkingRow
                        key={item.id}
                        item={item}
                        now={now}
                        onOpenChat={onOpenChat}
                        onOpenTicket={openTicket}
                        onRetry={retryStuck}
                        busy={busyIds.has(item.id)}
                      />
                    ))}
                  </ul>
                )}
              </div>
            )}
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
                  steps={data?.ship_steps || []}
                  busy={busyIds.has(`ship:${row.key}`)}
                  onOpenChat={onOpenChat}
                  onCommand={(r, cmd) => setDrawer({ type: 'command', row: r, cmd })}
                  onReview={(r) => {
                    const pending = needs.find(
                      (n) => n.type === 'deploy_confirm' && n.project_key?.toUpperCase() === r.key?.toUpperCase(),
                    )
                    if (pending) openNeed(pending)
                    else onOpenChat('devops')
                  }}
                />
              ))}
            </div>
          </Section>

          <Section
            id="week"
            title="This week"
            mobileTab={tab}
            className="lg:col-span-2"
            right={
              <button type="button" onClick={() => loadMoney(true)} className="btn-ghost text-xs px-2.5 py-1 min-h-[32px]">
                Refresh numbers
              </button>
            }
          >
            <WeekTiles week={data?.week} money={money} moneyError={moneyError} />
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
