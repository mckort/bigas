export function UnreadDot({ show, className = '' }) {
  if (!show) return null
  return (
    <span
      className={`absolute top-0 right-0 w-2.5 h-2.5 rounded-full bg-accent ring-2 ring-elevated ${className}`}
      aria-label="Unread messages"
    />
  )
}

const AGENT_SIDEBAR_ORDER = ['chief', 'cfo', 'cto', 'devops', 'marketing', 'product']

function sortAgents(agents) {
  return [...agents].sort((a, b) => {
    const aRank = AGENT_SIDEBAR_ORDER.indexOf(a.agent_id)
    const bRank = AGENT_SIDEBAR_ORDER.indexOf(b.agent_id)
    const aOrder = aRank === -1 ? AGENT_SIDEBAR_ORDER.length : aRank
    const bOrder = bRank === -1 ? AGENT_SIDEBAR_ORDER.length : bRank
    if (aOrder !== bOrder) return aOrder - bOrder
    return (a.name || '').localeCompare(b.name || '')
  })
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
  const orderedAgents = sortAgents(agents)
  return (
    <>
      {mobileOpen && (
        <div className="fixed inset-0 bg-overlay z-40 lg:hidden" onClick={onClose} aria-hidden="true" />
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
          {orderedAgents.map((agent) => {
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
                className={`w-full flex items-center gap-3 px-3 py-3 rounded-lg text-left transition-all duration-150 min-h-[52px] ${
                  isActive
                    ? 'nav-item-active'
                    : 'hover:bg-elevated border border-transparent text-text'
                }`}
              >
                <span
                  className={`relative flex-shrink-0 flex items-center justify-center w-9 h-9 rounded-lg ${
                    isActive ? 'bg-elevated/80' : 'bg-elevated border border-border'
                  } ${
                    (agent.icon || '').includes('<')
                      ? 'font-mono text-[11px] font-semibold tracking-tight'
                      : 'text-xl'
                  }`}
                >
                  {agent.icon || '🤖'}
                  <UnreadDot show={hasUnread} />
                </span>
                <div className="min-w-0 flex-1 font-medium truncate text-sm">{agent.name}</div>
              </button>
            )
          })}
        </nav>

        <div className="p-3 border-t border-border">
          <button
            type="button"
            onClick={onOpenSettings}
            className="w-full flex items-center gap-2 text-sm text-muted hover:text-text py-3 px-3 rounded-lg hover:bg-elevated transition-all duration-150 min-h-[44px]"
          >
            <span aria-hidden="true">⚙️</span>
            Settings
          </button>
        </div>
      </aside>
    </>
  )
}
