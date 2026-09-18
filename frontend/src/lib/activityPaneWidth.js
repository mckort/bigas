export const ACTIVITY_PANE_DEFAULT_WIDTH = 320
export const ACTIVITY_PANE_MIN_WIDTH = 240
export const ACTIVITY_PANE_MAX_WIDTH = 560
export const CHAT_MIN_WIDTH = 400
/** Agent sidebar uses Tailwind `w-72` (18rem) on large screens. */
export const DESKTOP_AGENT_SIDEBAR_WIDTH = 288
export const ACTIVITY_PANE_STORAGE_KEY = 'bigas-activity-pane-width'

export function clampActivityPaneWidth(width, viewportWidth) {
  const w = Number(width)
  if (!Number.isFinite(w)) return ACTIVITY_PANE_DEFAULT_WIDTH
  const vw =
    viewportWidth ??
    (typeof window !== 'undefined' ? window.innerWidth : ACTIVITY_PANE_DEFAULT_WIDTH + CHAT_MIN_WIDTH + DESKTOP_AGENT_SIDEBAR_WIDTH)
  const maxFromChat = vw - DESKTOP_AGENT_SIDEBAR_WIDTH - CHAT_MIN_WIDTH
  const max = Math.min(ACTIVITY_PANE_MAX_WIDTH, maxFromChat)
  const min = ACTIVITY_PANE_MIN_WIDTH
  if (max < min) return min
  return Math.round(Math.min(max, Math.max(min, w)))
}

export function readStoredActivityPaneWidth(viewportWidth) {
  if (typeof localStorage === 'undefined') {
    return clampActivityPaneWidth(ACTIVITY_PANE_DEFAULT_WIDTH, viewportWidth)
  }
  try {
    const raw = localStorage.getItem(ACTIVITY_PANE_STORAGE_KEY)
    if (raw == null) return clampActivityPaneWidth(ACTIVITY_PANE_DEFAULT_WIDTH, viewportWidth)
    return clampActivityPaneWidth(parseInt(raw, 10), viewportWidth)
  } catch {
    return clampActivityPaneWidth(ACTIVITY_PANE_DEFAULT_WIDTH, viewportWidth)
  }
}

export function persistActivityPaneWidth(width) {
  try {
    localStorage.setItem(ACTIVITY_PANE_STORAGE_KEY, String(width))
  } catch {
    /* ignore quota / private mode */
  }
}
