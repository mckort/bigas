import { useState } from 'react'

export default function AgentSidebar({ agents, activeAgentId, onSelectAgent, onOpenSettings, mobileOpen, onClose }) {
  return (
    <>
      {mobileOpen && (
        <div className="fixed inset-0 bg-black/60 z-40 lg:hidden" onClick={onClose} />
      )}
      <aside
        className={`fixed lg:static inset-y-0 left-0 z-50 w-72 bg-surface border-r border-border flex flex-col transform transition-transform lg:translate-x-0 ${
          mobileOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="p-4 border-b border-border flex items-center justify-between">
          <h2 className="font-semibold text-lg">Agents</h2>
          <button className="lg:hidden text-muted" onClick={onClose}>✕</button>
        </div>
        <nav className="flex-1 overflow-y-auto p-2 space-y-1">
          {agents.map((agent) => (
            <button
              key={agent.agent_id}
              onClick={() => {
                onSelectAgent(agent.agent_id)
                onClose?.()
              }}
              className={`w-full flex items-center gap-3 px-3 py-3 rounded-xl text-left transition ${
                activeAgentId === agent.agent_id
                  ? 'bg-accent/20 border border-accent/40'
                  : 'hover:bg-bg border border-transparent'
              }`}
            >
              <span
                className={
                  (agent.icon || '').includes('<')
                    ? 'font-mono text-xs font-semibold tracking-tight w-8 text-center'
                    : 'text-2xl'
                }
              >
                {agent.icon || '🤖'}
              </span>
              <div className="min-w-0">
                <div className="font-medium truncate">{agent.name}</div>
                <div className="text-xs text-muted truncate">{agent.agent_id}</div>
              </div>
            </button>
          ))}
        </nav>
        <div className="p-3 border-t border-border">
          <button
            onClick={onOpenSettings}
            className="w-full text-sm text-muted hover:text-text py-2"
          >
            ⚙️ Agent settings
          </button>
        </div>
      </aside>
    </>
  )
}
