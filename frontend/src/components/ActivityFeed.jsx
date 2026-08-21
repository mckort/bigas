import { useState } from 'react'

const PREVIEW_LINES = 5
const URL_RE = /(https?:\/\/[^\s<>"'`\]},]+(?:\([^\s<>"'`\]},)]*\)[^\s<>"'`\]},]*)*)/g
const MD_LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g

function linkAnchor(href, label, key) {
  return (
    <a
      key={key}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-bigas-black underline underline-offset-2 hover:opacity-70 break-all"
    >
      {label}
    </a>
  )
}

function linkifyUrls(text, keyPrefix) {
  const parts = text.split(URL_RE)
  return parts.map((part, i) =>
    i % 2 === 1 ? linkAnchor(part, part, `${keyPrefix}-u${i}`) : part
  )
}

function linkify(text) {
  if (!text || typeof text !== 'string') return text
  const nodes = []
  let lastIndex = 0
  let match
  const re = new RegExp(MD_LINK_RE.source, 'g')
  let idx = 0
  while ((match = re.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(...linkifyUrls(text.slice(lastIndex, match.index), `t${idx}`))
    }
    nodes.push(linkAnchor(match[2], match[1], `m${idx}`))
    lastIndex = match.index + match[0].length
    idx += 1
  }
  if (lastIndex < text.length) {
    nodes.push(...linkifyUrls(text.slice(lastIndex), `t${idx}`))
  }
  return nodes
}

function CollapsibleContent({ content }) {
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
        {linkify(text)}
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

export default function ActivityFeed({ events, open, onClose }) {
  return (
    <>
      {open && (
        <div className="fixed inset-0 bg-black/30 z-40 lg:hidden" onClick={onClose} aria-hidden="true" />
      )}
      <aside
        className={`fixed lg:static inset-y-0 right-0 z-50 w-full sm:w-80 bg-surface border-l border-border flex flex-col transform transition-transform duration-200 lg:translate-x-0 ${
          open ? 'translate-x-0 shadow-card' : 'translate-x-full lg:translate-x-0'
        } ${!open ? 'hidden lg:flex' : 'flex'}`}
      >
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
              className="bg-white border border-border rounded-xl p-3 shadow-soft"
            >
              <div className="flex items-center gap-2 text-[11px] text-muted mb-2 uppercase tracking-wide">
                <span className="inline-block w-1.5 h-1.5 rounded-full bg-bigas-blue flex-shrink-0" />
                <span>{event.source || event.type}</span>
                <span>·</span>
                <time className="normal-case tracking-normal">{new Date(event.created_at).toLocaleString()}</time>
              </div>
              <CollapsibleContent content={event.content} />
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}
