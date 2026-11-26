import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { post, errorMessages, safeNextPath } from '../api/client';
import { useAuthStatus } from '../api/auth';
import { displayTime } from '../format';

/**
 * Sign in (design/screens/Login.dc.html): password → authenticator or
 * recovery code → done. The locked state is the 429 answer rendered
 * in place. "Stock Trading Agent" is the product name.
 *
 * Item 083: while the global pause lasts, /api/auth/status reports
 * `paused_until`; a known browser (a live device cookie) sees the amber
 * note and keeps the form, an unknown one sees only the pause and its
 * end time with the form disabled.
 */
export default function Login() {
  const [params] = useSearchParams();
  const next = safeNextPath(params.get('next'));
  const expired = params.get('expired') === '1';
  const status = useAuthStatus();
  const pausedUntil = status?.paused_until ?? null;
  const knownBrowser = status?.known_device ?? false;
  const pausedOut = pausedUntil !== null && !knownBrowser;

  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(false);
  const [step, setStep] = useState<"password" | "code">("password");
  const [code, setCode] = useState('');
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const card: React.CSSProperties = {
    width: 380,
    maxWidth: '100%',
    boxSizing: 'border-box',
    background: 'var(--sa-panel)',
    border: '1px solid var(--sa-border)',
    borderRadius: 'var(--sa-radius-lg)',
    padding: '24px 28px',
    display: 'flex',
    flexDirection: 'column',
    gap: 14,
    boxShadow: '0 20px 40px rgba(0,0,0,0.45)',
  };
  const input: React.CSSProperties = {
    width: '100%',
    height: 40,
    boxSizing: 'border-box',
    borderRadius: 'var(--sa-radius-md)',
    border: '1px solid var(--sa-border)',
    background: 'var(--sa-bg)',
    color: 'var(--sa-text)',
    padding: '0 10px',
    fontSize: 'var(--sa-text-base)',
  };
  const label: React.CSSProperties = {
    fontSize: 'var(--sa-text-md)',
    color: 'var(--sa-text-2)',
    display: 'block',
    marginBottom: 6,
  };
  const button: React.CSSProperties = {
    height: 40,
    borderRadius: 'var(--sa-radius-md)',
    border: 'none',
    background: 'var(--sa-accent)',
    color: '#0B0E13',
    fontWeight: 600,
    fontSize: 'var(--sa-text-base)',
    cursor: 'pointer',
  };
  const link: React.CSSProperties = {
    background: 'none',
    border: 'none',
    padding: 0,
    color: 'var(--sa-accent)',
    fontSize: 'var(--sa-text-md)',
    cursor: 'pointer',
    textAlign: 'left',
  };

  const submitPassword = async () => {
    setBusy(true);
    setError(null);
    try {
      await post('/api/auth/sign-in', {
        password,
        remember,
      });
      // The pending ticket arrives as an HttpOnly cookie (sa_pending) —
      // bound to this browser, single-use, ten minutes.
      setStep('code');
    } catch (e) {
      setError(errorMessages(e)[0]);
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async () => {
    setBusy(true);
    setError(null);
    try {
      await post('/api/auth/code', { code });
      window.location.assign(next);
    } catch (e) {
      setError(errorMessages(e)[0]);
      setBusy(false);
    }
  };

  const chrome = (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <div
        aria-hidden
        style={{
          width: 36,
          height: 36,
          borderRadius: 'var(--sa-radius-md)',
          background: 'var(--sa-accent)',
          color: '#0B0E13',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontWeight: 700,
          fontSize: 14,
        }}
      >
        SA
      </div>
      <div>
        <div style={{ fontWeight: 600, fontSize: 'var(--sa-text-lg)' }}>Stock Trading Agent</div>
        <div style={{ fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)' }}>
          {window.location.host} · PAPER
        </div>
      </div>
    </div>
  );

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 16,
        padding: 24,
      }}
    >
      {chrome}
      <div style={card}>
        {expired && (
          <div
            role="status"
            style={{
              padding: '10px 12px',
              borderRadius: 'var(--sa-radius-md)',
              background: 'var(--sa-raised)',
              border: '1px solid var(--sa-border)',
              color: 'var(--sa-text-2)',
              fontSize: 'var(--sa-text-md)',
              lineHeight: 1.5,
            }}
          >
            You were signed out after a while without activity. The agent kept running.
          </div>
        )}
        {pausedUntil !== null && knownBrowser && (
          <div
            role="status"
            style={{
              display: 'flex',
              gap: 10,
              padding: '12px 14px',
              border: '1px solid var(--sa-warning-border)',
              background: 'var(--sa-warning-bg)',
              borderRadius: 'var(--sa-radius-md)',
              color: 'var(--sa-text-2)',
              fontSize: 'var(--sa-text-md)',
              lineHeight: 1.5,
            }}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              style={{ flex: '0 0 16px', marginTop: 2 }}
            >
              <path d="M12 3l10 18H2z" />
              <line x1="12" y1="10" x2="12" y2="14" />
              <line x1="12" y1="18" x2="12" y2="18" />
            </svg>
            <span>
              Sign-in is paused for other browsers until{' '}
              <b style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 600 }}>
                {displayTime(pausedUntil)}
              </b>{' '}
              after many failed attempts. This browser is known, so you can sign in.
            </span>
          </div>
        )}
        {pausedOut && (
          <div
            role="alert"
            style={{
              display: 'flex',
              gap: 10,
              padding: '12px 14px',
              border: '1px solid var(--sa-negative-border)',
              background: 'var(--sa-negative-bg)',
              borderRadius: 'var(--sa-radius-md)',
              color: 'var(--sa-negative-soft)',
              fontSize: 'var(--sa-text-md)',
              lineHeight: 1.5,
            }}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
              style={{ flex: '0 0 16px', marginTop: 2 }}
            >
              <rect x="5" y="11" width="14" height="10" rx="2" />
              <path d="M8 11V8a4 4 0 0 1 8 0v3" />
            </svg>
            <span>
              Sign-in is paused until{' '}
              <b style={{ fontFamily: 'var(--sa-font-mono)', fontWeight: 600 }}>
                {displayTime(pausedUntil)}
              </b>{' '}
              after many failed attempts. You were notified by email.
            </span>
          </div>
        )}
        {step === "password" ? (
          <div style={pausedOut ? { opacity: 0.5 } : undefined}>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Sign in</h1>
            <div>
              <label htmlFor="pw" style={label}>
                Password
              </label>
              <input
                id="pw"
                type="password"
                autoComplete="current-password"
                disabled={pausedOut}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submitPassword()}
                style={input}
              />
            </div>
            <label
              style={{
                display: 'flex',
                gap: 10,
                alignItems: 'center',
                fontSize: 'var(--sa-text-md)',
                color: 'var(--sa-text-2)',
              }}
            >
              <input
                type="checkbox"
                disabled={pausedOut}
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              Keep me signed in on this device for 30 days
            </label>
            {error && (
              <p role="alert" style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-negative)' }}>
                {error}
              </p>
            )}
            <button
              type="button"
              style={pausedOut ? { ...button, background: 'var(--sa-raised)', color: 'var(--sa-text-muted)', cursor: 'default' } : button}
              onClick={submitPassword}
              disabled={busy || !password || pausedOut}
            >
              Continue
            </button>
            <p style={{ margin: 0, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
              One account. It is created on the server with{' '}
              <code>make create-user</code>; there is no sign-up page.
            </p>
          </div>
        ) : (
          <>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>Authenticator code</h1>
            <p style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-text-2)', lineHeight: 1.5 }}>
              {useRecovery
                ? 'Enter one of your recovery codes — each works once.'
                : 'Enter the 6-digit code from your authenticator app.'}
            </p>
            <div>
              <label htmlFor="otp" style={label}>
                {useRecovery ? 'Recovery code' : 'Code'}
              </label>
              <input
                id="otp"
                inputMode={useRecovery ? 'text' : 'numeric'}
                autoComplete="one-time-code"
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submitCode()}
                placeholder={useRecovery ? 'XXXX-XXXX-XXXX' : '000 000'}
                style={{ ...input, fontFamily: 'var(--sa-mono)' }}
              />
            </div>
            {error && (
              <p role="alert" style={{ margin: 0, fontSize: 'var(--sa-text-md)', color: 'var(--sa-negative)' }}>
                {error}
              </p>
            )}
            <button type="button" style={button} onClick={submitCode} disabled={busy || !code}>
              Sign in
            </button>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <button type="button" style={link} onClick={() => setUseRecovery(!useRecovery)}>
                {useRecovery ? 'Use the authenticator code' : 'Use a recovery code'}
              </button>
              <button
                type="button"
                style={link}
                onClick={() => {
                  setStep('password');
                  setCode('');
                  setError(null);
                }}
              >
                Back
              </button>
            </div>
          </>
        )}
        {pausedUntil !== null && knownBrowser && (
          <p style={{ margin: 0, fontSize: 'var(--sa-text-sm)', color: 'var(--sa-text-muted)', lineHeight: 1.5 }}>
            An unknown browser sees only the pause and its end time. The password, the code and the limit per address still apply here.
          </p>
        )}
      </div>
    </div>
  );
}
