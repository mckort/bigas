import { useCallback, useEffect, useState } from 'react'
import {
  ACTIVITY_PANE_DEFAULT_WIDTH,
  ACTIVITY_PANE_MAX_WIDTH,
  ACTIVITY_PANE_MIN_WIDTH,
  clampActivityPaneWidth,
  persistActivityPaneWidth,
  readStoredActivityPaneWidth,
} from '../lib/activityPaneWidth.js'

const DESKTOP_MEDIA = '(min-width: 1024px)'

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

export default function ActivityFeed({ events, open, onClose, onOpenBoard }) {
  const [desktopWidth, setDesktopWidth] = useState(() => readStoredActivityPaneWidth())
  const [isDesktop, setIsDesktop] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(DESKTOP_MEDIA).matches
  )
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_MEDIA)
    const onChange = () => setIsDesktop(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    if (!isDesktop) return undefined
    const onResize = () => {
      setDesktopWidth((w) => clampActivityPaneWidth(w))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [isDesktop])

  const resetDesktopWidth = useCallback(() => {
    const next = clampActivityPaneWidth(ACTIVITY_PANE_DEFAULT_WIDTH)
    setDesktopWidth(next)
    persistActivityPaneWidth(next)
  }, [])

  const startResize = useCallback((clientX) => {
    const startX = clientX
    const startWidth = desktopWidth

    const onMove = (e) => {
      const next = clampActivityPaneWidth(startWidth + (startX - e.clientX))
      setDesktopWidth(next)
    }

    const onUp = () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.removeProperty('user-select')
      document.body.style.removeProperty('cursor')
      setDesktopWidth((w) => {
        persistActivityPaneWidth(w)
        return w
      })
    }

    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [desktopWidth])

  const onResizePointerDown = (e) => {
    if (e.button !== 0) return
    e.preventDefault()
    startResize(e.clientX)
  }

  const onResizeKeyDown = (e) => {
    if (e.key === 'Home') {
      e.preventDefault()
      resetDesktopWidth()
    }
  }

  return (
    <>
      {open && (
        <div className="fixed inset-0 bg-overlay z-40 lg:hidden" onClick={onClose} aria-hidden="true" />
      )}
      <aside
        style={isDesktop ? { width: desktopWidth } : undefined}
        className={`fixed lg:static inset-y-0 right-0 z-50 w-full sm:w-80 lg:w-auto lg:flex-shrink-0 bg-surface border-l border-border flex flex-col transform transition-transform duration-200 lg:translate-x-0 ${
          open ? 'translate-x-0 shadow-card' : 'translate-x-full lg:translate-x-0'
        } ${!open ? 'hidden lg:flex' : 'flex'}`}
      >
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize activity pane"
          aria-valuemin={ACTIVITY_PANE_MIN_WIDTH}
          aria-valuemax={ACTIVITY_PANE_MAX_WIDTH}
          aria-valuenow={isDesktop ? desktopWidth : undefined}
          tabIndex={0}
          onMouseDown={onResizePointerDown}
          onDoubleClick={resetDesktopWidth}
          onKeyDown={onResizeKeyDown}
          className="hidden lg:block absolute left-0 top-0 bottom-0 w-1.5 -translate-x-1/2 cursor-col-resize z-10 touch-none hover:bg-accent/25 focus:outline-none focus-visible:bg-accent/35"
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
