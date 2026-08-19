import { useState } from 'react'

const PREVIEW_LINES = 5

function CollapsibleContent({ content }) {
  const [expanded, setExpanded] = useState(false)
  const text = content || ''
  const lineCount = text.split('\n').length
  const needsCollapse = lineCount > PREVIEW_LINES || text.length > 400

  return (
    <div>
      <p
        className={`text-sm whitespace-pre-wrap break-words ${
          !expanded && needsCollapse ? 'line-clamp-5' : ''
        }`}
      >
        {text}
      </p>
      {needsCollapse && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 text-xs text-accent hover:underline"
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
      {open && <div className="fixed inset-0 bg-black/60 z-40 lg:hidden" onClick={onClose} />}
      <aside
        className={`fixed lg:static inset-y-0 right-0 z-50 w-full sm:w-80 bg-surface border-l border-border flex flex-col transform transition-transform lg:translate-x-0 ${
          open ? 'translate-x-0' : 'translate-x-full lg:translate-x-0'
        } ${!open ? 'hidden lg:flex' : 'flex'}`}
      >
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h2 className="font-semibold">Activity</h2>
          <button className="lg:hidden text-muted" onClick={onClose}>✕</button>
        </div>
        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          {events.length === 0 && (
            <p className="text-muted text-sm text-center py-8">No activity yet</p>
          )}
          {events.map((event) => (
            <div key={event.id} className="bg-bg border border-border rounded-xl p-3">
              <div className="flex items-center gap-2 text-xs text-muted mb-2">
                <span className="uppercase">{event.source || event.type}</span>
                <span>·</span>
                <time>{new Date(event.created_at).toLocaleString()}</time>
              </div>
              <CollapsibleContent content={event.content} />
            </div>
          ))}
        </div>
      </aside>
    </>
  )
}
