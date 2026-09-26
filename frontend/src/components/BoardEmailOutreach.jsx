import { useCallback, useEffect, useState } from 'react'
import {
  fetchBoardEmailDraft,
  fetchBoardEmailSettings,
  fetchBoardRecipients,
  fetchOutboundEmailEnabled,
  pollBoardCampaign,
  previewBoardCampaign,
  saveBoardEmailDraft,
  saveBoardEmailSettings,
  sendBoardCampaign,
  testBoardEmailSettings,
  uploadBoardRecipients,
} from '../lib/api'

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

export default function BoardEmailOutreach({ boards }) {
  const [enabled, setEnabled] = useState(false)
  const [boardId, setBoardId] = useState('')
  const [settings, setSettings] = useState(emptySettings())
  const [settingsMsg, setSettingsMsg] = useState('')
  const [testing, setTesting] = useState(false)
  const [recipients, setRecipients] = useState([])
  const [selected, setSelected] = useState(() => new Set())
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [preview, setPreview] = useState(null)
  const [uploadMsg, setUploadMsg] = useState('')
  const [sendMsg, setSendMsg] = useState('')
  const [sending, setSending] = useState(false)

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
    if (draft) {
      setSubject(draft.subject || '')
      setBody(draft.body || '')
    }
    setRecipients(recipientsRes.recipients || [])
    setSelected(new Set())
    setPreview(null)
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

  function toggleAll() {
    if (selected.size === recipients.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(recipients.map((r) => r.recipient_id)))
    }
  }

  async function handleSaveSettings(e) {
    e.preventDefault()
    setSettingsMsg('')
    try {
      await saveBoardEmailSettings(boardId, settings)
      setSettingsMsg('Email settings saved.')
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
      setUploadMsg(
        `Added ${res.added} contacts` +
          (res.invalid_rows?.length ? ` (${res.invalid_rows.length} invalid rows skipped)` : ''),
      )
    } catch (err) {
      setUploadMsg(err.message)
    }
  }

  async function handleSaveDraft() {
    setSendMsg('')
    try {
      await saveBoardEmailDraft(boardId, { subject, body })
      setSendMsg('Draft saved.')
    } catch (err) {
      setSendMsg(err.message)
    }
  }

  async function handlePreview() {
    setSendMsg('')
    try {
      const sampleId = selected.size ? [...selected][0] : recipients[0]?.recipient_id
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
      setSendMsg('Select at least one recipient.')
      return
    }
    setSending(true)
    setSendMsg('')
    try {
      const res = await sendBoardCampaign(boardId, {
        subject,
        body,
        recipient_ids: [...selected],
      })
      const campaignId = res.campaign?.campaign_id
      setSendMsg('Sending started…')
      if (campaignId) {
        const final = await pollBoardCampaign(boardId, campaignId)
        const counts = final.campaign?.counts
        setSendMsg(
          counts
            ? `Done: ${counts.sent} sent, ${counts.failed} failed, ${counts.pending} pending.`
            : 'Campaign finished.',
        )
      }
    } catch (err) {
      setSendMsg(err.message)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <label className="text-sm text-muted font-medium">Board</label>
        <select
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

      <form onSubmit={handleSaveSettings} className="space-y-3 border border-border rounded-lg p-3">
        <h4 className="text-sm font-medium">Outbound SMTP</h4>
        <p className="text-[11px] text-muted leading-relaxed">
          Gmail / Google Workspace: use smtp.gmail.com, port 587, STARTTLS, and a 16-character App
          Password (requires 2FA on the Google account).
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <input
            className="input-field"
            placeholder="SMTP host"
            value={settings.smtp_host}
            onChange={(e) => setSettings({ ...settings, smtp_host: e.target.value })}
          />
          <input
            className="input-field"
            type="number"
            placeholder="Port"
            value={settings.smtp_port}
            onChange={(e) => setSettings({ ...settings, smtp_port: Number(e.target.value) })}
          />
          <select
            className="input-field"
            value={settings.security}
            onChange={(e) => setSettings({ ...settings, security: e.target.value })}
          >
            <option value="starttls">STARTTLS (587)</option>
            <option value="ssl">SSL (465)</option>
          </select>
          <input
            className="input-field"
            placeholder="Username"
            value={settings.username}
            onChange={(e) => setSettings({ ...settings, username: e.target.value })}
          />
          <input
            className="input-field sm:col-span-2"
            type="password"
            placeholder="Password / App password"
            value={settings.password}
            onChange={(e) => setSettings({ ...settings, password: e.target.value })}
          />
          <input
            className="input-field"
            placeholder="Sender name"
            value={settings.sender_name}
            onChange={(e) => setSettings({ ...settings, sender_name: e.target.value })}
          />
          <input
            className="input-field"
            placeholder="Sender email"
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

      <section className="space-y-2">
        <h4 className="text-sm font-medium">Recipients (CSV)</h4>
        <p className="text-[11px] text-muted">Required columns: first_name, email</p>
        <input
          type="file"
          accept=".csv,text/csv"
          className="text-sm w-full"
          onChange={(e) => handleUpload(e.target.files?.[0])}
        />
        {uploadMsg && <p className="text-sm text-muted">{uploadMsg}</p>}
        {recipients.length > 0 && (
          <div className="border border-border rounded-lg overflow-x-auto max-h-48 overflow-y-auto">
            <table className="w-full text-sm min-w-[280px]">
              <thead className="bg-surface sticky top-0">
                <tr>
                  <th className="p-2 text-left w-10">
                    <input
                      type="checkbox"
                      checked={selected.size === recipients.length && recipients.length > 0}
                      onChange={toggleAll}
                      aria-label="Select all recipients"
                      className="min-w-[20px] min-h-[20px]"
                    />
                  </th>
                  <th className="p-2 text-left">First name</th>
                  <th className="p-2 text-left">Email</th>
                </tr>
              </thead>
              <tbody>
                {recipients.map((r) => (
                  <tr key={r.recipient_id} className="border-t border-border">
                    <td className="p-2">
                      <input
                        type="checkbox"
                        checked={selected.has(r.recipient_id)}
                        onChange={() => toggleRecipient(r.recipient_id)}
                        className="min-w-[20px] min-h-[20px]"
                      />
                    </td>
                    <td className="p-2">{r.first_name}</td>
                    <td className="p-2 break-all">{r.email}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="space-y-2">
        <h4 className="text-sm font-medium">Email draft</h4>
        <p className="text-[11px] text-muted">Use {'{{first_name}}'} in subject and body.</p>
        <input
          className="input-field w-full"
          placeholder="Subject"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
        />
        <textarea
          className="input-field w-full resize-y min-h-[120px]"
          placeholder="Body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={6}
        />
        {preview && (
          <div className="text-sm bg-surface border border-border rounded-lg p-3 whitespace-pre-wrap break-words">
            <p className="font-medium mb-1">{preview.subject}</p>
            {preview.body}
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
            disabled={sending}
            onClick={handleSend}
            className="btn-primary px-4 py-2.5 min-h-[44px] rounded-lg disabled:opacity-50"
          >
            {sending ? 'Sending…' : 'Send to selected'}
          </button>
        </div>
      </section>
    </div>
  )
}
