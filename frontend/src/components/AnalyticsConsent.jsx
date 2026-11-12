import { useState } from 'react'
import {
  analyticsConsentPending,
  denyAnalyticsConsent,
  grantAnalyticsConsent,
} from '../lib/productAnalytics'

/** Shown until the visitor accepts or declines Google Analytics. */
export default function AnalyticsConsent({ aboveNav = false }) {
  const [visible, setVisible] = useState(() => analyticsConsentPending())
  if (!visible) return null

  return (
    <div
      role="dialog"
      aria-label="Cookie consent"
      className={aboveNav ? 'analytics-consent analytics-consent-above-nav' : 'analytics-consent'}
      style={{
        position: 'fixed',
        left: 16,
        right: 16,
        zIndex: 80,
        display: 'flex',
        flexWrap: 'wrap',
        gap: 12,
        alignItems: 'center',
        justifyContent: 'space-between',
        maxWidth: 720,
        margin: '0 auto',
        padding: '14px 16px',
        background: '#14161c',
        color: '#f6f5f1',
        borderRadius: 12,
        boxShadow: '0 8px 30px rgba(0,0,0,.25)',
        fontSize: 14,
        lineHeight: 1.45,
      }}
    >
      <p style={{ margin: 0, flex: '1 1 220px' }}>
        We use Google Analytics to understand how the site is used. Analytics cookies load only if you accept.
      </p>
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          type="button"
          onClick={() => {
            denyAnalyticsConsent()
            setVisible(false)
          }}
          style={{
            minHeight: 40,
            padding: '0 14px',
            borderRadius: 8,
            fontWeight: 600,
            cursor: 'pointer',
            background: 'transparent',
            color: '#f6f5f1',
            border: '1px solid rgba(255,255,255,.35)',
          }}
        >
          Decline
        </button>
        <button
          type="button"
          onClick={() => {
            grantAnalyticsConsent()
            setVisible(false)
          }}
          style={{
            minHeight: 40,
            padding: '0 14px',
            borderRadius: 8,
            fontWeight: 600,
            cursor: 'pointer',
            background: '#f6f5f1',
            color: '#14161c',
            border: 0,
          }}
        >
          Accept analytics
        </button>
      </div>
    </div>
  )
}
