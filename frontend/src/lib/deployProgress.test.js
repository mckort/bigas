import assert from 'node:assert/strict'
import test from 'node:test'
import {
  applyDeployUpdate,
  commandForShipRow,
  deployProgressLines,
  deployRunPhase,
  emptyDeployRun,
  filterMessagesForProject,
  inferProjectKeyFromMessage,
  isTrackedShipRun,
  pipelineForProject,
  shipConfirmPrompt,
} from './deployProgress.js'

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

test('commandForShipRow prefers primary_prompt', () => {
  const cmd = commandForShipRow({
    primary_prompt: 'update staging GPW-PROD',
    commands: [
      { label: 'Prepare staging', prompt: 'prepare staging GPW-PROD' },
      { label: 'Update staging', prompt: 'update staging GPW-PROD' },
    ],
  })
  assert.equal(cmd.label, 'Update staging')
})

test('pipelineForProject matches the ship key', () => {
  const item = pipelineForProject(
    [
      { type: 'ticket', project_key: 'VFA' },
      { type: 'pipeline', project_key: 'vfa', thread_id: 't1', since: since },
    ],
    'VFA',
  )
  assert.equal(item.thread_id, 't1')
})

test('emptyDeployRun starts in working with deployPoll', () => {
  const run = emptyDeployRun({
    threadId: 't1',
    since,
    prompt: 'prepare deploy VFA 0.1.0',
    projectKey: 'vfa',
    token: 3,
  })
  assert.equal(run.phase, 'working')
  assert.equal(run.deployPoll, true)
  assert.equal(run.threadId, 't1')
  assert.equal(run.since, since)
  assert.equal(run.projectKey, 'VFA')
  assert.equal(run.token, 3)
})

test('isTrackedShipRun stays true while working or confirming for that ship', () => {
  const run = emptyDeployRun({ threadId: 't1', projectKey: 'AURA', token: 1 })
  assert.equal(isTrackedShipRun(run, 'aura'), true)
  assert.equal(isTrackedShipRun({ ...run, phase: 'confirm' }, 'AURA'), true)
  assert.equal(isTrackedShipRun({ ...run, phase: 'done' }, 'AURA'), false)
  assert.equal(isTrackedShipRun(run, 'VFA'), false)
})

test('emptyDeployRun keeps a blank since so a resume can load the whole thread', () => {
  const run = emptyDeployRun({ threadId: 't1', since: '', token: 4 })
  assert.equal(run.since, '')
  assert.equal(run.phase, 'working')
  assert.equal(run.error, '')
})

test('inferProjectKeyFromMessage reads metadata, deploy headers, and repos', () => {
  assert.equal(
    inferProjectKeyFromMessage(message({ metadata: { project_key: 'aura' } })),
    'AURA',
  )
  assert.equal(
    inferProjectKeyFromMessage(
      message({ content: '🚀 **Deploy BIG:** starting GitHub Actions…' }),
    ),
    'BIG',
  )
  assert.equal(
    inferProjectKeyFromMessage(
      message({ content: '🚀 **Deploy POF 0.4.0:** starting GitHub Actions…' }),
    ),
    'POF',
  )
  assert.equal(
    inferProjectKeyFromMessage(
      message({
        content: 'Compared tag -> **main on mckort/bigas**. 2 file(s) changed.',
      }),
    ),
    'BIG',
  )
  assert.equal(
    inferProjectKeyFromMessage(
      message({ role: 'user', content: 'prepare deploy AURA 0.7.0' }),
    ),
    'AURA',
  )
  assert.equal(
    inferProjectKeyFromMessage(
      message({
        content: '📦 **Prepare AuraLearn 0.7.0:** getting this cut onto main…',
        metadata: {},
      }),
    ),
    'AURA',
  )
  assert.equal(
    inferProjectKeyFromMessage(
      message({ content: '**Prepare VFA 0.1.0**', metadata: {} }),
    ),
    'VFA',
  )
  assert.equal(
    inferProjectKeyFromMessage(
      message({
        content: '**Prepare VC Field Assistant 0.3.0:** staging the cut',
        metadata: {},
      }),
    ),
    'VFA',
  )
})

