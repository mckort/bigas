import { useEffect, useRef, useState } from 'react'

const PREVIEW_LINES = 5
const URL_RE = /(https?:\/\/[^\s<>"'`\]},]+(?:\([^\s<>"'`\]},)]*\)[^\s<>"'`\]},]*)*)/g
const MD_LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^)\s]+|\/[^)\s]+)\)/g
const TICKET_KEY_RE = /(`?\b[A-Z][A-Z0-9]+-\d+\b`?)/

function isInternalBoardHref(href) {
  return (
    typeof href === 'string' &&
    (href === '/board' || href.startsWith('/board?') || href.startsWith('/board/'))
  )
}

function ticketKeyFromToken(token) {
  return token.replace(/^`|`$/g, '')
}

function ticketHref(key) {
  return `/board?ticket=${key}`
}

function linkAnchor(href, label, key, onOpenBoard) {
  const internal = isInternalBoardHref(href)
  return (
    <a
      key={key}
      href={href}
      {...(internal
        ? {
            onClick: (event) => {
              if (
                event.defaultPrevented ||
                event.button !== 0 ||
                event.metaKey ||
                event.ctrlKey ||
                event.shiftKey ||
                event.altKey
              ) {
                return
              }
              event.preventDefault()
              if (onOpenBoard) {
                onOpenBoard(href)
              } else {
                window.location.assign(href)
              }
            },
          }
        : {
            target: '_blank',
            rel: 'noopener noreferrer',
          })}
      className="text-accent underline underline-offset-2 hover:opacity-70 break-all transition-opacity duration-150"
    >
      {label}
    </a>
  )
}

function linkifyTicketKeys(text, keyPrefix, onOpenBoard) {
  if (!text) return []
  const parts = text.split(TICKET_KEY_RE)
  return parts.map((part, i) =>
    i % 2 === 1
      ? linkAnchor(ticketHref(ticketKeyFromToken(part)), part, `${keyPrefix}-k${i}`, onOpenBoard)
      : part
  )
}

function linkifyUrls(text, keyPrefix, onOpenBoard) {
  const parts = text.split(URL_RE)
  return parts.flatMap((part, i) =>
    i % 2 === 1
      ? [linkAnchor(part, part, `${keyPrefix}-u${i}`, onOpenBoard)]
      : linkifyTicketKeys(part, `${keyPrefix}-t${i}`, onOpenBoard)
  )
}

function linkify(text, onOpenBoard) {
  if (!text || typeof text !== 'string') return text
  const nodes = []
  let lastIndex = 0
  let match
  const re = new RegExp(MD_LINK_RE.source, 'g')
  let idx = 0
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(...linkifyUrls(text.slice(lastIndex, match.index), `t${idx}`, onOpenBoard))
    }
    nodes.push(linkAnchor(match[2], match[1], `m${idx}`, onOpenBoard))
    lastIndex = match.index + match[0].length
    idx += 1
  }
  if (lastIndex < text.length) {
    nodes.push(...linkifyUrls(text.slice(lastIndex), `t${idx}`, onOpenBoard))
  }
  return nodes
}

function CollapsibleContent({ content, onOpenBoard }) {
  const [expanded, setExpanded] = useState(false)
  const text = content || ''
  const lineCount = text.split('\n').length
  const needsCollapse = lineCount > PREVIEW_LINES || text.length > 400

  return (
    <div>
      <p
        className={`text-sm whitespace-pre-wrap break-words text-text ${
          !expanded && needsCollapse ? 'line-clamp-5' : ''
        }`}
      >
        {linkify(text, onOpenBoard)}
      </p>
      {needsCollapse && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 text-xs text-muted hover:text-text underline underline-offset-2"
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  )
}

const ACTIVITY_WIDTH_KEY = 'bigas_activity_width'
const DEFAULT_ACTIVITY_WIDTH = 320
const MIN_ACTIVITY_WIDTH = 240
const MAX_ACTIVITY_WIDTH = 560
const MIN_CHAT_WIDTH = 400
const AGENT_SIDEBAR_WIDTH = 288
const DESKTOP_LAYOUT_MIN_WIDTH = 1024

function clampActivityWidth(width, viewportWidth = typeof window === 'undefined' ? 1280 : window.innerWidth) {
  let maxWidth = MAX_ACTIVITY_WIDTH
  if (viewportWidth >= DESKTOP_LAYOUT_MIN_WIDTH) {
    const maxByChat = viewportWidth - AGENT_SIDEBAR_WIDTH - MIN_CHAT_WIDTH
    maxWidth = Math.max(MIN_ACTIVITY_WIDTH, Math.min(MAX_ACTIVITY_WIDTH, maxByChat))
  }
  return Math.round(Math.min(maxWidth, Math.max(MIN_ACTIVITY_WIDTH, width)))
}

function readActivityWidth() {
  try {
    const raw = localStorage.getItem(ACTIVITY_WIDTH_KEY)
    const parsed = Number.parseInt(raw, 10)
    if (Number.isFinite(parsed)) return clampActivityWidth(parsed)
  } catch {
    /* ignore quota / private mode */
  }
  return DEFAULT_ACTIVITY_WIDTH
}

function writeActivityWidth(width) {
  try {
    localStorage.setItem(ACTIVITY_WIDTH_KEY, String(width))
  } catch {
    /* ignore quota / private mode */
  }
}

export default function ActivityFeed({ events, open, onClose, onOpenBoard }) {
  const [width, setWidth] = useState(DEFAULT_ACTIVITY_WIDTH)
  const [dragging, setDragging] = useState(false)
  const widthRef = useRef(width)
  const dragCleanupRef = useRef(null)
  widthRef.current = width

  useEffect(() => {
    setWidth(readActivityWidth())
  }, [])

  useEffect(() => {
    function onResize() {
      setWidth((current) => clampActivityWidth(current))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    return () => {
      dragCleanupRef.current?.()
      dragCleanupRef.current = null
    }
  }, [])

  function beginResize(event) {
    if (event.button != null && event.button !== 0) return
    event.preventDefault()
    const handle = event.currentTarget
    handle.setPointerCapture(event.pointerId)
    const startX = event.clientX
    const startWidth = width
    setDragging(true)

    const previousUserSelect = document.body.style.userSelect
    const previousCursor = document.body.style.cursor
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'

    function onMove(moveEvent) {
      const next = clampActivityWidth(startWidth + (startX - moveEvent.clientX))
      widthRef.current = next
      setWidth(next)
    }

    function endDrag({ persist }) {
      if (dragCleanupRef.current !== endDragBound) return
      dragCleanupRef.current = null
      if (persist) writeActivityWidth(widthRef.current)
      setDragging(false)
      document.body.style.userSelect = previousUserSelect
      document.body.style.cursor = previousCursor
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      try {
        handle.releasePointerCapture(event.pointerId)
      } catch {
        /* capture may already be released */
      }
    }

    function onUp() {
      endDrag({ persist: true })
    }

    function endDragBound() {
      endDrag({ persist: false })
    }

    dragCleanupRef.current = endDragBound

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

  function resetWidth() {
    const next = clampActivityWidth(DEFAULT_ACTIVITY_WIDTH)
    setWidth(next)
    writeActivityWidth(next)
  }

  function nudgeWidth(delta) {
    setWidth((current) => {
      const next = clampActivityWidth(current + delta)
      writeActivityWidth(next)
      return next
    })
  }

  return (
    <>
      {open && (
        <div className="fixed inset-0 bg-overlay z-40 lg:hidden" onClick={onClose} aria-hidden="true" />
      )}
      <aside
        style={{ '--activity-width': `${width}px` }}
        className={`fixed lg:relative lg:inset-auto inset-y-0 right-0 z-50 w-full sm:w-80 lg:w-[var(--activity-width)] lg:flex-shrink-0 lg:h-full bg-surface border-l border-border flex flex-col transform transition-transform duration-200 lg:translate-x-0 ${
          open ? 'translate-x-0 shadow-card' : 'translate-x-full lg:translate-x-0'
        } ${!open ? 'hidden lg:flex' : 'flex'}`}
      >
        <button
          type="button"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize activity panel"
          aria-valuemin={MIN_ACTIVITY_WIDTH}
          aria-valuemax={MAX_ACTIVITY_WIDTH}
          aria-valuenow={width}
          title="Drag to resize. Double-click to reset."
          onPointerDown={beginResize}
          onDoubleClick={resetWidth}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') {
              event.preventDefault()
              nudgeWidth(16)
            } else if (event.key === 'ArrowRight') {
              event.preventDefault()
              nudgeWidth(-16)
            } else if (event.key === 'Home') {
              event.preventDefault()
              resetWidth()
            }
          }}
          className={`hidden lg:block absolute inset-y-0 left-0 z-20 w-3 -translate-x-1/2 cursor-col-resize touch-none border-0 p-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 ${
            dragging ? 'bg-accent/20' : 'bg-transparent hover:bg-accent/15'
          }`}
        />
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div>
            <h2 className="font-semibold text-sm">Activity</h2>
            <p className="text-xs text-muted mt-0.5">Background tasks & alerts</p>
          </div>
          <button
            type="button"
            className="lg:hidden text-muted hover:text-text p-2 min-w-[44px] min-h-[44px] flex items-center justify-center"
            onClick={onClose}
            aria-label="Close activity"
          >
            ✕
          </button>
        </div>
        <div className="flex-1 overflow-y-auto scrollbar-thin p-3 space-y-2">
          {events.length === 0 && (
            <p className="text-muted text-sm text-center py-12 px-4">
              No activity yet — reports, PR reviews, and deploy updates will appear here.
            </p>
          )}
          {events.map((event) => (
            <div
              key={event.id}
              className="card p-3 transition-all duration-150 hover:shadow-card"
            >
              <div className="flex items-center gap-2 text-[11px] text-muted mb-2 uppercase tracking-wide">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-accent flex-shrink-0" />
                <span>{event.source || event.type}</span>
                <span>·</span>
                <time className="normal-case tracking-normal">{new Date(event.created_at).toLocaleString()}</time>
              </div>
              <CollapsibleContent content={event.content} onOpenBoard={onOpenBoard} />
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}
