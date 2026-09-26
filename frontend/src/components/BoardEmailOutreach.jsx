import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchBoardEmailDraft,
  fetchBoardEmailSettings,
  fetchBoardRecipients,
  fetchOutboundEmailEnabled,
  generateBoardEmailDraft,
  pollBoardCampaign,
  previewBoardCampaign,
  saveBoardEmailDraft,
  saveBoardEmailSettings,
  sendBoardCampaign,
  testBoardEmailSettings,
  uploadBoardRecipients,
} from '../lib/api'

const TONES = [
  { value: 'professional', label: 'Professional' },
  { value: 'friendly', label: 'Friendly' },
  { value: 'concise', label: 'Concise' },
]

function emptySettings() {
  return {
    smtp_host: 'smtp.gmail.com',
    smtp_port: 587,
    security: 'starttls',
    username: '',
    password: '',
    sender_name: '',
    sender_email: '',
    reply_to: '',
  }
}

function formatSendProgress(campaign) {
  const counts = campaign?.counts || {}
  const sent = Number(counts.sent) || 0
  const failed = Number(counts.failed) || 0
  const total = Number(counts.total) || 0
  const status = campaign?.status
  const stillSending = !status || status === 'pending' || status === 'in_progress'
  if (stillSending) {
    const failedNote = failed ? ` (${failed} failed)` : ''
    return {
      phase: 'sending',
      sent,
      failed,
      total,
      text: total ? `Sending ${sent} of ${total}${failedNote}` : 'Sending…',
    }
  }
  if (!total) {
    return { phase: 'done', sent, failed, total, text: 'Sending done.' }
  }
  if (failed > 0) {
    return {
      phase: 'done',
      sent,
      failed,
      total,
      text: `Sending done. Sent to ${sent} of ${total}. ${failed} could not be sent.`,
    }
  }
  return {
    phase: 'done',
    sent,
    failed,
    total,
    text: `Sending done. Sent to ${sent} of ${total}.`,
  }
}

function formatUploadMessage(res) {
  const added = `Added ${res.added} subscribers`
  const invalid = res.invalid_rows || []
  if (!invalid.length) return added
  const reasons = [...new Set(invalid.map((row) => row.error).filter(Boolean))]
  if (invalid.length === 1 && invalid[0].row === 0 && reasons.length === 1) {
    return `${added} (${reasons[0]})`
  }
  const reasonText = reasons.length ? `: ${reasons.join('; ')}` : ''
  return `${added} (${invalid.length} invalid rows skipped${reasonText})`
}