test('filterMessagesForProject drops other projects in the shared thread', () => {
  const lines = filterMessagesForProject(
    [
      message({
        message_id: 'u1',
        role: 'user',
        content: 'prepare deploy AURA 0.7.0',
        created_at: '2026-11-13T14:00:01.000Z',
      }),
      message({
        message_id: 'a1',
        content: '📦 **Prepare AuraLearn 0.7.0:** getting this cut onto `main`',
        metadata: { pipeline: true, project_key: 'AURA' },
        created_at: '2026-11-13T14:00:02.000Z',
      }),
      message({
        message_id: 'b1',
        content: '🔎 Pre-check: comparing…',
        metadata: { pipeline: true, project_key: 'BIG' },
        created_at: '2026-11-13T14:00:03.000Z',
      }),
      message({
        message_id: 'b2',
        content: '🚀 **Deploy BIG:** starting GitHub Actions…',
        created_at: '2026-11-13T14:00:04.000Z',
      }),
      message({
        message_id: 'p1',
        content: '🚀 **Deploy POF 0.4.0:** starting GitHub Actions…',
        created_at: '2026-11-13T14:00:05.000Z',
      }),
      message({
        message_id: 'a2',
        content: 'AuraLearn risk check done.',
        metadata: { pipeline: true, project_key: 'AURA' },
        created_at: '2026-11-13T14:00:06.000Z',
      }),
    ],
    'AURA',
  )
  assert.deepEqual(
    lines.map((m) => m.message_id),
    ['u1', 'a1', 'a2'],
  )
})

test('filterMessagesForProject ignores leading messages with no project', () => {
  const lines = filterMessagesForProject(
    [
      message({
        message_id: 'lead',
        content: '⏳ Autofix agent running...',
        metadata: { pipeline: true, status: 'in_progress' },
      }),
      message({
        message_id: 'a',
        content: '📦 **Prepare AuraLearn 0.7.0:** getting this cut onto main',
        metadata: {},
      }),
      message({
        message_id: 'follow',
        content: 'Still working on the cut',
        metadata: { pipeline: true, status: 'in_progress' },
      }),
      message({
        message_id: 'other',
        content: '🚀 **Deploy BIG:** starting GitHub Actions…',
        metadata: {},
      }),
    ],
    'AURA',
  )
  assert.deepEqual(
    lines.map((m) => m.message_id),
    ['a', 'follow'],
  )
})

test('applyDeployUpdate scopes progress to the ship project key', () => {
  const run = applyDeployUpdate(
    {
      since,
      prompt: 'prepare deploy AURA 0.7.0',
      projectKey: 'AURA',
      messages: [],
    },
    [
      message({
        message_id: 'u1',
        role: 'user',
        content: 'prepare deploy AURA 0.7.0',
        created_at: '2026-11-13T14:00:01.000Z',
      }),
      message({
        message_id: 'a1',
        content: 'Prepare AuraLearn started',
        metadata: { pipeline: true, project_key: 'AURA' },
        created_at: '2026-11-13T14:00:02.000Z',
      }),
      message({
        message_id: 'b1',
        content: '🚀 **Deploy BIG:** starting GitHub Actions…',
        created_at: '2026-11-13T14:00:03.000Z',
      }),
    ],
  )
  assert.deepEqual(
    deployProgressLines(run.messages).map((line) => line.text),
    ['Prepare AuraLearn started'],
  )
})

test('shipConfirmPrompt names the project for the shared DevOps thread', () => {
  assert.equal(shipConfirmPrompt('aura', true), 'yes AURA')
  assert.equal(shipConfirmPrompt('VFA', false), 'no VFA')
  assert.equal(shipConfirmPrompt('', true), 'yes')
})
