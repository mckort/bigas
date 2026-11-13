import assert from 'node:assert/strict'
import test from 'node:test'
import { applyDeployUpdate, deployProgressLines, deployRunPhase } from './deployProgress.js'

const since = '2026-11-13T14:00:00.000Z'

function message(overrides) {
  return {
    message_id: 'm1',
    role: 'assistant',
    content: 'Pre-check started',
    created_at: '2026-11-13T14:00:05.000Z',
    metadata: { pipeline: true },
    ...overrides,
  }
}

test('progress lines skip the user command and anything from before this click', () => {
  const run = applyDeployUpdate({ since, messages: [] }, [
    message({ message_id: 'old', created_at: '2026-11-13T13:00:00.000Z', content: 'old' }),
    message({ message_id: 'user', role: 'user', content: 'deploy BIG to production' }),
    message({ message_id: 'pre', content: '🔎 Pre-check' }),
  ])
  assert.deepEqual(
    deployProgressLines(run.messages).map((line) => line.text),
    ['🔎 Pre-check'],
  )
})

test('lines start at the command just sent, not an older deploy', () => {
  const run = applyDeployUpdate(
    { since, prompt: 'deploy BIG to production', messages: [] },
    [
      message({
        message_id: 'earlier',
        role: 'assistant',
        content: 'Previous deploy finished',
        created_at: '2026-11-13T14:00:02.000Z',
      }),
      message({
        message_id: 'cmd',
        role: 'user',
        content: 'deploy BIG to production',
        created_at: '2026-11-13T14:00:10.000Z',
      }),
      message({ message_id: 'pre', content: '🔎 Pre-check', created_at: '2026-11-13T14:00:11.000Z' }),
    ],
  )
  assert.deepEqual(
    deployProgressLines(run.messages).map((line) => line.text),
    ['🔎 Pre-check'],
  )
})

test('a yes/no question waits in the drawer once the send has finished', () => {
  const run = applyDeployUpdate(
    { since, messages: [] },
    [message({ content: 'Risk is **medium**. Reply **yes** to deploy anyway, or **no** to cancel.' })],
    { pendingSend: false },
  )
  assert.equal(run.phase, 'confirm')
})

test('the command that asked for confirmation is not treated as the reply', () => {
  const phase = deployRunPhase({
    pendingSend: false,
    messages: [
      message({
        message_id: 'cmd',
        role: 'user',
        content: 'deploy BIG to production',
        created_at: '2026-11-13T14:00:01.000Z',
      }),
      message({
        message_id: 'ask',
        content: 'Reply **yes** to deploy.',
        created_at: '2026-11-13T14:00:05.000Z',
      }),
    ],
  })
  assert.equal(phase, 'confirm')
})

test('a user reply after the confirmation stays on working', () => {
  const asked = applyDeployUpdate(
    { since, prompt: 'deploy BIG to production', messages: [] },
    [
      message({
        message_id: 'cmd',
        role: 'user',
        content: 'deploy BIG to production',
        created_at: '2026-11-13T14:00:01.000Z',
      }),
      message({
        message_id: 'ask',
        content: 'Reply **yes** to deploy.',
        created_at: '2026-11-13T14:00:05.000Z',
      }),
    ],
    { pendingSend: false },
  )
  assert.equal(asked.phase, 'confirm')
  const answered = applyDeployUpdate(
    asked,
    [
      message({
        message_id: 'yes',
        role: 'user',
        content: 'yes',
        created_at: '2026-11-13T14:00:06.000Z',
      }),
    ],
    { pendingSend: false },
  )
  assert.equal(answered.phase, 'working')
})

test('a user reply timestamp at or after the confirmation stays on working', () => {
  const phase = deployRunPhase({
    pendingSend: false,
    messages: [
      message({
        message_id: 'yes',
        role: 'user',
        content: 'yes',
        created_at: '2026-11-13T14:00:06.000Z',
      }),
      message({
        message_id: 'ask',
        content: 'Reply **yes** to deploy.',
        created_at: '2026-11-13T14:00:05.000Z',
      }),
    ],
  })
  assert.equal(phase, 'working')
})

test('progress line ids stay unique when the fallback key would collide', () => {
  const lines = deployProgressLines([
    message({ message_id: '', created_at: '2026-11-13T14:00:05.000Z', content: 'First' }),
    message({ message_id: '', created_at: '2026-11-13T14:00:05.000Z', content: 'Second' }),
  ])
  assert.equal(lines.length, 2)
  assert.notEqual(lines[0].id, lines[1].id)
})

test('the drawer stays on working while the send is still in flight', () => {
  const phase = deployRunPhase({
    pendingSend: true,
    messages: [message({ content: 'Reply **yes** to deploy.' })],
  })
  assert.equal(phase, 'working')
})

test('github actions progress keeps the drawer working', () => {
  const phase = deployRunPhase({
    messages: [message({ content: 'Still running', metadata: { status: 'in_progress' } })],
    deployPoll: true,
  })
  assert.equal(phase, 'working')
})

test('a finished reply ends the run', () => {
  const phase = deployRunPhase({
    messages: [message({ content: 'Deploy finished. Site is healthy.' })],
  })
  assert.equal(phase, 'done')
})

test('a later update replaces the same progress message', () => {
  const first = applyDeployUpdate({ since, messages: [] }, [
    message({ content: 'Starting', metadata: { status: 'in_progress' } }),
  ])
  const second = applyDeployUpdate(first, [
    message({ content: 'Starting', metadata: { status: 'complete' } }),
    message({ message_id: 'm2', content: 'Workflow succeeded', created_at: '2026-11-13T14:00:06.000Z' }),
  ])
  assert.deepEqual(deployProgressLines(second.messages).map((line) => line.status), ['complete', ''])
  assert.equal(second.phase, 'done')
})