export default function BoardEmailOutreach({ boards }) {
  const [enabled, setEnabled] = useState(false)
  const [boardId, setBoardId] = useState('')
  const [settings, setSettings] = useState(emptySettings())
  const [settingsMsg, setSettingsMsg] = useState('')
  const [testing, setTesting] = useState(false)
  const [recipients, setRecipients] = useState([])
  const [selected, setSelected] = useState(() => new Set())
  const [search, setSearch] = useState('')
  const [purpose, setPurpose] = useState('')
  const [tone, setTone] = useState('professional')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [preview, setPreview] = useState(null)
  const [uploadMsg, setUploadMsg] = useState('')
  const [sendMsg, setSendMsg] = useState('')
  const [generating, setGenerating] = useState(false)
  const [sending, setSending] = useState(false)
  const [sendProgress, setSendProgress] = useState(null)

  useEffect(() => {
    fetchOutboundEmailEnabled()
      .then((res) => setEnabled(Boolean(res.enabled)))
      .catch(() => setEnabled(false))
  }, [])

  const loadBoardData = useCallback(async (id) => {
    if (!id) return
    const [settingsRes, draftRes, recipientsRes] = await Promise.all([
      fetchBoardEmailSettings(id),
      fetchBoardEmailDraft(id),
      fetchBoardRecipients(id),
    ])
    const s = settingsRes.settings || {}
    setSettings({
      ...emptySettings(),
      smtp_host: s.smtp_host || 'smtp.gmail.com',
      smtp_port: s.smtp_port || 587,
      security: s.security || 'starttls',
      username: s.username || '',
      password: '',
      sender_name: s.sender_name || '',
      sender_email: s.sender_email || '',
      reply_to: s.reply_to || '',
    })
    const draft = draftRes.draft
    setPurpose(draft?.purpose || '')
    setTone(draft?.tone || 'professional')
    setSubject(draft?.subject || '')
    setBody(draft?.body || '')
    setRecipients(recipientsRes.recipients || [])
    setSelected(new Set())
    setSearch('')
    setPreview(null)
    setUploadMsg('')
    setSendMsg('')
    setSettingsMsg('')
  }, [])

  useEffect(() => {
    if (!boardId && boards?.length) {
      setBoardId(boards[0].board_id)
    }
  }, [boards, boardId])

  useEffect(() => {
    if (boardId && enabled) {
      loadBoardData(boardId).catch((err) => setSettingsMsg(err.message))
    }
  }, [boardId, enabled, loadBoardData])

  const visibleRecipients = useMemo(() => {
    const query = search.trim().toLowerCase()
    const rows = [...recipients].sort((a, b) => {
      const name = (a.first_name || '').localeCompare(b.first_name || '')
      if (name !== 0) return name
      return (a.email || '').localeCompare(b.email || '')
    })
    if (!query) return rows
    return rows.filter((row) => {
      const name = (row.first_name || '').toLowerCase()
      const email = (row.email || '').toLowerCase()
      return name.includes(query) || email.includes(query)
    })
  }, [recipients, search])

  const allVisibleSelected =
    visibleRecipients.length > 0 &&
    visibleRecipients.every((row) => selected.has(row.recipient_id))

  if (!enabled) {
    return (
      <p className="text-sm text-muted">
        Outbound email is disabled. Set <code className="text-xs">ENABLE_OUTBOUND_EMAIL=true</code> on
        the server to configure SMTP outreach per board.
      </p>
    )
  }

  if (!boards?.length) {
    return <p className="text-sm text-muted">Create a board to configure outbound email.</p>
  }

  function toggleRecipient(id) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAllVisible() {
    setSelected((prev) => {
      const next = new Set(prev)
      if (allVisibleSelected) {
        visibleRecipients.forEach((row) => next.delete(row.recipient_id))
      } else {
        visibleRecipients.forEach((row) => next.add(row.recipient_id))
      }
      return next
    })
  }

  async function handleSaveSettings(e) {
    e.preventDefault()
    setSettingsMsg('')
    try {
      await saveBoardEmailSettings(boardId, settings)
      setSettingsMsg('Sending account saved.')
    } catch (err) {
      setSettingsMsg(err.message)
    }
  }

  async function handleTestConnection() {
    setTesting(true)
    setSettingsMsg('')
    try {
      const res = await testBoardEmailSettings(boardId)
      setSettingsMsg(res.message || 'Connection OK')
    } catch (err) {
      setSettingsMsg(err.message)
    } finally {
      setTesting(false)
    }
  }

  async function handleUpload(file) {
    if (!file) return
    setUploadMsg('')
    try {
      const res = await uploadBoardRecipients(boardId, file, true)
      setRecipients(res.recipients || [])
      setSelected(new Set())
      setSearch('')
      setUploadMsg(formatUploadMessage(res))
    } catch (err) {
      setUploadMsg(err.message)
    }
  }

  async function handleGenerate() {
    const text = purpose.trim()
    if (!text) {
      setSendMsg('Describe the email purpose first.')
      return
    }
    setGenerating(true)
    setSendMsg('')
    setPreview(null)
    try {
      const res = await generateBoardEmailDraft(boardId, { purpose: text, tone })
      const draft = res.draft || {}
      setSubject(draft.subject || '')
      setBody(draft.body || '')
      setPurpose(draft.purpose || text)
      setTone(draft.tone || tone)
      setSendMsg('Draft ready. Edit it before sending.')
    } catch (err) {
      setSendMsg(err.message)
    } finally {
      setGenerating(false)
    }
  }

  async function handleSaveDraft() {
    setSendMsg('')
    try {
      await saveBoardEmailDraft(boardId, { subject, body, purpose, tone })
      setSendMsg('Draft saved.')
    } catch (err) {
      setSendMsg(err.message)
    }
  }

  async function handlePreview() {
    setSendMsg('')
    try {
      const sampleId = selected.size ? [...selected][0] : visibleRecipients[0]?.recipient_id
      const res = await previewBoardCampaign(boardId, {
        subject,
        body,
        recipient_id: sampleId,
      })
      setPreview(res.preview)
    } catch (err) {
      setSendMsg(err.message)
    }
  }

  async function handleSend() {
    if (!selected.size) {
      setSendMsg('Select at least one subscriber.')
      return
    }
    setSending(true)
    setSendMsg('')
    setSendProgress(null)
    try {
      const res = await sendBoardCampaign(boardId, {
        subject,
        body,
        recipient_ids: [...selected],
      })
      const campaignId = res.campaign?.campaign_id
      if (res.campaign) setSendProgress(formatSendProgress(res.campaign))
      if (campaignId) {
        const final = await pollBoardCampaign(boardId, campaignId, {
          onUpdate: (update) => {
            if (update.campaign) setSendProgress(formatSendProgress(update.campaign))
          },
        })
        const progress = formatSendProgress(final.campaign)
        if (progress.phase === 'sending') {
          setSendProgress({
            ...progress,
            text: progress.total
              ? `Sending is still going. Sent ${progress.sent} of ${progress.total} so far.`
              : 'Sending is still going.',
          })
        } else {
          setSendProgress(progress)
        }
      }
    } catch (err) {
      setSendMsg(err.message)
    } finally {
      setSending(false)
    }
  }

  const selectedLabel = selected.size === 1 ? '1 subscriber' : `${selected.size} subscribers`

  return (
    <div className="space-y-6">
      <div>
        <label className="text-sm text-muted font-medium" htmlFor="outreach-board">
          Board
        </label>
        <select
          id="outreach-board"
          value={boardId}
          onChange={(e) => setBoardId(e.target.value)}
          className="w-full mt-1.5 input-field"
        >
          {boards.map((b) => (
            <option key={b.board_id} value={b.board_id}>
              {b.name}
            </option>
          ))}
        </select>
      </div>

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h4 className="text-sm font-medium">Subscribers</h4>
          <p className="text-[11px] text-muted">
            {recipients.length} total
            {selected.size ? ` · ${selected.size} selected` : ''}
          </p>
        </div>
        <p className="text-[11px] text-muted">
          CSV columns: first_name, email. Comma or semicolon; a header row is optional. Uploading replaces the list.
        </p>
        <input
          type="file"
          accept=".csv,text/csv"
          className="text-sm w-full"
          onChange={(e) => handleUpload(e.target.files?.[0])}
        />
        {uploadMsg && <p className="text-sm text-muted">{uploadMsg}</p>}
        {recipients.length > 0 && (
          <>
            <input
              className="input-field w-full"
              placeholder="Search name or email"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search subscribers"
            />
            <div className="border border-border rounded-lg overflow-x-auto max-h-64 overflow-y-auto">
              <table className="w-full text-sm min-w-[280px]">
                <thead className="bg-surface sticky top-0">
                  <tr>
                    <th className="p-2 text-left w-10">
                      <input
                        type="checkbox"
                        checked={allVisibleSelected}
                        onChange={toggleAllVisible}
                        aria-label="Select visible subscribers"
                        className="min-w-[20px] min-h-[20px]"
                      />
                    </th>
                    <th className="p-2 text-left">Name</th>
                    <th className="p-2 text-left">Email</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRecipients.map((r) => (
                    <tr key={r.recipient_id} className="border-t border-border">
                      <td className="p-2">
                        <input
                          type="checkbox"
                          checked={selected.has(r.recipient_id)}
                          onChange={() => toggleRecipient(r.recipient_id)}
                          aria-label={`Select ${r.email}`}
                          className="min-w-[20px] min-h-[20px]"
                        />
                      </td>
                      <td className="p-2">{r.first_name}</td>
                      <td className="p-2 break-all">{r.email}</td>
                    </tr>
                  ))}
                  {visibleRecipients.length === 0 && (
                    <tr>
                      <td colSpan={3} className="p-3 text-sm text-muted">
                        No subscribers match that search.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="space-y-3 border border-border rounded-lg p-3">
        <h4 className="text-sm font-medium">Email</h4>
        <label className="block text-[11px] text-muted" htmlFor="outreach-purpose">
          What is this email for?
        </label>
        <textarea
          id="outreach-purpose"
          className="input-field w-full resize-y min-h-[88px]"
          placeholder="Invite founders to next month’s portfolio update and ask them to reply with one metric."
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          rows={3}
        />
        <label className="block text-[11px] text-muted" htmlFor="outreach-tone">
          Tone
        </label>
        <select
          id="outreach-tone"
          className="input-field"
          value={tone}
          onChange={(e) => setTone(e.target.value)}
        >
          {TONES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={generating || !purpose.trim()}
          onClick={handleGenerate}
          className="btn-primary px-4 py-2.5 min-h-[44px] rounded-lg disabled:opacity-50"
        >
          {generating ? 'Generating…' : 'Generate draft'}
        </button>
        <p className="text-[11px] text-muted">
          The draft uses {'{{first_name}}'}. Edit the subject and body before sending.
        </p>
        <input
          className="input-field w-full"
          placeholder="Subject"
          aria-label="Email subject"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
        <textarea
          className="input-field w-full resize-y min-h-[140px]"
          placeholder="Body"
          aria-label="Email body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={8}
        />
        {preview && (
          <div className="text-sm bg-surface border border-border rounded-lg p-3 whitespace-pre-wrap break-words">
            <p className="text-[11px] text-muted mb-1">Preview with the name filled in</p>
            <p className="font-medium mb-1">{preview.subject}</p>
            {preview.body}
          </div>
        )}
        {sendProgress && (
          <div
            role="status"
            aria-live="polite"
            className={`rounded-lg border px-3 py-2 ${
              sendProgress.phase === 'done' ? 'border-accent/40 bg-accent-muted' : 'border-border bg-surface'
            }`}
          >
            <p className={`text-sm ${sendProgress.phase === 'done' ? 'font-medium text-text' : 'text-muted'}`}>
              {sendProgress.text}
            </p>
            {sendProgress.total > 0 && (
              <div className="mt-2 h-1.5 rounded-full bg-border overflow-hidden" aria-hidden="true">
                <div
                  className="h-full bg-accent transition-[width] duration-300"
                  style={{
                    width: `${Math.min(
                      100,
                      Math.round(((sendProgress.sent + sendProgress.failed) / sendProgress.total) * 100),
                    )}%`,
                  }}
                />
              </div>
            )}
          </div>
        )}
        {sendMsg && <p className="text-sm text-muted">{sendMsg}</p>}
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={handleSaveDraft} className="btn-secondary px-4 py-2.5 min-h-[44px] rounded-lg">
            Save draft
          </button>
          <button type="button" onClick={handlePreview} className="btn-secondary px-4 py-2.5 min-h-[44px] rounded-lg">
            Preview
          </button>
          <button
            type="button"
            disabled={sending || !selected.size}
            onClick={handleSend}
            className="btn-primary px-4 py-2.5 min-h-[44px] rounded-lg disabled:opacity-50"
          >
            {sending
              ? sendProgress?.total
                ? `Sending ${sendProgress.sent} of ${sendProgress.total}`
                : 'Sending…'
              : selected.size
                ? `Send to ${selectedLabel}`
                : 'Send'}
          </button>
        </div>
      </section>

      <details className="border border-border rounded-lg p-3">
        <summary className="text-sm font-medium cursor-pointer">Sending account</summary>
        <form onSubmit={handleSaveSettings} className="space-y-3 mt-3">
          <p className="text-[11px] text-muted leading-relaxed">
            Gmail / Google Workspace: use smtp.gmail.com, port 587, STARTTLS, and a 16-character App
            Password (requires 2FA on the Google account).
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input
              className="input-field"
              placeholder="SMTP host"
              aria-label="SMTP host"
              value={settings.smtp_host}
              onChange={(e) => setSettings({ ...settings, smtp_host: e.target.value })}
            />
            <input
              className="input-field"
              type="number"
              placeholder="Port"
              aria-label="SMTP port"
              value={settings.smtp_port}
              onChange={(e) => setSettings({ ...settings, smtp_port: Number(e.target.value) })}
            />
            <select
              className="input-field"
              aria-label="SMTP security"
              value={settings.security}
              onChange={(e) => setSettings({ ...settings, security: e.target.value })}
            >
              <option value="starttls">STARTTLS (587)</option>
              <option value="ssl">SSL (465)</option>
            </select>
            <input
              className="input-field"
              placeholder="Username"
              aria-label="SMTP username"
              value={settings.username}
              onChange={(e) => setSettings({ ...settings, username: e.target.value })}
            />
            <input
              className="input-field sm:col-span-2"
              type="password"
              placeholder="Password / App password"
              aria-label="SMTP password"
              value={settings.password}
              onChange={(e) => setSettings({ ...settings, password: e.target.value })}
            />
            <input
              className="input-field"
              placeholder="Sender name"
              aria-label="Sender name"
              value={settings.sender_name}
              onChange={(e) => setSettings({ ...settings, sender_name: e.target.value })}
            />
            <input
              className="input-field"
              placeholder="Sender email"
              aria-label="Sender email"
              value={settings.sender_email}
              onChange={(e) => setSettings({ ...settings, sender_email: e.target.value })}
            />
          </div>
          {settingsMsg && <p className="text-sm text-muted">{settingsMsg}</p>}
          <div className="flex flex-wrap gap-2">
            <button type="submit" className="btn-primary px-4 py-2.5 min-h-[44px] rounded-lg">
              Save settings
            </button>
            <button
              type="button"
              disabled={testing}
              onClick={handleTestConnection}
              className="btn-secondary px-4 py-2.5 min-h-[44px] rounded-lg disabled:opacity-50"
            >
              {testing ? 'Testing…' : 'Test connection'}
            </button>
          </div>
        </form>
      </details>
    </div>
  )
}
