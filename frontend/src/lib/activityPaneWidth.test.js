import assert from 'node:assert/strict'
import test from 'node:test'
import {
  ACTIVITY_PANE_DEFAULT_WIDTH,
  ACTIVITY_PANE_MAX_WIDTH,
  ACTIVITY_PANE_MIN_WIDTH,
  CHAT_MIN_WIDTH,
  DESKTOP_AGENT_SIDEBAR_WIDTH,
  clampActivityPaneWidth,
} from './activityPaneWidth.js'

test('clampActivityPaneWidth enforces activity min and max', () => {
  const wideViewport = 2000
  assert.equal(clampActivityPaneWidth(100, wideViewport), ACTIVITY_PANE_MIN_WIDTH)
  assert.equal(clampActivityPaneWidth(900, wideViewport), ACTIVITY_PANE_MAX_WIDTH)
  assert.equal(clampActivityPaneWidth(320, wideViewport), ACTIVITY_PANE_DEFAULT_WIDTH)
})

test('clampActivityPaneWidth keeps chat at least CHAT_MIN_WIDTH', () => {
  const viewport = DESKTOP_AGENT_SIDEBAR_WIDTH + CHAT_MIN_WIDTH + 300
  assert.equal(clampActivityPaneWidth(500, viewport), 300)
})
