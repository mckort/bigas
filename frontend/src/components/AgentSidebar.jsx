export function UnreadDot({ show, className = '' }) {
  if (!show) return null
  return (
    <span
      className={`absolute top-0 right-0 w-2.5 h-2.5 rounded-full bg-bigas-black ring-2 ring-white ${className}`}
      aria-label="Unread messages"
    />
  )
}

export default function AgentSidebar({
  agents,
  activeAgentId,
  onSelectAgent,
  onOpenSettings,
  mobileOpen,
  onClose,
  unreadAgentIds,
}) {
  return (
    <>
      {mobileOpen && (
        <div className="fixed inset-0 bg-black/30 z-40 lg:hidden" onClick={onClose} aria-hidden="true" />
      )}
      <aside
        className={`fixed lg:static inset-y-0 left-0 z-50 w-72 bg-surface border-r border-border flex flex-col transform transition-transform duration-200 lg:translate-x-0 ${
          mobileOpen ? 'translate-x-0 shadow-card' : '-translate-x-full'
        }`}
      >
        <div className="p-4 border-b border-border">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <img
                src="/bigas-logo.png"
                alt="Bigas"
                className="h-10 w-10 rounded-xl object-cover flex-shrink-0"
              />
              <div className="min-w-0">
                <h2 className="font-bold text-base leading-tight truncate">Bigas</h2>
                <p className="text-xs text-muted truncate">Your AI team</p>
              </div>
            </div>
            <button
              type="button"
              className="lg:hidden text-muted hover:text-text p-2 -mr-1 min-w-[44px] min-h-[44px] flex items-center justify-center"
              onClick={onClose}
              aria-label="Close menu"
            >
              ✕
            </button>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto scrollbar-thin p-3 space-y-1">
          <p className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
            Specialists
          </p>
          {agents.map((agent) => {
            const isActive = activeAgentId === agent.agent_id
            const hasUnread = Boolean(unreadAgentIds?.has(agent.agent_id))
            return (
              <button
                key={agent.agent_id}
                type="button"
                onClick={() => {
                  onSelectAgent(agent.agent_id)
                  onClose?.()
                }}
                className={`w-full flex items-center gap-3 px-3 py-3 rounded-xl text-left transition-colors min-h-[52px] ${
                  isActive
                    ? 'bg-bigas-blue text-bigas-black shadow-soft border border-black/10'
                    : 'hover:bg-white border border-transparent text-text'
                }`}
              >
                <span
                  className={`relative flex-shrink-0 flex items-center justify-center w-9 h-9 rounded-lg ${
                    isActive ? 'bg-white/60' : 'bg-white border border-border'
                  } ${
                    (agent.icon || '').includes('<')
                      ? 'font-mono text-[11px] font-semibold tracking-tight'
                      : 'text-xl'
                  }`}
                >
                  {agent.icon || '🤖'}
                  <UnreadDot show={hasUnread} />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate text-sm">{agent.name}</div>
                  <div className="text-xs text-muted truncate capitalize">
                    {agent.agent_id.replace(/_/g, ' ')}
                  </div>
                </div>
              </button>
            )
          })}
        </nav>

        <div className="p-3 border-t border-border">
          <button
            type="button"
            onClick={onOpenSettings}
            className="w-full flex items-center gap-2 text-sm text-muted hover:text-text py-3 px-3 rounded-xl hover:bg-white transition-colors min-h-[44px]"
          >
            <span aria-hidden="true">⚙️</span>
            Agent settings
          </button>
        </div>
      </aside>
    </>
  )
}
