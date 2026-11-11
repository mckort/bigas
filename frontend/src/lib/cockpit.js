import { createThread, fetchThreads, sendMessage } from './api'

/** Post a message to an agent's most recent thread (or a new one). */
export async function sendToAgent(agentId, content) {
  let threadId = ''
  try {
    const listed = await fetchThreads()
    const thread = (listed.threads || []).find((t) => t.agent_id === agentId)
    threadId = thread?.thread_id || ''
  } catch {
    threadId = ''
  }
  if (!threadId) {
    const created = await createThread(agentId)
    threadId = created.thread?.thread_id || ''
  }
  if (!threadId) throw new Error('Could not open a chat thread')
  await sendMessage(threadId, content, `cockpit-${Date.now()}`)
  return threadId
}

export function timeAgo(iso, now = Date.now()) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const sec = Math.max(0, Math.round((now - t) / 1000))
  if (sec < 60) return 'now'
  const min = Math.round(sec / 60)
  if (min < 60) return `${min}m`
  const hrs = Math.round(min / 60)
  if (hrs < 48) return `${hrs}h`
  return `${Math.round(hrs / 24)}d`
}

export function cursorPrompt(ticket) {
  const key = ticket?.key || ''
  const lines = [
    `Work on ${key}: ${ticket?.title || ''}`.trim(),
    `Read the description, plan and comments with the Bigas MCP tool lookup_ticket ${key}.`,
    `Use branch ${key} and start every commit message with "${key}:".`,
  ]
  if (ticket?.url) {
    const url = ticket.url.startsWith('/') ? `${window.location.origin}${ticket.url}` : ticket.url
    lines.push(`Card: ${url}`)
  }
  return lines.join('\n')
}

export const WEEKLY_ACTIONS = [
  {
    id: 'progress',
    agentId: 'product',
    title: 'Weekly PM update',
    body: 'Progress from the board and commits over the last 7 days, plus a LinkedIn post drafted from it.',
    button: 'Write update',
    prompt:
      'Write this week\'s progress update from the board and git commits for the last 7 days (use progress_updates). ' +
      'Then draft one LinkedIn post from it: first person, founder voice, the three most important changes, one lesson learned, what is next.',
  },
  {
    id: 'x',
    agentId: 'product',
    title: 'X posts',
    body: 'Drafts per X account from this week\'s shipped work, with an approve-and-post link.',
    button: 'Draft X posts',
    prompt: 'Generate this week\'s X post drafts (use generate_weekly_x_post).',
  },
  {
    id: 'cfo',
    agentId: 'cfo',
    title: 'Money this week',
    body: 'AI and GCP spend plus month-to-date books from Accounted, with anything unusual flagged.',
    button: 'Run CFO report',
    prompt:
      'Give me the weekly CFO report (use weekly_cto_ai_report): AI and GCP spend, month-to-date books per company, and flag anything unusual.',
  },
]
